"""Wiring checks for the alliance-broadcast delivery on the chat module.

Guarantees the cron tick scenario parses with a scheduler-supported cron shape,
the input/send tap regions are labeled, and the exec handler the scenario calls
is actually exported.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from modules.broadcast.engine import cron_interval_seconds

MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_broadcast_tick_scenario_uses_supported_cron() -> None:
    sc = _load_yaml("scenarios/broadcast_tick.yaml")
    assert sc["enabled"] is True
    assert cron_interval_seconds(sc["cron"]) > 0, "cron must be a scheduler-supported shape"
    steps = sc["steps"]
    assert len(steps) == 1
    assert steps[0]["exec"] == "alliance_broadcast_tick"


def test_area_declares_input_and_send_regions() -> None:
    area = _load_yaml("area.yaml")
    names = {r["name"] for s in area.get("screens", []) for r in s.get("regions", [])}
    assert "chat.alliance.input" in names
    assert "chat.alliance.send" in names


def test_exec_exports_the_handler() -> None:
    exec_path = MODULE_DIR / "exec.py"
    spec = importlib.util.spec_from_file_location("wos_chat_exec_under_test", exec_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "alliance_broadcast_tick" in mod.DSL_EXEC_HANDLERS
