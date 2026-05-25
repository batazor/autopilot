"""Compile overlay YAML rules into a per-tick evaluation plan.

Normalization (action, screens, ttl, pushScenario, threshold, …) runs once when
manifests load — not on every ``evaluate_overlay_rules_async`` frame.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from analysis.overlay_rules import (
    normalize_overlay_action,
    optional_expected_texts,
    optional_inline_steps,
    optional_min_match_saturation,
    optional_prefer_primary_bbox,
    optional_priority,
    optional_push_scenario_tasks,
    optional_ttl_seconds,
    overlay_rule_screen_allowlist,
)


@dataclass(frozen=True)
class ScreenGate:
    """Pre-parsed ``screens`` filter for overlay rules."""

    allowed_lc: frozenset[str]
    glob_patterns: tuple[str, ...]
    wants_unknown: bool
    has_gate: bool

    def allows(self, cur_screen_norm: str) -> bool:
        if not self.has_gate:
            return True
        cur = (cur_screen_norm or "").strip()
        cur_lc = cur.lower()
        if cur:
            if cur_lc in self.allowed_lc:
                return True
            if self.glob_patterns:
                return any(
                    fnmatch.fnmatchcase(cur_lc, pat) for pat in self.glob_patterns
                )
            return False
        return self.wants_unknown


@dataclass(frozen=True)
class CompiledOverlayRule:
    """One overlay rule with hot-path fields precomputed."""

    raw: dict[str, Any]
    logical_name: str
    set_node_s: str
    priority: int | None
    ttl_seconds: float | None
    action: str
    region_name: str
    threshold: float
    push_tasks: list[dict[str, Any]]
    inline_steps: tuple[dict[str, Any], ...]
    expected: tuple[str, ...]
    screen: ScreenGate
    is_red_dot_required: bool | None
    min_match_saturation: float | None
    prefer_primary_bbox: bool
    search_region_explicit: str
    direct_template: str
    rule_type_lc: str
    cond_expr: str | None


@dataclass(frozen=True)
class CompiledOverlayPlan:
    """Ordered compiled rules from merged ``analyze.yaml`` overlay lists."""

    rules: tuple[CompiledOverlayRule, ...]

    def __iter__(self) -> Iterator[CompiledOverlayRule]:
        return iter(self.rules)


def _screen_gate_from_rule(rule: dict[str, Any]) -> ScreenGate:
    allowlist = overlay_rule_screen_allowlist(rule)
    if not allowlist:
        return ScreenGate(frozenset(), (), False, False)
    allowed_lc = frozenset(s.lower() for s in allowlist)
    glob_patterns = tuple(p for p in allowed_lc if "*" in p or "?" in p)
    return ScreenGate(
        allowed_lc=allowed_lc,
        glob_patterns=glob_patterns,
        wants_unknown="none" in allowed_lc,
        has_gate=True,
    )


def _rule_threshold(rule: dict[str, Any], *, default: float = 0.7) -> float:
    try:
        return float(rule.get("threshold", default))
    except (TypeError, ValueError):
        return default


def compile_overlay_rule(rule: dict[str, Any]) -> CompiledOverlayRule | None:
    if not isinstance(rule, dict):
        return None
    logical_name = str(rule.get("name") or "").strip()
    if not logical_name:
        return None
    set_node = rule.get("set_node")
    set_node_s = str(set_node).strip() if isinstance(set_node, str) else ""
    is_red_dot = rule.get("isRedDot")
    is_red_dot_required = is_red_dot if isinstance(is_red_dot, bool) else None
    inline_steps = tuple(optional_inline_steps(rule))
    # Process-local registry: worker looks up inline steps by rule name from
    # the matched payload, sidestepping the need to plumb a new field through
    # the ~15 ``hit[...]`` sites in ``overlay_engine``. Each (re)compile wins.
    if inline_steps:
        _INLINE_STEPS_REGISTRY[logical_name] = inline_steps
    else:
        _INLINE_STEPS_REGISTRY.pop(logical_name, None)
    return CompiledOverlayRule(
        raw=rule,
        logical_name=logical_name,
        set_node_s=set_node_s,
        priority=optional_priority(rule),
        ttl_seconds=optional_ttl_seconds(rule),
        action=normalize_overlay_action(rule),
        region_name=str(rule.get("region") or "").strip(),
        threshold=_rule_threshold(rule),
        push_tasks=optional_push_scenario_tasks(rule),
        inline_steps=inline_steps,
        expected=tuple(optional_expected_texts(rule)),
        screen=_screen_gate_from_rule(rule),
        is_red_dot_required=is_red_dot_required,
        min_match_saturation=optional_min_match_saturation(rule),
        prefer_primary_bbox=optional_prefer_primary_bbox(rule),
        search_region_explicit=str(rule.get("search_region") or "").strip(),
        direct_template=str(rule.get("template") or "").replace("\\", "/").strip(),
        rule_type_lc=str(rule.get("type") or "").strip().lower(),
        cond_expr=_cond_expr(rule),
    )


_INLINE_STEPS_REGISTRY: dict[str, tuple[dict[str, Any], ...]] = {}


def get_inline_steps(rule_name: str) -> tuple[dict[str, Any], ...]:
    """Look up inline ``steps:`` for a compiled overlay rule by name.

    Empty tuple if the rule has no inline steps (or hasn't been compiled).
    """
    return _INLINE_STEPS_REGISTRY.get(rule_name, ())


def _reset_inline_steps_registry() -> None:
    """Test helper: drop the inline-steps registry."""
    _INLINE_STEPS_REGISTRY.clear()


def _cond_expr(rule: dict[str, Any]) -> str | None:
    raw = rule.get("cond")
    if raw is None or isinstance(raw, bool):
        return None
    expr = str(raw).strip()
    return expr or None


def compile_overlay_plan(overlay_rules: list[dict[str, Any]]) -> CompiledOverlayPlan:
    compiled: list[CompiledOverlayRule] = []
    for rule in overlay_rules:
        if not isinstance(rule, dict):
            continue
        item = compile_overlay_rule(rule)
        if item is not None:
            compiled.append(item)
    return CompiledOverlayPlan(tuple(compiled))


def ensure_overlay_plan(
    overlay_rules: list[dict[str, Any]] | CompiledOverlayPlan,
) -> CompiledOverlayPlan:
    if isinstance(overlay_rules, CompiledOverlayPlan):
        return overlay_rules
    return compile_overlay_plan(overlay_rules)
