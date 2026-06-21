"""Per-module rolling-capture rate overrides (config/capture_rate.py)."""
from __future__ import annotations

from config.capture_rate import (
    MIN_CAPTURE_INTERVAL_S,
    capture_interval_s_for_scenario_key,
    module_capture_interval_ms,
)
from config.paths import repo_root


def test_fast_modules_declare_capture_interval() -> None:
    r = repo_root()
    # Fishing + dreamscape opt into ~10 fps via module.yaml.
    assert module_capture_interval_ms(r, "dreamscape_memory") == 100
    assert module_capture_interval_ms(r, "fishing_tournament") == 100


def test_module_without_override_is_none() -> None:
    r = repo_root()
    assert module_capture_interval_ms(r, "heroes") is None
    # Unknown / empty ids never override.
    assert module_capture_interval_ms(r, "does_not_exist") is None
    assert module_capture_interval_ms(r, None) is None


def test_scenario_key_resolves_to_owning_module_interval() -> None:
    r = repo_root()
    # A scenario from a fast module resolves to that module's seconds value.
    assert capture_interval_s_for_scenario_key(r, "dreamscape_memory") == 0.1
    assert capture_interval_s_for_scenario_key(r, "dreamscape_memory_multiplayer") == 0.1


def test_empty_or_coreless_scenario_key_is_none() -> None:
    r = repo_root()
    assert capture_interval_s_for_scenario_key(r, "") is None
    assert capture_interval_s_for_scenario_key(r, "   ") is None


def test_floor_is_sane() -> None:
    # 20 fps hard cap guards against a typo'd sub-millisecond interval.
    assert 0.0 < MIN_CAPTURE_INTERVAL_S <= 0.1
