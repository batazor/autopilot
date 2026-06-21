"""One-off area-region probe (exist/findIcon/red-dot) + live-vs-template crops."""
from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any

import cv2

from analysis.overlay_engine import evaluate_overlay_rules_async
from api.services.click_approval_overlay import (
    OverlayShape,
    load_preview_bytes,
)
from api.services.overlay_test.common import _area_region_names, _bbox_pct_to_px, _decode_png_to_bgr
from api.services.overlay_test.drawing import (
    _add_probe_best_match,
    _add_probe_search_area,
    _add_tap_marker_if_any,
)
from api.services.overlay_test.types import AreaRegionProbeResult, ProbeCrops, ProbeCropSide
from config.paths import repo_root
from dashboard.click_approvals import active_player_state_flat
from dashboard.reference_preview import load_rolling_instance_preview
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import (
    area_manifest_max_mtime,
    load_area_doc,
)
from layout.area_versions import effective_ocr_for_region
from layout.crop_paths import exported_crop_png, resolve_reference_path
from layout.template_match import patch_bgr_from_bbox_percent

if TYPE_CHECKING:
    import numpy as np


def _bgr_crop_to_data_url(fragment: np.ndarray) -> tuple[str, int, int] | None:
    if fragment.size <= 0:
        return None
    ok, enc = cv2.imencode(".png", fragment)
    if not ok:
        return None
    h, w = int(fragment.shape[0]), int(fragment.shape[1])
    b64 = base64.standard_b64encode(enc.tobytes()).decode("ascii")
    return f"data:image/png;base64,{b64}", w, h


def _ensure_fresh_reference_crop(
    *,
    repo: Any,
    ref_rel: str,
    bbox_pct: dict[str, Any],
    crop_path: Any,
    area_mtime: float,
) -> None:
    """Re-export crop when missing, stale, or wrong size vs labeling bbox rounding."""
    try:
        ref_path = resolve_reference_path(repo, ref_rel)
        ref_mtime = float(ref_path.stat().st_mtime) if ref_path.is_file() else 0.0
        crop_mtime = float(crop_path.stat().st_mtime) if crop_path.is_file() else 0.0
        img = cv2.imread(str(ref_path))
        if img is None:
            return
        crop, _ = patch_bgr_from_bbox_percent(img, bbox_pct)
        if crop.size <= 0:
            return
        exp_h, exp_w = int(crop.shape[0]), int(crop.shape[1])
        if crop_path.is_file():
            existing = cv2.imread(str(crop_path))
            if existing is not None:
                eh, ew = int(existing.shape[0]), int(existing.shape[1])
                if eh == exp_h and ew == exp_w and crop_mtime >= max(area_mtime, ref_mtime):
                    return
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(crop_path), crop)
    except Exception:
        return


def _build_region_probe_crops(
    *,
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo: Any,
    region_name: str,
    state_flat: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> ProbeCrops | None:
    """Live screenshot crop vs ``references/crop/`` template (Streamlit parity)."""
    pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
    if pair is None:
        return None
    entry, reg = pair
    bbox = reg.get("bbox")
    if not isinstance(bbox, dict):
        return None

    h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    left, top, right, bottom = _bbox_pct_to_px(bbox, w, h)
    pad = 6
    left = max(0, min(w - 1, left - pad))
    top = max(0, min(h - 1, top - pad))
    right = max(left + 1, min(w, right + pad))
    bottom = max(top + 1, min(h, bottom + pad))

    resolved_region = str(payload.get("resolved_region") or reg.get("name") or region_name).strip()
    ref_rel = effective_ocr_for_region(entry, reg)
    if not ref_rel:
        ref_rel = str(entry.get("ocr") or "").strip()

    live_side: ProbeCropSide = {"available": False, "width": 0, "height": 0, "label": "Live (rolling PNG)"}
    template_side: ProbeCropSide = {
        "available": False,
        "width": 0,
        "height": 0,
        "label": "Template crop",
    }

    try:
        live_frag = image_bgr[top:bottom, left:right].copy()
        live_enc = _bgr_crop_to_data_url(live_frag)
        if live_enc is not None:
            data_url, lw, lh = live_enc
            live_side = {
                "available": True,
                "width": lw,
                "height": lh,
                "label": "Live (rolling PNG)",
                "data_url": data_url,
            }
    except Exception:
        pass

    if ref_rel:
        try:
            area_mtime = area_manifest_max_mtime(repo)
            crop_path = exported_crop_png(repo, ref_rel, resolved_region)
            _ensure_fresh_reference_crop(
                repo=repo,
                ref_rel=ref_rel,
                bbox_pct=bbox,
                crop_path=crop_path,
                area_mtime=area_mtime,
            )
            if crop_path.is_file():
                tpl = cv2.imread(str(crop_path))
                if tpl is not None:
                    tpl_enc = _bgr_crop_to_data_url(tpl)
                    if tpl_enc is not None:
                        data_url, tw, th = tpl_enc
                        template_side = {
                            "available": True,
                            "width": tw,
                            "height": th,
                            "label": crop_path.name,
                            "data_url": data_url,
                        }
        except Exception:
            pass

    return ProbeCrops(
        region=region_name,
        resolved_region=resolved_region,
        reference_rel=ref_rel,
        live=live_side,
        template=template_side,
    )


def run_area_region_probe(
    *,
    client: Any,
    instance_id: str,
    region: str | None = None,
    threshold: float | None = None,
) -> AreaRegionProbeResult:
    """Run a one-off ``exist``/``findIcon`` probe for a selected area region."""
    from dashboard.redis_client import get_instance_state

    inst_state = get_instance_state(client, instance_id) or {}
    current_screen = str(inst_state.get("current_screen") or "").strip()
    active_player = str(inst_state.get("active_player") or "").strip()
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)

    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id, payload=None, source="live"
    )
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)

    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = _decode_png_to_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    repo = repo_root()
    area_doc = load_area_doc(repo)
    regions = _area_region_names(area_doc)
    selected = (region or "").strip()
    if selected not in regions:
        selected = regions[0] if regions else ""

    payload: dict[str, Any] | None = None
    overlays: list[OverlayShape] = []
    crops: ProbeCrops | None = None
    if image_bgr is not None and selected:
        selected_pair = screen_region_by_name(
            area_doc,
            selected,
            state_flat=state_flat,
        )
        selected_region_def = selected_pair[1] if selected_pair is not None else {}
        raw_threshold = (
            threshold
            if threshold is not None
            else selected_region_def.get("threshold", 0.9)
        )
        safe_threshold = max(0.0, min(1.0, float(raw_threshold)))
        use_red_dot_probe = bool(selected_region_def.get("has_red_dot")) and not bool(
            selected_region_def.get("isSearch")
        )
        rule = {
            "name": f"probe.area.{selected}",
            "region": selected,
        }
        if use_red_dot_probe:
            rule["isRedDot"] = True
        else:
            rule["action"] = "exist"
            rule["threshold"] = safe_threshold
        try:
            results = asyncio.run(
                evaluate_overlay_rules_async(
                    image_bgr,
                    area_doc,
                    repo,
                    [rule],
                    current_screen=current_screen or None,
                    state_flat=state_flat,
                )
            )
            raw = results.get(str(rule["name"]))
            payload = raw if isinstance(raw, dict) else {}
        except Exception as exc:
            payload = {
                "matched": False,
                "region": selected,
                "action": "findIcon",
                "threshold": safe_threshold,
                "reason": f"{type(exc).__name__}: {exc}",
            }

        if width > 0 and height > 0 and payload is not None:
            _add_probe_search_area(
                overlays,
                payload=payload,
                region_name=selected,
                area_doc=area_doc,
                state_flat=state_flat,
                w=width,
                h=height,
            )
            _add_probe_best_match(
                overlays,
                payload=payload,
                region_name=selected,
                area_doc=area_doc,
                state_flat=state_flat,
                w=width,
                h=height,
            )
            _add_tap_marker_if_any(overlays, payload=payload, w=width, h=height)
            crops = _build_region_probe_crops(
                image_bgr=image_bgr,
                area_doc=area_doc,
                repo=repo,
                region_name=selected,
                state_flat=state_flat,
                payload=payload,
            )

    return AreaRegionProbeResult(
        instance_id=instance_id,
        current_screen=current_screen,
        active_player=active_player,
        selected_region=selected,
        regions=regions,
        preview={
            "available": png is not None,
            "rel": rel,
            "mtime": mtime,
            "width": width,
            "height": height,
        },
        result=payload,
        overlays=overlays,
        crops=crops,
    )
