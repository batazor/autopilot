"""Operator ``WOS_MODULES`` allowlist over module discovery."""

from __future__ import annotations

import pytest

from config import module_discovery as md


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    md._clear_module_discovery_caches()
    yield
    md._clear_module_discovery_caches()


def test_no_env_keeps_all_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WOS_MODULES", raising=False)
    md._clear_module_discovery_caches()
    assert md._module_allowlist() is None
    assert md._module_allowed("core/arena") is True
    assert md._module_allowed("events/bear_hunt") is True


def test_empty_env_is_no_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOS_MODULES", "  ,  ")
    md._clear_module_discovery_caches()
    assert md._module_allowlist() is None
    assert md._module_allowed("events/bear_hunt") is True


def test_allowlist_matches_by_rel_basename_and_core_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WOS_MODULES", "intel, arena")
    md._clear_module_discovery_caches()
    # basename + core-stripped forms both hit
    assert md._module_allowed("intel") is True
    assert md._module_allowed("core/arena") is True
    # full rel also works
    monkeypatch.setenv("WOS_MODULES", "core/arena")
    md._clear_module_discovery_caches()
    assert md._module_allowed("core/arena") is True
    # anything not listed is filtered out
    assert md._module_allowed("events/bear_hunt") is False
    assert md._module_allowed("intel") is False


def test_allowlist_is_case_and_slash_forgiving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOS_MODULES", "/Core/Arena/, INTEL")
    md._clear_module_discovery_caches()
    assert md._module_allowed("core/arena") is True
    assert md._module_allowed("intel") is True


def test_iter_module_dirs_respects_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOS_MODULES", "intel,arena,common")
    md._clear_module_discovery_caches()
    dirs = [d.as_posix() for d in md.iter_module_dirs()]
    assert any(d.endswith("/intel") for d in dirs)
    assert any(d.endswith("/core/arena") for d in dirs)
    # an excluded feature module is absent
    assert not any(d.endswith("/events/bear_hunt") for d in dirs)
