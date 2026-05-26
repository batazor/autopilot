from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from dashboard.overlay_analyze_audit import audit_overlay_rules

if TYPE_CHECKING:
    from pathlib import Path


def test_audit_flags_missing_region_and_exist_action(tmp_path: Path) -> None:
    area = {
        "screens": [
            {
                "id": "main",
                "regions": [
                    {"name": "btn_ok", "action": "exist", "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
                ],
            }
        ]
    }
    (tmp_path / "area.json").write_text(yaml.dump(area), encoding="utf-8")

    rules = [
        {
            "name": "bad_region",
            "action": "findIcon",
            "region": "missing_btn",
        },
        {
            "name": "bad_action",
            "action": "exist",
            "region": "btn_ok",
        },
    ]
    issues = audit_overlay_rules(area, rules, repo_root_path=tmp_path)
    messages = {i.message for i in issues}
    assert any("missing area region" in m for m in messages)
    assert any("findIcon" in m and "exist" in m for m in messages)


def test_audit_ok_when_region_and_action_valid(tmp_path: Path) -> None:
    area = {
        "screens": [
            {
                "id": "main",
                "regions": [
                    {"name": "btn_ok", "action": "exist", "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
                ],
            }
        ]
    }
    rules = [{"name": "ok_rule", "action": "findIcon", "region": "btn_ok"}]
    issues = audit_overlay_rules(area, rules, repo_root_path=tmp_path)
    assert issues == []


def test_audit_flags_missing_action(tmp_path: Path) -> None:
    """Overlay rule without ``action:`` would silently no-op at runtime
    (overlay_engine marks it as ``unsupported_action``). Catch at audit time."""
    area = {
        "screens": [
            {
                "id": "main",
                "regions": [
                    {"name": "btn_ok", "action": "exist", "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
                ],
            }
        ]
    }
    rules = [
        {
            "name": "missing_action",
            "region": "btn_ok",
            "steps": [{"click": "btn_ok"}],
        }
    ]
    issues = audit_overlay_rules(area, rules, repo_root_path=tmp_path)
    messages = {i.message for i in issues}
    assert any("no `action:`" in m and "unsupported_action" in m for m in messages)


def test_audit_accepts_red_dot_gate_without_explicit_action(tmp_path: Path) -> None:
    """``isRedDot: true`` alone is enough — normalize_overlay_action derives
    the action from the boolean gate, so the rule must not be flagged."""
    area = {
        "screens": [
            {
                "id": "main",
                "regions": [
                    {
                        "name": "btn_ok",
                        "action": "exist",
                        "has_red_dot": True,
                        "bbox": {"x": 1, "y": 2, "w": 3, "h": 4},
                    },
                ],
            }
        ]
    }
    rules = [{"name": "red_dot_gated", "region": "btn_ok", "isRedDot": True}]
    issues = audit_overlay_rules(area, rules, repo_root_path=tmp_path)
    assert issues == []


def test_audit_flags_unknown_action(tmp_path: Path) -> None:
    area = {
        "screens": [
            {
                "id": "main",
                "regions": [
                    {"name": "btn_ok", "action": "exist", "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
                ],
            }
        ]
    }
    rules = [{"name": "weird", "action": "bogus_match", "region": "btn_ok"}]
    issues = audit_overlay_rules(area, rules, repo_root_path=tmp_path)
    messages = {i.message for i in issues}
    assert any("not dispatched by overlay_engine" in m for m in messages)
