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

    # This guards a DECISION, not the rule's shape: detection must stay
    # locale-proof. A findIcon crop carries one locale's label and breaks on the
    # other (seen live on the RU build), so the rule has to OCR the button's own
    # text and list both locales. Region / ttl / steps are deliberately NOT
    # asserted — restating them here only means editing two files instead of one.
    assert rule["action"] == "text"
    assert "одтверд" in rule["expected"]
    assert "Confirm" in rule["expected"]
