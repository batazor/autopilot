from __future__ import annotations

from typing import Any

from analysis.overlay_duration import parse_duration_seconds
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_xy_pct


def optional_push_scenario_tasks(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Optional task enqueue hints for matched overlays.

    Preferred flat form:

    pushScenario:
      - name: is_new_people
        priority: 80000
        ttl: 15m

    Per-item ``priority`` is optional: ``worker.instance_worker_overlay`` resolves
    queue priority as: explicit push entry ``priority`` → scenario YAML top-level
    ``priority`` for the pushed name → overlay rule ``priority`` → ``80_000``.

    Nested form (still supported):

    pushScenario:
      - task:
          name: is_new_people
          priority: 80000
    """
    out: list[dict[str, Any]] = []

    pu = rule.get("pushScenario")
    if isinstance(pu, list):
        for item in pu:
            if not isinstance(item, dict):
                continue
            task = item.get("task")
            if isinstance(task, dict):
                src: dict[str, Any] = task
            elif item.get("name") is not None or item.get("type") is not None:
                src = item
            else:
                continue
            t = str(src.get("name") or src.get("type") or "").strip()
            if not t:
                continue
            pr_raw = src.get("priority")
            pr: int | None
            try:
                pr = int(pr_raw) if pr_raw is not None else None
            except (TypeError, ValueError):
                pr = None
            ttl = parse_duration_seconds(src.get("ttl"))
            dsl = str(src.get("dsl_scenario") or "").strip() or None
            out.append({"type": t, "priority": pr, "ttl": ttl, "dsl_scenario": dsl})

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


def optional_priority(rule: dict[str, Any]) -> int | None:
    v = rule.get("priority")
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def overlay_rule_screen_allowlist(rule: dict[str, Any]) -> list[str]:
    """Which FSM screens may run this overlay rule.

    Set ``screens`` to a string or list of strings (e.g. ``[main_city]``, ``[none]``).
    Empty / missing ``screens`` means **no gate** (rule evaluated on every tick).

    ``screens: [none]`` keeps the legacy meaning: allow evaluation when Redis
    ``current_screen`` is empty / unknown.
    """
    raw = rule.get("screens")
    out: list[str] = []
    if isinstance(raw, str):
        s = raw.strip()
        if s:
            out.append(s)
    elif isinstance(raw, list):
        for item in raw:
            s = str(item or "").strip()
            if s:
                out.append(s)
    return out


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


def resolved_search_region_for_findicon(
    area_doc: dict[str, Any],
    region_name: str,
    ref_rel: str,
    rule: dict[str, Any],
    *,
    state_flat: dict[str, Any] | None = None,
) -> str:
    """Effective ``search_region`` for ``findIcon``.

    Explicit ``rule["search_region"]`` wins if non-empty.

    Otherwise, when ``area.json`` defines ``{region_name}_search`` on the same screen as
    ``region_name`` (same ``ocr`` as ``ref_rel``) with a bbox, that name is used.

    Returns ``""`` for fixed-bbox 1:1 template match at the primary region.
    """
    explicit = str(rule.get("search_region") or "").strip()
    if explicit:
        return explicit
    primary = str(region_name or "").strip()
    if not primary:
        return ""
    candidate = f"{primary}_search"
    pair_s = screen_region_by_name(area_doc, candidate, state_flat=state_flat)
    if pair_s is None:
        return ""
    entry_s, reg_s = pair_s
    if str(entry_s.get("ocr") or "").strip() != str(ref_rel or "").strip():
        return ""
    if not isinstance(reg_s.get("bbox"), dict):
        return ""
    return candidate


def centers_delta_pct_between_regions(
    area_doc: dict[str, Any],
    from_region: str,
    to_region: str,
    *,
    state_flat: dict[str, Any] | None = None,
) -> tuple[float, float] | None:
    """Vector ``to_center - from_center`` in percent of frame (from ``area.json`` bboxes)."""
    pa = screen_region_by_name(area_doc, from_region, state_flat=state_flat)
    pb = screen_region_by_name(area_doc, to_region, state_flat=state_flat)
    if pa is None or pb is None:
        return None
    ba = pa[1].get("bbox")
    bb = pb[1].get("bbox")
    if not isinstance(ba, dict) or not isinstance(bb, dict):
        return None
    ax, ay = bbox_percent_center_xy_pct(ba)
    bx, by = bbox_percent_center_xy_pct(bb)
    return bx - ax, by - ay

