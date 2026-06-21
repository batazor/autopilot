#!/usr/bin/env python3
"""Drop redundant ``landmarks:`` blocks from screen_verify.yaml when rules suffice.

Run from repo root: ``uv run python scripts/strip_redundant_screen_verify_landmarks.py``

Keeps ``landmarks`` only when it differs from ``rules`` (shop sub-tabs: narrow detect).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def _rule_key(rule: object) -> str:
    if not isinstance(rule, dict):
        return repr(rule)
    return yaml.dump(rule, sort_keys=True, default_flow_style=True).strip()


def _landmarks_redundant(landmarks: list, rules: list) -> bool:
    if landmarks == rules:
        return True
    rule_keys = {_rule_key(r) for r in rules}
    return all(_rule_key(lm) in rule_keys for lm in landmarks)


def _strip_entry(entry: dict) -> bool:
    landmarks = entry.get("landmarks")
    rules = entry.get("rules")
    if not isinstance(landmarks, list) or not landmarks:
        return False
    if not isinstance(rules, list) or not rules:
        entry["rules"] = list(landmarks)
        del entry["landmarks"]
        return True
    if not _landmarks_redundant(landmarks, rules):
        return False
    del entry["landmarks"]
    return True


def _process_file(path: Path) -> int:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return 0
    screens = raw.get("screens")
    if not isinstance(screens, dict):
        return 0
    changed = 0
    for entry in screens.values():
        if isinstance(entry, dict) and _strip_entry(entry):
            changed += 1
    if changed:
        path.write_text(
            yaml.dump(raw, sort_keys=False, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    return changed


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    paths = sorted(
        p
        for p in root.rglob("screen_verify.yaml")
        if "draft" not in p.parts and ".venv" not in p.parts
    )
    total = 0
    for path in paths:
        n = _process_file(path)
        if n:
            print(f"{path.relative_to(root)}: stripped {n} screen(s)")
            total += n
    print(f"done ({total} screens updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
