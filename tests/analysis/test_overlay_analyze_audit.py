from __future__ import annotations

from pathlib import Path

import yaml

from ui.overlay_analyze_audit import audit_overlay_rules


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
