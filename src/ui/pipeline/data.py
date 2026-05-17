from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any

import cv2
import streamlit as st
import yaml

from analysis.overlay import run_overlay_analysis_sync
from analysis.overlay_manifest import analyze_manifests_mtime, load_merged_analyze_yaml
from analysis.overlay_rules import (
    overlay_rule_screen_allowlist,
    resolved_search_region_for_findicon,
)
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region
from ui.keys import PIPELINE_OVERLAY_CACHE
from ui.reference_preview import rolling_live_preview_path

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np


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
    instance_id: str, *, repo_root: Path, area_path: Path
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
        analyze_manifests_mtime(repo_root),
    )


def pipeline_overlay_cache_key(
    instance_id: str,
    current_screen: str | None,
    module_scope: str | None = None,
) -> tuple[str, str, str]:
    """Cache bucket for rolling-overlay analysis (instance + node)."""
    sk = (current_screen or "").strip()
    scope = (module_scope or "").strip()
    return (instance_id, sk, scope)


def clear_pipeline_overlay_cache_entries(instance_id: str) -> None:
    """Drop cached overlay rows for *instance_id* (all ``current_screen`` variants)."""
    cache = st.session_state.get(PIPELINE_OVERLAY_CACHE)
    if not isinstance(cache, dict):
        return
    for k in list(cache.keys()):
        if k == instance_id or (isinstance(k, tuple) and len(k) >= 1 and k[0] == instance_id):
            cache.pop(k, None)


def get_or_build_pipeline_cache(
    instance_id: str,
    *,
    repo_root: Path,
    area_path: Path,
    current_screen: str | None = None,
    state_flat: dict[str, Any] | None = None,
    module_scope: str | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Return analysis data for *instance_id*, rebuilding only when a source file changes.

    Caches in st.session_state[PIPELINE_OVERLAY_CACHE] keyed by ``(instance_id, current_screen)``.
    Invalidated when the rolling PNG, area.json, module analyze mtime, **or** ``current_screen``
    changes — rules with YAML ``screens`` depend on Redis ``current_screen``
    (same as ``worker/instance_worker.py``).

    ``state_flat`` (when provided) is forwarded to overlay analysis so screen-version ``cond``
    selection picks the right ``_vN`` region per player. Cache key includes nothing about state
    yet — this works because the active player rarely changes per instance during a session;
    if it does, callers should bump ``force_nonce`` to invalidate.
    """
    preview_mtime, area_mtime, _ = mtimes(instance_id, repo_root=repo_root, area_path=area_path)
    analyze_mtime = analyze_manifests_mtime(repo_root, module_scope=module_scope)
    if preview_mtime is None:
        return None, False

    screen_key = (current_screen or "").strip()
    scope_key = (module_scope or "").strip()
    overlay_ck = pipeline_overlay_cache_key(instance_id, current_screen, module_scope)

    cache: dict = st.session_state.setdefault(PIPELINE_OVERLAY_CACHE, {})
    entry = cache.get(overlay_ck)
    nonce = force_nonce()

    if (
        entry is not None
        and entry["preview_mtime"] == preview_mtime
        and entry["area_mtime"] == area_mtime
        and entry["analyze_mtime"] == analyze_mtime
        and entry.get("current_screen", "") == screen_key
        and entry.get("module_scope", "") == scope_key
        and entry.get("nonce", 0) == nonce
    ):
        return entry, False

    image_bgr = read_rolling_png_with_retry(instance_id, attempts=3)
    if image_bgr is None:
        return None, True

    results = run_overlay_analysis_sync(
        image_bgr,
        repo_root=repo_root,
        current_screen=screen_key or None,
        state_flat=state_flat,
        module_scope=module_scope,
    )

    area_doc: dict = {}
    if area_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            area_doc = json.loads(area_path.read_text(encoding="utf-8"))

    rule_order: list[str] = []
    rule_search: dict[str, str] = {}
    rule_node: dict[str, str] = {}
    if analyze_mtime is not None:
        try:
            raw_yaml = load_merged_analyze_yaml(repo_root, module_scope=module_scope)
            ov = raw_yaml.get("overlay") if isinstance(raw_yaml, dict) else None
            for r in ov if isinstance(ov, list) else []:
                if not isinstance(r, dict):
                    continue
                nm = str(r.get("name") or "").strip()
                if not nm:
                    continue
                rule_order.append(nm)
                reg_nm = str(r.get("region") or "").strip()
                pair_rr = (
                    screen_region_by_name(area_doc, reg_nm, state_flat=state_flat)
                    if reg_nm
                    else None
                )
                ref_rr = (
                    effective_ocr_for_region(pair_rr[0], pair_rr[1])
                    if pair_rr is not None
                    else ""
                )
                sr_eff = (
                    resolved_search_region_for_findicon(
                        area_doc, reg_nm, ref_rr, r, state_flat=state_flat
                    )
                    if pair_rr is not None
                    else ""
                )
                if sr_eff:
                    rule_search[nm] = sr_eff
                gate = overlay_rule_screen_allowlist(r)
                if gate:
                    rule_node[nm] = gate[0]
        except (OSError, yaml.YAMLError):
            pass

    entry = {
        "preview_mtime": preview_mtime,
        "area_mtime": area_mtime,
        "analyze_mtime": analyze_mtime,
        "nonce": nonce,
        "current_screen": screen_key,
        "module_scope": scope_key,
        "image_bgr": image_bgr,
        "results": results,
        "area_doc": area_doc,
        "rule_order": rule_order,
        "rule_search": rule_search,
        "rule_node": rule_node,
    }
    cache[overlay_ck] = entry
    return entry, True
