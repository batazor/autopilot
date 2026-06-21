"""Deals module analyzer contracts."""
from __future__ import annotations

from pathlib import Path

import yaml

DEALS_DIR = Path(__file__).resolve().parents[2]


def test_deals_modules_with_scenarios_have_existing_analyze_yaml() -> None:
    missing: list[str] = []

    for module_yaml in sorted(DEALS_DIR.glob("*/module.yaml")):
        module_dir = module_yaml.parent
        if not (module_dir / "scenarios").is_dir():
            continue

        module = yaml.safe_load(module_yaml.read_text()) or {}
        analyze_rel = module.get("analyze")
        if not analyze_rel:
            missing.append(f"{module_dir.name}: module.yaml has no analyze")
            continue

        analyze_path = module_dir / analyze_rel
        if not analyze_path.is_file():
            missing.append(f"{module_dir.name}: missing {analyze_rel}")
            continue

        analyze = yaml.safe_load(analyze_path.read_text()) or {}
        if not analyze.get("overlay"):
            missing.append(f"{module_dir.name}: {analyze_rel} has no overlay rules")

    assert missing == []
