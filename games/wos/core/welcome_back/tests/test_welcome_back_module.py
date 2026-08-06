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

    # Locale-proof detection: OCR the button's own label (EN + RU) and click
    # the same region — the overlay inline hot path only taps the rule's own
    # region, and a findIcon crop would carry one locale's label and break on
    # the other (seen live on the RU build).
    assert rule["region"] == "button.confirm.green"
    assert rule["action"] == "text"
    assert "одтверд" in rule["expected"]
    assert "Confirm" in rule["expected"]
    assert rule["device_level"] is True
    assert rule["ttl"] == "5s"
    assert rule["steps"] == [{"click": "button.confirm.green"}]
