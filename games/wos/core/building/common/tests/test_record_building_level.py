from __future__ import annotations

import importlib.util
from pathlib import Path

_EXEC = Path(__file__).resolve().parents[1] / "exec.py"
_spec = importlib.util.spec_from_file_location("building_common_exec", _EXEC)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
_parse = _mod._parse_level


def test_parse_furnace() -> None:
    assert _parse("Furnace Lv. 1") == ("furnace", 1)
    assert _parse("Furnace Lv 11") == ("furnace", 11)


def test_parse_multiword_and_apostrophe() -> None:
    assert _parse("Hunters' Hut Lv. 3") == ("hunters_hut", 3)
    assert _parse("Sawmill Lv 2") == ("sawmill", 2)


def test_parse_no_level() -> None:
    assert _parse("Cookhouse") is None
    assert _parse("") is None
    assert _parse("Survivors are getting cold") is None


def test_handler_registered() -> None:
    assert "record_building_level" in _mod.DSL_EXEC_HANDLERS
