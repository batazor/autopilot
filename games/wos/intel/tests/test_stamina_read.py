"""Robust intel stamina reading: never store an OCR-mangled max.

scrcpy H.264 compression corrupts the small denominator of the board stamina
counter on live frames ("43/70" → "43/710" / "43/10") while the numerator stays
clean. ``state.parse_stamina`` rejects the implausible max, and the
``read_intel_stamina`` handler retries on fresh frames, falling back to "store
current, keep the last known max" when no clean read appears.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from games.wos.intel.state import STAMINA_MAX_CAP, parse_stamina

from tasks.dsl_exec.context import DslExecContext

MODULE_DIR = Path(__file__).resolve().parents[1]


# --- pure parser -------------------------------------------------------------


def test_parse_clean_reads() -> None:
    assert parse_stamina("43/70") == (43, 70)
    assert parse_stamina("14/90") == (14, 90)
    assert parse_stamina("70/70") == (70, 70)        # current == max is valid
    assert parse_stamina(" 43 / 70 ") == (43, 70)    # tolerant of spacing


def test_parse_rejects_mangled_max_keeps_current() -> None:
    # The exact live corruptions: an inserted digit and a dropped digit.
    assert parse_stamina("43/710") == (43, None)     # max > cap → artefact
    assert parse_stamina("43/10") == (43, None)      # max < current → artefact
    assert parse_stamina("43/810") == (43, None)


def test_parse_rejects_implausible_current() -> None:
    assert parse_stamina("430/70") is None           # current > cap
    assert parse_stamina("0/70") is None             # current must be > 0


def test_parse_plain_single_number() -> None:
    # The top-right board stamina counter is a plain «200» (no «/max»); it must
    # parse as (value, None). Regression for the bug where the reader pointed at
    # the bottom-left LEVEL bar («37/70» → stamina 7) and declined every fight.
    assert parse_stamina("200") == (200, None)
    assert parse_stamina("200.") == (200, None)     # trailing OCR dot
    assert parse_stamina("136") == (136, None)
    assert parse_stamina("44") == (44, None)


def test_parse_no_digit_pair() -> None:
    assert parse_stamina("") is None
    assert parse_stamina(None) is None
    assert parse_stamina("MAX:") is None
    assert parse_stamina("ter a.") is None


def test_parse_cap_is_configurable() -> None:
    assert parse_stamina("43/710", cap=1000) == (43, 710)
    assert STAMINA_MAX_CAP == 300


# --- handler retry + fallback ------------------------------------------------


def _load_exec() -> Any:
    spec = importlib.util.spec_from_file_location(
        "intel_exec_stamina_test", MODULE_DIR / "exec.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXEC = _load_exec()


class _SeqOcr:
    """Returns a scripted OCR text per call (repeats the last one if exhausted)."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.calls = 0

    async def ocr_region(self, *_a: Any, **_k: Any) -> Any:
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return type("R", (), {"text": text, "confidence": 0.6})()


class _Actions:
    def __init__(self) -> None:
        self.captures = 0

    def capture_screen_bgr(self, _inst: str) -> Any:
        self.captures += 1
        return np.zeros((1280, 720, 3), dtype=np.uint8)


class _Redis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}

    async def hset(self, key: str, mapping: dict[str, Any]) -> int:
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)


def _wire(monkeypatch: Any, ocr: _SeqOcr, actions: _Actions) -> None:
    monkeypatch.setattr(EXEC.dsl_runtime, "bot_actions", lambda: actions)
    monkeypatch.setattr("services.get_ocr_client", lambda: ocr)
    monkeypatch.setattr("services.get_repo_root", lambda: MODULE_DIR)
    monkeypatch.setattr("services.get_active_module_catalog", lambda: None)
    monkeypatch.setattr("layout.area_manifest.load_area_doc", lambda *_a, **_k: {"screens": [1]})
    monkeypatch.setattr(
        "layout.area_lookup.screen_region_by_name",
        lambda *_a, **_k: (
            "intel.stamina",
            {"bbox": {"x": 21.6, "y": 93.5, "width": 18.3, "height": 5.15}},
        ),
    )


def _ctx(redis: Any) -> DslExecContext:
    return DslExecContext(redis_client=redis, player_id="p1", instance_id="i1", args={})


@pytest.mark.asyncio
async def test_retry_takes_first_clean_max(monkeypatch) -> None:
    # First two frames are corrupted; the third reads clean → store both.
    ocr = _SeqOcr(["43/710", "43/10", "43/70"])
    actions = _Actions()
    _wire(monkeypatch, ocr, actions)
    redis = _Redis()

    ctx = _ctx(redis)
    await EXEC._exec_read_intel_stamina(ctx)

    assert ctx.result["action"] == "measured"
    assert ctx.result["stamina"] == 43
    assert ctx.result["stamina_max"] == 70
    assert ctx.result["max_stable"] is True
    assert redis.hashes["wos:player:p1:state"]["stamina_max"] == "70"
    assert ocr.calls == 3  # retried until clean


@pytest.mark.asyncio
async def test_all_corrupt_stores_current_keeps_old_max(monkeypatch) -> None:
    # Every frame mangles the max → store current, leave stamina_max untouched.
    ocr = _SeqOcr(["43/710", "43/10", "43/810"])
    actions = _Actions()
    _wire(monkeypatch, ocr, actions)
    # A previously-known good max must survive.
    redis = _Redis()
    redis.hashes["wos:player:p1:state"] = {"stamina_max": "70"}

    ctx = _ctx(redis)
    await EXEC._exec_read_intel_stamina(ctx)

    assert ctx.result["action"] == "measured"
    assert ctx.result["stamina"] == 43
    assert ctx.result["max_stable"] is False
    state = redis.hashes["wos:player:p1:state"]
    assert state["stamina"] == "43"          # current updated
    assert state["stamina_max"] == "70"      # old max preserved, NOT 710/10/810


@pytest.mark.asyncio
async def test_parse_failed_when_never_matches(monkeypatch) -> None:
    ocr = _SeqOcr(["MAX:", "", "ter a."])
    actions = _Actions()
    _wire(monkeypatch, ocr, actions)
    redis = _Redis()

    ctx = _ctx(redis)
    await EXEC._exec_read_intel_stamina(ctx)

    assert ctx.result["action"] == "parse_failed"
    assert "wos:player:p1:state" not in redis.hashes
