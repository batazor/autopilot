"""ADB integration: screencap, device control, click approvals, BotActions facade."""

from __future__ import annotations

from adb.approvals import (
    APPROVAL_CURRENT_TTL_SECONDS,
    _consume_skip,
    _redis,
    _require_approval,
    abort_pending_approval,
    click_approval_enabled,
)
from adb.bot_actions import BotActions
from adb.controller import AdbController, ProcessDetection
from adb.screencap import (
    DEFAULT_ADB_BIN,
    DEFAULT_ADB_TIMEOUT_SECONDS,
    MSG_ADB_NOT_FOUND,
    adb_screencap_bgr,
    adb_screencap_png,
    adb_screencap_to_file,
    resolve_adb_executable,
)
from adb.serial import canonical_adb_serial

__all__ = [
    "APPROVAL_CURRENT_TTL_SECONDS",
    "DEFAULT_ADB_BIN",
    "DEFAULT_ADB_TIMEOUT_SECONDS",
    "MSG_ADB_NOT_FOUND",
    "AdbController",
    "BotActions",
    "ProcessDetection",
    "_consume_skip",
    "_redis",
    "_require_approval",
    "abort_pending_approval",
    "adb_screencap_bgr",
    "adb_screencap_png",
    "adb_screencap_to_file",
    "canonical_adb_serial",
    "click_approval_enabled",
    "resolve_adb_executable",
]
