from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_sync_module():
    path = _REPO_ROOT / "cmd" / "sync_hero_recruitment_resources.py"
    spec = importlib.util.spec_from_file_location("sync_hero_recruitment_resources", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hr_sync():
    return _load_sync_module()


def test_parse_hud_count(hr_sync: object) -> None:
    p = hr_sync.parse_hud_count
    assert p("") is None
    assert p("123") == 123
    assert p("1,234") == 1234
    assert p("12 345") == 12345
    assert p("x") is None
