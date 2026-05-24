from __future__ import annotations

from typing import Any

from analysis.overlay_duration import parse_duration_seconds
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_xy_pct


def normalize_overlay_action(rule: dict[str, Any]) -> str:
    """Map YAML overlay ``action`` / boolean gates to the runtime action name."""
    action = str(rule.get("action") or "").strip()
    if action == "exist":
        action = "findIcon"
    if rule.get("isRedDot") is True and action not in ("findIcon", "feature_match"):
        action = "red_dot"
    elif rule.get("isRedDot") is False and action not in ("findIcon", "feature_match"):
        action = "red_dot_absent"
    if rule.get("isTabActive") is True:
        action = "tab_active"
    elif rule.get("isTabActive") is False:
        action = "tab_active_absent"
    if rule.get("isWhiteBorder") is True:
        action = "white_border"
    elif rule.get("isWhiteBorder") is False:
        action = "white_border_absent"
    return action


def _coerce_push_scenario_step(
    src: dict[str, Any] | str,
) -> dict[str, Any] | None:
    """Normalize a ``push_scenario`` payload into a push_tasks entry."""
    if isinstance(src, str):
        name = src.strip()
        if not name:
            return None
        return {"type": name, "priority": None, "ttl": None, "dsl_scenario": None}
    if not isinstance(src, dict):
        return None
    t = str(src.get("name") or src.get("type") or "").strip()
    if not t:
        return None
    pr_raw = src.get("priority")
    try:
        pr = int(pr_raw) if pr_raw is not None else None
    except (TypeError, ValueError):
        pr = None
    ttl = parse_duration_seconds(src.get("ttl"))
    dsl = str(src.get("dsl_scenario") or "").strip() or None
    return {"type": t, "priority": pr, "ttl": ttl, "dsl_scenario": dsl}


def optional_push_scenario_tasks(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract ``push_scenario`` steps from an overlay rule's ``steps:`` block.

    The DSL grammar mirrors scenarios:

    steps:
      - push_scenario: is_new_people            # string shortcut
      - push_scenario:
          name: claim_mail
          priority: 80000
          ttl: 15m

    Per-item ``priority`` is optional: ``worker.instance_worker_overlay`` resolves
    queue priority as: explicit push entry ``priority`` → scenario YAML top-level
    ``priority`` for the pushed name → overlay rule ``priority`` → ``80_000``.

    Other DSL step types inside ``steps:`` (click, wait, cond, ...) are accepted
    by the parser but not yet executed by the overlay engine; they are reserved
    for a follow-up that runs analyze ``steps:`` as an inline scenario.
    """
    out: list[dict[str, Any]] = []

    steps = rule.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            if "push_scenario" not in step:
                continue
            entry = _coerce_push_scenario_step(step.get("push_scenario"))
            if entry is not None:
                out.append(entry)

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


def optional_prefer_primary_bbox(rule: dict[str, Any]) -> bool:
    """YAML ``prefer_primary_bbox`` (findIcon-only): try cheap 1:1 match at the
    primary bbox before the sliding search inside ``search_region``.

    Off by default. Use only when the icon position is essentially fixed and
    ``search_region`` is just a small tolerance band around it — turning it on
    for grid/list scans (where the icon really moves) would short-circuit the
    sliding search at the wrong location whenever the primary bbox happens to
    correlate with the template.
    """
    v = rule.get("prefer_primary_bbox")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return False


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


async def overlay_rule_cond_allows(
    rule: dict[str, Any],
    *,
    instance_id: str | None = None,
    redis_async: Any | None = None,
    state_flat: dict[str, Any] | None = None,
) -> bool:
    """Whether a YAML ``cond`` on an overlay rule passes (skip rule when false)."""
    raw = rule.get("cond")
    if raw is None or isinstance(raw, bool):
        return True
    expr = str(raw).strip()
    if not expr:
        return True
    inst = str(instance_id or "").strip()
    if inst:
        from tasks.dsl_scenario_helpers import _dsl_cond_allows_step

        return await _dsl_cond_allows_step(
            {"cond": expr},
            inst,
            redis_async,
            state_flat=state_flat,
        )
    if state_flat is not None:
        from layout.area_versions import eval_cond

        return eval_cond(expr, state_flat)
    return False


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
    screen_id: str | None = None,
) -> str:
    """Effective ``search_region`` for ``findIcon``.

    Only explicit ``rule["search_region"]`` is honored. Movable primary
    regions use ``isSearch: true`` and the full-frame cached matcher.
    Returns ``""`` for fixed-bbox 1:1 template match at the primary region.
    """
    explicit = str(rule.get("search_region") or "").strip()
    if explicit:
        return explicit
    _ = (area_doc, region_name, ref_rel, state_flat, screen_id)
    return ""


def centers_delta_pct_between_regions(
    area_doc: dict[str, Any],
    from_region: str,
    to_region: str,
    *,
    state_flat: dict[str, Any] | None = None,
    screen_id: str | None = None,
) -> tuple[float, float] | None:
    """Vector ``to_center - from_center`` in percent of frame (from ``area.json`` bboxes)."""
    pa = screen_region_by_name(
        area_doc,
        from_region,
        state_flat=state_flat,
    )
    pb = screen_region_by_name(
        area_doc,
        to_region,
        state_flat=state_flat,
    )
    if pa is None or pb is None:
        return None
    ba = pa[1].get("bbox")
    bb = pb[1].get("bbox")
    if not isinstance(ba, dict) or not isinstance(bb, dict):
        return None
    ax, ay = bbox_percent_center_xy_pct(ba)
    bx, by = bbox_percent_center_xy_pct(bb)
    return bx - ax, by - ay

