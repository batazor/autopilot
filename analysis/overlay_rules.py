from __future__ import annotations

from typing import Any

from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_xy_pct

from analysis.overlay_duration import parse_duration_seconds


def optional_push_scenario_tasks(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Optional task enqueue hints for matched overlays.

    Preferred (nested, readable) form:

    pushScenario:
      - task:
          name: is_new_people
          priority: 80000
          ttl: 15m

    Backward compatible form:
    - pushUsecase: [...]
    - push_task_type / push_task_priority (flat)
    """
    out: list[dict[str, Any]] = []

    pu = rule.get("pushScenario")
    if not isinstance(pu, list):
        # Backward compat
        pu = rule.get("pushUsecase")

    if isinstance(pu, list):
        for item in pu:
            if not isinstance(item, dict):
                continue
            task = item.get("task")
            if not isinstance(task, dict):
                continue
            t = str(task.get("name") or task.get("type") or "").strip()
            if not t:
                continue
            pr_raw = task.get("priority")
            pr: int | None
            try:
                pr = int(pr_raw) if pr_raw is not None else None
            except (TypeError, ValueError):
                pr = None
            ttl_raw = task.get("ttl")
            if ttl_raw is None:
                ttl_raw = task.get("ttl_seconds")  # backward compat
            ttl = parse_duration_seconds(ttl_raw)
            dsl = str(task.get("dsl_scenario") or "").strip() or None
            out.append({"type": t, "priority": pr, "ttl": ttl, "dsl_scenario": dsl})

    # Flat fallback
    if not out:
        t = str(rule.get("push_task_type") or "").strip()
        if t:
            pr_raw = rule.get("push_task_priority")
            pr2: int | None
            try:
                pr2 = int(pr_raw) if pr_raw is not None else None
            except (TypeError, ValueError):
                pr2 = None
            out.append({"type": t, "priority": pr2, "ttl": None, "dsl_scenario": None})

    return out


def optional_min_match_saturation(rule: dict[str, Any]) -> float | None:
    """YAML ``min_match_saturation`` (0–255): reject match if mean HSV S is below."""
    v = rule.get("min_match_saturation")
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def optional_fuzzy_threshold(rule: dict[str, Any]) -> float | None:
    v = rule.get("fuzzy_threshold")
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def optional_priority(rule: dict[str, Any]) -> int | None:
    v = rule.get("priority")
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def optional_ttl_seconds(rule: dict[str, Any]) -> float | None:
    """YAML ``ttl``: minimum gap between successive evaluations (``5``, ``5s``, ``1m``, …)."""
    v = rule.get("ttl")
    if v is None or isinstance(v, bool):
        return None
    sec = parse_duration_seconds(v)
    if sec is None:
        return None
    return float(sec)


def optional_expected_texts(rule: dict[str, Any]) -> list[str]:
    v = rule.get("expected")
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    s = rule.get("expected_text")
    if s:
        return [str(s)]
    return []


def centers_delta_pct_between_regions(
    area_doc: dict[str, Any],
    from_region: str,
    to_region: str,
) -> tuple[float, float] | None:
    """Vector ``to_center - from_center`` in percent of frame (from ``area.json`` bboxes)."""
    pa = screen_region_by_name(area_doc, from_region)
    pb = screen_region_by_name(area_doc, to_region)
    if pa is None or pb is None:
        return None
    ba = pa[1].get("bbox")
    bb = pb[1].get("bbox")
    if not isinstance(ba, dict) or not isinstance(bb, dict):
        return None
    ax, ay = bbox_percent_center_xy_pct(ba)
    bx, by = bbox_percent_center_xy_pct(bb)
    return bx - ax, by - ay

