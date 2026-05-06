from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import streamlit as st
import yaml

from analysis.overlay import load_analyze_yaml, run_overlay_analysis_sync
from ui.keys import PIPELINE_OVERLAY_CACHE
from ui.reference_preview import rolling_live_preview_path


def force_nonce() -> int:
    """Manual refresh knob for the live fragment."""
    v = st.session_state.get("pipeline_force_refresh_nonce", 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def read_rolling_png_with_retry(instance_id: str, *, attempts: int = 3) -> np.ndarray | None:
    """cv2.imread can return None if we race an atomic file swap."""
    path = rolling_live_preview_path(instance_id)
    for i in range(max(1, attempts)):
        image_bgr = cv2.imread(str(path))
        if image_bgr is not None:
            return image_bgr
        if i < attempts - 1:
            import time

            time.sleep(0.08)
    return None


def mtimes(
    instance_id: str, *, repo_root: Path, area_path: Path, analyze_path: Path
) -> tuple[float | None, float | None, float | None]:
    """Return (preview_mtime, area_mtime, analyze_mtime); None when file is absent."""

    def _mt(p: Path) -> float | None:
        try:
            return p.stat().st_mtime if p.is_file() else None
        except OSError:
            return None

    return (
        _mt(rolling_live_preview_path(instance_id)),
        _mt(area_path),
        _mt(analyze_path),
    )


def get_or_build_pipeline_cache(
    instance_id: str, *, repo_root: Path, area_path: Path, analyze_path: Path
) -> tuple[dict[str, Any] | None, bool]:
    """Return analysis data for *instance_id*, rebuilding only when a source file changes.

    Caches in st.session_state[PIPELINE_OVERLAY_CACHE] keyed by instance_id.
    Invalidated when the rolling PNG, area.json, or analyze.yaml mtime changes.
    """
    preview_mtime, area_mtime, analyze_mtime = mtimes(
        instance_id, repo_root=repo_root, area_path=area_path, analyze_path=analyze_path
    )
    if preview_mtime is None:
        return None, False

    cache: dict = st.session_state.setdefault(PIPELINE_OVERLAY_CACHE, {})
    entry = cache.get(instance_id)
    nonce = force_nonce()

    if (
        entry is not None
        and entry["preview_mtime"] == preview_mtime
        and entry["area_mtime"] == area_mtime
        and entry["analyze_mtime"] == analyze_mtime
        and entry.get("nonce", 0) == nonce
    ):
        return entry, False

    image_bgr = read_rolling_png_with_retry(instance_id, attempts=3)
    if image_bgr is None:
        return None, True

    results = run_overlay_analysis_sync(image_bgr, repo_root=repo_root)

    area_doc: dict = {}
    if area_path.is_file():
        try:
            area_doc = json.loads(area_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    rule_order: list[str] = []
    rule_search: dict[str, str] = {}
    rule_tap: dict[str, str] = {}
    if analyze_path.is_file():
        try:
            raw_yaml = load_analyze_yaml(analyze_path)
            ov = raw_yaml.get("overlay") if isinstance(raw_yaml, dict) else None
            for r in ov if isinstance(ov, list) else []:
                if not isinstance(r, dict):
                    continue
                nm = str(r.get("name") or "").strip()
                if not nm:
                    continue
                rule_order.append(nm)
                if sr := r.get("search_region"):
                    rule_search[nm] = str(sr).strip()
                if tr := r.get("tap_region"):
                    rule_tap[nm] = str(tr).strip()
        except (OSError, yaml.YAMLError):
            pass

    entry = {
        "preview_mtime": preview_mtime,
        "area_mtime": area_mtime,
        "analyze_mtime": analyze_mtime,
        "nonce": nonce,
        "image_bgr": image_bgr,
        "results": results,
        "area_doc": area_doc,
        "rule_order": rule_order,
        "rule_search": rule_search,
        "rule_tap": rule_tap,
    }
    cache[instance_id] = entry
    return entry, True

