"""Shared Navigator construction for tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from config.loader import Settings
from navigation.navigator import Navigator
from ocr.client import OcrClient


def make_navigator(
    capture_fn: Callable[[str], np.ndarray],
    tap_fn: Callable[..., bool | None],
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
