"""Per-module rolling-capture rate overrides (config/capture_rate.py)."""
from __future__ import annotations

from config.capture_rate import (
    DEEP_IDLE_AFTER_S,
    DEEP_IDLE_SCRCPY_MAX_FPS,
    IDLE_SCRCPY_MAX_FPS,
    MIN_CAPTURE_INTERVAL_S,
    capture_interval_s_for_scenario_key,
    module_capture_interval_ms,
    scrcpy_max_fps_for_capture_interval,
)
from config.paths import repo_root


def test_scrcpy_fps_uncapped_for_fast_scenario() -> None:
    # A scenario with a capture override (fishing) streams uncapped (0),
    # regardless of any (stale) deep-idle verdict.
    assert scrcpy_max_fps_for_capture_interval(0.1) == 0
    assert scrcpy_max_fps_for_capture_interval(0.1, deep_idle=True) == 0


def test_scrcpy_fps_idle_cap_for_normal_scenario() -> None:
    # No override (normal autopilot / idle) → low cap.
    assert scrcpy_max_fps_for_capture_interval(None) == IDLE_SCRCPY_MAX_FPS
    assert IDLE_SCRCPY_MAX_FPS > 0


def test_scrcpy_fps_deep_idle_cap() -> None:
    # Long-stable idle screen / paused instance → deepest cap; still >0 so the
    # preview keepalive and a task's first capture have frames to grab.
    assert scrcpy_max_fps_for_capture_interval(None, deep_idle=True) == DEEP_IDLE_SCRCPY_MAX_FPS
    assert 0 < DEEP_IDLE_SCRCPY_MAX_FPS < IDLE_SCRCPY_MAX_FPS
    assert DEEP_IDLE_AFTER_S > 0


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
