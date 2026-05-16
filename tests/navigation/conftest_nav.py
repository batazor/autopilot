"""Shared Navigator construction for tests."""

from __future__ import annotations

from typing import Any

from config.loader import Settings
from navigation.navigator import Navigator
from ocr.client import OcrClient


def make_navigator(
    capture_fn: object,
    tap_fn: object,
    *,
    settings: Settings,
    ocr_client: OcrClient,
    redis_client: Any | None = None,
) -> Navigator:
    return Navigator(
        capture_fn,
        tap_fn,
        settings=settings,
        ocr_client=ocr_client,
        redis_client=redis_client,
    )
