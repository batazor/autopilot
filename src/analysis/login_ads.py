"""Login-ad scenario keys for the ads boot phase (phase 1 before ``who_i_am``).

Derived from ``modules/ads/analyze/analyze.yaml``: ``device_level`` overlay rules
whose ``cond`` gates on an empty ``active_player`` and declare ``pushScenario``.
Adding a new login popup = new scenario YAML + matching overlay rule — no worker
constant to maintain.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from analysis.overlay_manifest import (
    analyze_manifests_fingerprint,
    load_analyze_yaml,
)
from analysis.overlay_rules import optional_push_scenario_tasks
from config.paths import repo_root as default_repo_root
from dsl.registry import iter_module_analyze_manifests

_ADS_MODULE_SCOPE = "ads"
_LOGIN_AD_COND_RE = re.compile(
    r"^\s*active_player\s*==\s*(?P<rhs>(?:\"[^\"]*\"|'[^']*'|.+?))\s*$",
    re.IGNORECASE,
)
_EMPTY_ACTIVE_PLAYER_RHS = frozenset({"", "null", "nil", "none", "empty"})


def _cond_requires_empty_active_player(rule: dict[str, Any]) -> bool:
    raw = rule.get("cond")
    if raw is None or isinstance(raw, bool):
        return False
    m = _LOGIN_AD_COND_RE.match(str(raw).strip())
    if not m:
        return False
    rhs = m.group("rhs").strip().strip('"').strip("'").lower()
    return rhs in _EMPTY_ACTIVE_PLAYER_RHS


def _is_login_ad_overlay_rule(rule: dict[str, Any]) -> bool:
    if rule.get("device_level") is not True:
        return False
    if not _cond_requires_empty_active_player(rule):
        return False
    return bool(optional_push_scenario_tasks(rule))


def login_ad_task_types(repo_root: Path | None = None) -> frozenset[str]:
    """Scenario keys overlay may enqueue before ``active_player`` is known."""
    root = repo_root if repo_root is not None else default_repo_root()
    fp = analyze_manifests_fingerprint(root, module_scope=_ADS_MODULE_SCOPE)
    return _login_ad_task_types_cached(str(root), fp)


@lru_cache(maxsize=8)
def _login_ad_task_types_cached(
    repo_root_s: str,
    ads_analyze_fp: tuple[tuple[str, int, int], ...],
) -> frozenset[str]:
    del ads_analyze_fp  # bust cache when ads analyze manifests or includes change
    root = Path(repo_root_s)
    names: set[str] = set()
    for manifest in iter_module_analyze_manifests(root, module_scope=_ADS_MODULE_SCOPE):
        doc = load_analyze_yaml(manifest)
        overlay = doc.get("overlay")
        if not isinstance(overlay, list):
            continue
        for raw in overlay:
            if not isinstance(raw, dict) or not _is_login_ad_overlay_rule(raw):
                continue
            for task in optional_push_scenario_tasks(raw):
                key = str(task.get("type") or "").strip()
                if key:
                    names.add(key)
    return frozenset(names)


def clear_login_ad_task_types_cache() -> None:
    _login_ad_task_types_cached.cache_clear()
