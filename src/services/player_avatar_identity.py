"""Identify the active player from the main-city avatar crop."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from config.paths import repo_root
from layout.template_match import (
    _hybrid_scores_at_patch,
    patch_bgr_from_bbox_percent,
)

# Deliberately a stable, slightly generous top-left crop that covers the chief
# avatar on all current main-city variants. References are captured with the
# same bbox, so matching stays 1:1 even if the crop includes a little HUD chrome.
MAIN_CITY_AVATAR_BBOX: dict[str, float] = {
    "x": 0.0,
    "y": 0.0,
    "width": 13.5,
    "height": 7.65,
    "rotation": 0.0,
    "original_width": 720,
    "original_height": 1280,
}

MIN_AVATAR_MATCH_SCORE = 0.86
MIN_AVATAR_MATCH_MARGIN = 0.04
# When the device maps to a single player there is no runner-up to form a
# margin, so the margin gate can't veto a weak match. A fresh account still in
# onboarding (default/unset avatar) can graze the floor against the device's
# stale mapped player and bind to the wrong identity — which then disables the
# onboarding overlay rules (they gate on ``active_player == ""``) and runs the
# wrong account's tasks. Demand a strong absolute score in the sole-candidate
# case so a default avatar can't squeak past.
MIN_AVATAR_MATCH_SCORE_SOLE_CANDIDATE = 0.92


@dataclass(frozen=True)
class AvatarReferenceMeta:
    player_id: str
    path: Path
    rel_path: str
    exists: bool
    mtime: float | None = None


@dataclass(frozen=True)
class AvatarMatch:
    player_id: str
    score: float
    phash_score: float
    color_score: float
    edge_score: float
    ncc_score: float
    hash_distance: int
    margin: float
    reference_path: Path

    def as_dict(self) -> dict[str, Any]:
        try:
            reference = self.reference_path.relative_to(repo_root()).as_posix()
        except ValueError:
            reference = self.reference_path.as_posix()
        return {
            "player_id": self.player_id,
            "score": self.score,
            "phash_score": self.phash_score,
            "color_score": self.color_score,
            "edge_score": self.edge_score,
            "ncc_score": self.ncc_score,
            "hash_distance": self.hash_distance,
            "margin": self.margin,
            "reference": reference,
        }


def avatar_reference_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "db" / "state" / "player_avatars"


def avatar_reference_path(player_id: str, root: Path | None = None) -> Path:
    pid = _clean_player_id(player_id)
    if not pid:
        msg = "player_id is required"
        raise ValueError(msg)
    return avatar_reference_dir(root) / f"{pid}.png"


def avatar_reference_meta(player_id: str, root: Path | None = None) -> AvatarReferenceMeta:
    path = avatar_reference_path(player_id, root)
    exists = path.is_file()
    return AvatarReferenceMeta(
        player_id=_clean_player_id(player_id),
        path=path,
        rel_path=path.relative_to(root or repo_root()).as_posix(),
        exists=exists,
        mtime=path.stat().st_mtime if exists else None,
    )


def list_avatar_references(
    player_ids: list[str] | tuple[str, ...] | None = None,
    *,
    root: Path | None = None,
) -> list[Path]:
    base = avatar_reference_dir(root)
    if player_ids:
        out: list[Path] = []
        for pid in player_ids:
            clean = _clean_player_id(pid)
            if not clean:
                continue
            p = base / f"{clean}.png"
            if p.is_file():
                out.append(p)
        return out
    if not base.is_dir():
        return []
    return sorted(base.glob("*.png"), key=lambda p: p.stem)


def save_avatar_reference_from_frame(
    player_id: str,
    image_bgr: np.ndarray,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    pid = _clean_player_id(player_id)
    if not pid:
        msg = "player_id is required"
        raise ValueError(msg)
    patch, _ = patch_bgr_from_bbox_percent(image_bgr, MAIN_CITY_AVATAR_BBOX)
    if patch.size == 0:
        msg = "avatar crop is empty"
        raise ValueError(msg)
    path = avatar_reference_path(pid, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{pid}-", suffix=".png", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        if not cv2.imwrite(str(tmp), patch):
            msg = f"cv2.imwrite failed for {tmp}"
            raise RuntimeError(msg)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return {
        "ok": True,
        "player_id": pid,
        "reference": path.relative_to(root or repo_root()).as_posix(),
        "width": int(patch.shape[1]),
        "height": int(patch.shape[0]),
        "mtime": path.stat().st_mtime,
    }


def match_player_avatar(
    image_bgr: np.ndarray,
    *,
    candidate_player_ids: list[str] | tuple[str, ...] | None = None,
    root: Path | None = None,
    min_score: float = MIN_AVATAR_MATCH_SCORE,
    min_margin: float = MIN_AVATAR_MATCH_MARGIN,
    min_score_sole_candidate: float = MIN_AVATAR_MATCH_SCORE_SOLE_CANDIDATE,
) -> AvatarMatch | None:
    refs = list_avatar_references(candidate_player_ids, root=root)
    if not refs:
        return None
    patch, _ = patch_bgr_from_bbox_percent(image_bgr, MAIN_CITY_AVATAR_BBOX)
    if patch.size == 0:
        return None

    scored: list[AvatarMatch] = []
    for ref in refs:
        template = cv2.imread(str(ref), cv2.IMREAD_COLOR)
        if template is None or template.size == 0:
            continue
        if template.shape != patch.shape:
            template = cv2.resize(
                template,
                (int(patch.shape[1]), int(patch.shape[0])),
                interpolation=cv2.INTER_LINEAR,
            )
        phash, hamming, ncc, color, edge = _hybrid_scores_at_patch(patch, template)
        # Conservative rank: pHash catches structure, color catches wrong avatar
        # palettes, edge catches HUD/chrome drift. A low sub-score should veto.
        score = min(float(phash), float(color), float(edge))
        scored.append(
            AvatarMatch(
                player_id=ref.stem,
                score=score,
                phash_score=float(phash),
                color_score=float(color),
                edge_score=float(edge),
                ncc_score=float(ncc),
                hash_distance=int(hamming),
                margin=0.0,
                reference_path=ref,
            )
        )
    if not scored:
        return None
    scored.sort(key=lambda m: m.score, reverse=True)
    best = scored[0]
    second_score = scored[1].score if len(scored) > 1 else 0.0
    margin = best.score - second_score
    if best.score < min_score:
        return None
    if len(scored) > 1:
        if margin < min_margin:
            return None
    elif best.score < min_score_sole_candidate:
        # Sole candidate (single device-mapped player): no margin signal, so
        # require a strong absolute match to avoid binding a default avatar.
        return None
    return AvatarMatch(
        player_id=best.player_id,
        score=best.score,
        phash_score=best.phash_score,
        color_score=best.color_score,
        edge_score=best.edge_score,
        ncc_score=best.ncc_score,
        hash_distance=best.hash_distance,
        margin=margin,
        reference_path=best.reference_path,
    )


def decode_png_bgr(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        msg = "failed to decode PNG"
        raise ValueError(msg)
    return image


def is_main_city_screen(screen: str) -> bool:
    return str(screen or "").strip().lower() == "main_city"


def _clean_player_id(player_id: str | int) -> str:
    return str(player_id or "").strip()
