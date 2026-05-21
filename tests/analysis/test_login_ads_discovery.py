"""``login_ad_task_types`` tracks ads overlay rules, not a hand-maintained set."""

from __future__ import annotations

from analysis.login_ads import (
    _is_login_ad_overlay_rule,
    clear_login_ad_task_types_cache,
    login_ad_task_types,
)
from config.paths import repo_root


def test_login_ad_overlay_rule_matcher() -> None:
    assert _is_login_ad_overlay_rule({
        "device_level": True,
        "cond": 'active_player == ""',
        "pushScenario": [{"name": "myriad_bazaar"}],
    })
    assert not _is_login_ad_overlay_rule({
        "device_level": True,
        "cond": "active_player != null",
        "pushScenario": [{"name": "mail.claim"}],
    })


def test_login_ad_task_types_from_ads_analyze() -> None:
    clear_login_ad_task_types_cache()
    types = login_ad_task_types(repo_root())
    assert "myriad_bazaar" in types
    assert "ads_natalia" in types
