"""Onboarding state + environment health for the first-run wizard."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis

ONBOARDING_KEY = "wos:onboarding:state"

# Sticky milestones we expose to the checklist. Once a timestamp is written,
# it never gets cleared — these are "have you ever done X" bits.
MILESTONES = (
    "device_added_at",
    "bot_started_at",
    "first_scenario_at",
    "first_approval_at",
    "first_ocr_at",
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _set_if_unset(client: redis.Redis, field: str) -> str | None:
    existing = client.hget(ONBOARDING_KEY, field)
    if existing:
        return str(existing)
    stamp = _now_iso()
    client.hsetnx(ONBOARDING_KEY, field, stamp)
    return str(client.hget(ONBOARDING_KEY, field) or stamp)


def _refresh_device_milestone(client: redis.Redis) -> str | None:
    from config.devices_db import load_registry

    if load_registry().devices:
        return _set_if_unset(client, "device_added_at")
    return None


def _refresh_bot_milestone(client: redis.Redis) -> str | None:
    from worker import local_bot

    try:
        status = local_bot.bot_status()
    except Exception:
        return None
    if status.get("running"):
        return _set_if_unset(client, "bot_started_at")
    return None


def read_state(client: redis.Redis) -> dict[str, Any]:
    """Return milestone bits, auto-detecting cheap ones on every call."""
    _refresh_device_milestone(client)
    _refresh_bot_milestone(client)
    raw = client.hgetall(ONBOARDING_KEY) or {}
    return {m: (str(raw[m]) if m in raw else None) for m in MILESTONES}


def _check_redis(client: redis.Redis) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        client.ping()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return {"ok": True, "latency_ms": latency_ms}


def _resolve_binary(configured: str, fallback: str) -> str | None:
    cand = configured.strip() or fallback
    resolved = shutil.which(cand) if "/" not in cand else cand
    if not resolved:
        return None
    return resolved


def _check_binary(name: str, configured: str, args: list[str]) -> dict[str, Any]:
    resolved = _resolve_binary(configured, name)
    if not resolved:
        return {"ok": False, "error": f"{name} not found on PATH"}
    try:
        result = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "path": resolved, "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": resolved, "error": "version check timed out"}
    if result.returncode != 0:
        return {
            "ok": False,
            "path": resolved,
            "error": (result.stderr or result.stdout or "non-zero exit").strip()[:200],
        }
    version_line = (result.stdout or result.stderr or "").strip().splitlines()
    version = version_line[0].strip() if version_line else ""
    return {"ok": True, "path": resolved, "version": version}


def check_env_health(client: redis.Redis) -> dict[str, Any]:
    from config.loader import load_settings

    settings = load_settings()
    return {
        "redis": _check_redis(client),
        "tesseract": _check_binary(
            "tesseract", settings.ocr.tesseract_cmd, ["--version"]
        ),
        "adb": _check_binary(
            "adb", settings.worker.adb_executable, ["version"]
        ),
    }
