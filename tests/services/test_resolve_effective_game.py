"""``resolve_effective_game`` adopts the game actually running on the device.

bs1 may be configured for one game in SQLite while a different game is live on
screen. The worker should serve the running game (and point launch/foreground
checks at it) instead of force-launching the configured one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.loader import (
    InstanceConfig,
    OcrConfig,
    RedisConfig,
    SchedulerConfig,
    Settings,
    WorkerConfig,
    reset_settings,
    set_settings,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _settings(game: str) -> Settings:
    return Settings(
        redis=RedisConfig(url="redis://localhost:6379/0"),
        ocr=OcrConfig(),
        scheduler=SchedulerConfig(),
        worker=WorkerConfig(),
        instances=[
            InstanceConfig(
                instance_id="bs1",
                bluestacks_window_title="127.0.0.1:5555",
                game=game,
            ),
        ],
    )


class _FakeController:
    def __init__(self, running: str | None) -> None:
        self._running = running

    def __call__(self, *_args: object, **_kwargs: object) -> _FakeController:
        return self

    def detect_running_game(self) -> str | None:
        return self._running


@pytest.fixture(autouse=True)
def _clean_settings() -> Iterator[None]:
    reset_settings()
    yield
    reset_settings()


def _patch_controller(
    monkeypatch: pytest.MonkeyPatch, running: str | None, *, raises: bool = False
) -> None:
    import adb

    if raises:
        def _boom(*_a: object, **_k: object) -> object:
            msg = "device offline"
            raise RuntimeError(msg)

        monkeypatch.setattr(adb, "AdbController", _boom)
    else:
        monkeypatch.setattr(adb, "AdbController", _FakeController(running))


def test_adopts_running_game_over_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import resolve_effective_game

    settings = _settings("kingshot")
    set_settings(settings)
    _patch_controller(monkeypatch, running="wos")

    effective = resolve_effective_game(settings.instances[0])

    assert effective == "wos"
    # Settings.instances entry is updated so bot_actions launches WOS, not kingshot.
    assert settings.instances[0].game == "wos"


def test_keeps_configured_when_same_game(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import resolve_effective_game

    settings = _settings("wos")
    set_settings(settings)
    _patch_controller(monkeypatch, running="wos")

    assert resolve_effective_game(settings.instances[0]) == "wos"
    assert settings.instances[0].game == "wos"


def test_keeps_configured_when_nothing_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import resolve_effective_game

    settings = _settings("kingshot")
    set_settings(settings)
    _patch_controller(monkeypatch, running=None)

    # Fresh boot / launcher only — launch the configured game.
    assert resolve_effective_game(settings.instances[0]) == "kingshot"
    assert settings.instances[0].game == "kingshot"


def test_keeps_configured_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import resolve_effective_game

    settings = _settings("kingshot")
    set_settings(settings)
    _patch_controller(monkeypatch, running=None, raises=True)

    assert resolve_effective_game(settings.instances[0]) == "kingshot"
    assert settings.instances[0].game == "kingshot"
