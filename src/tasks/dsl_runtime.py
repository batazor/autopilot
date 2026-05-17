"""Runtime accessors for DSL handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navigation.navigator import Navigator
from services import get_ocr_client, get_settings

if TYPE_CHECKING:
    from adb import BotActions
    from config.loader import Settings
    from ocr.client import OcrClient


def settings() -> Settings:
    return get_settings()


def bot_actions() -> BotActions:
    # Tests monkeypatch ``tasks.dsl_scenario.BotActions`` with a zero-arg factory
    # returning a fake. Production binds the real :class:`adb.BotActions` class
    # (a ``type``) which must be constructed with settings.
    from tasks import dsl_scenario as _dsl_scenario

    ctor = _dsl_scenario.BotActions
    if isinstance(ctor, type):
        return ctor(get_settings())
    return ctor()


def ocr_client() -> OcrClient:
    return get_ocr_client()


def navigator(actions: BotActions, *, redis_client: Any | None = None) -> Navigator:
    return Navigator(
        actions.capture_screen_bgr,
        actions.tap,
        settings=get_settings(),
        ocr_client=ocr_client(),
        redis_client=redis_client,
    )
