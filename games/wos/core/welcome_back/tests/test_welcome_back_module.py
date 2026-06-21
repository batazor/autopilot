from __future__ import annotations

from pathlib import Path

import yaml

MODULE_DIR = Path(__file__).resolve().parents[1]


def test_welcome_back_overlay_clicks_confirm_inline() -> None:
    doc = yaml.safe_load(
        (MODULE_DIR / "analyze" / "analyze.yaml").read_text(encoding="utf-8")
    )
    rules = {rule["name"]: rule for rule in doc["overlay"]}
    rule = rules["welcome_back.confirm.visible"]

    assert rule["region"] == "button.confirm.green"
    assert rule["action"] == "findIcon"
    assert rule["device_level"] is True
    assert rule["ttl"] == "5s"
    assert rule["steps"] == [{"click": "button.confirm.green"}]
