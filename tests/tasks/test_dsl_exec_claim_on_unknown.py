"""Tests for ``exec: claim_on_unknown`` — OCR-locate + press a Claim button."""
from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from conftest import make_actions

import tasks.dsl_exec as dsl_exec
from ocr.word_boxes import WordBox
from tasks import dsl_runtime
from tasks.dsl_exec import claim_on_unknown
from tasks.dsl_exec.claim_on_unknown import (
    _looks_like_purchase,
    _select_claim_target,
)

_VOCAB = claim_on_unknown._DEFAULT_CLAIM_CUES


def _box(text: str, *, cy: int, conf: float = 0.9, cx: int = 360) -> WordBox:
    # height/width arbitrary; centre is what selection uses.
    return WordBox(text=text, conf=conf, left=cx - 40, top=cy - 20, width=80, height=40)


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_looks_like_purchase_detects_price_and_currency() -> None:
    assert _looks_like_purchase("Special Offer $4.99 only")
    assert _looks_like_purchase("Pay 1,99 now")
    assert _looks_like_purchase("Buy this pack")
    assert _looks_like_purchase("€ 9")
    assert not _looks_like_purchase("Labyrinth Treasure Claim")


def test_select_claim_target_matches_label() -> None:
    boxes = [_box("Labyrinth", cy=300), _box("Treasure", cy=320), _box("Claim", cy=1100)]
    target = _select_claim_target(boxes, _VOCAB, min_conf=0.45)
    assert target is not None and target.text == "Claim"


def test_select_claim_target_prefers_bottom_button() -> None:
    # A body-text "claim" mention up high and the real button low — pick the low one.
    boxes = [_box("claim", cy=200), _box("Claim", cy=1100)]
    target = _select_claim_target(boxes, _VOCAB, min_conf=0.45)
    assert target is not None and target.cy == 1100


def test_select_claim_target_matches_russian() -> None:
    boxes = [_box("Получить", cy=1080)]
    target = _select_claim_target(boxes, _VOCAB, min_conf=0.45)
    assert target is not None and target.text == "Получить"


def test_select_claim_target_none_when_no_button() -> None:
    boxes = [_box("Details", cy=600), _box("Source", cy=620)]
    assert _select_claim_target(boxes, _VOCAB, min_conf=0.45) is None


def test_select_claim_target_skips_low_conf() -> None:
    boxes = [_box("Claim", cy=1100, conf=0.20)]
    assert _select_claim_target(boxes, _VOCAB, min_conf=0.45) is None


# ── handler ───────────────────────────────────────────────────────────────────


class _FakeWordOcr:
    """Returns a queued list of word-box sets, one per ``detect_word_boxes`` call."""

    def __init__(self, frames: list[list[WordBox]]) -> None:
        self._frames = iter(frames)

    def detect_word_boxes(self, _image: Any, **_kwargs: object) -> list[WordBox]:
        return next(self._frames, [])


def _recording_actions() -> Any:
    taps: list[tuple[int, int, Any, Any]] = []
    actions = make_actions(resolution=(720, 1280))
    actions.capture_screen_bgr.return_value = np.zeros((1280, 720, 3), dtype=np.uint8)

    def _tap(_instance_id: str, point: Any, **kwargs: object) -> bool:
        taps.append(
            (point.x, point.y, kwargs.get("approval_region"), kwargs.get("approval_source"))
        )
        return True

    actions.tap.side_effect = _tap
    actions._test_taps = taps  # type: ignore[attr-defined]
    return actions


def _wire(mocker, actions: Any, ocr: _FakeWordOcr) -> None:
    mocker.patch.object(dsl_runtime, "bot_actions", return_value=actions)
    mocker.patch.object(dsl_runtime, "ocr_client", return_value=ocr)
    mocker.patch.object(claim_on_unknown, "_SETTLE_S", 0)


def _ctx(args: dict[str, Any] | None = None) -> dsl_exec.DslExecContext:
    return dsl_exec.DslExecContext(
        redis_client=None,
        player_id="",
        instance_id="bs3",
        args=args or {},
    )


@pytest.mark.asyncio
async def test_claim_taps_button_then_clears(mocker) -> None:
    actions = _recording_actions()
    # Pass 1: a Claim button. Pass 2: nothing left → claimed.
    ocr = _FakeWordOcr([[_box("Claim", cy=1100, cx=360)], []])
    _wire(mocker, actions, ocr)

    ctx = _ctx()
    await dsl_exec.DSL_EXEC_REGISTRY["claim_on_unknown"](ctx)

    assert actions._test_taps == [(360, 1100, "button.claim", "claim_on_unknown")]  # type: ignore[attr-defined]
    assert ctx.result["reason"] == "claimed"
    assert ctx.result["claim_claimed"] == 1


@pytest.mark.asyncio
async def test_claim_purchase_guard_never_taps(mocker) -> None:
    actions = _recording_actions()
    ocr = _FakeWordOcr([[_box("Claim", cy=1100), _box("$4.99", cy=900)]])
    _wire(mocker, actions, ocr)

    ctx = _ctx()
    await dsl_exec.DSL_EXEC_REGISTRY["claim_on_unknown"](ctx)

    assert actions._test_taps == []  # type: ignore[attr-defined]
    assert ctx.result["reason"] == "purchase_guard"


@pytest.mark.asyncio
async def test_claim_no_button_reports_none(mocker) -> None:
    actions = _recording_actions()
    ocr = _FakeWordOcr([[_box("Details", cy=600)]])
    _wire(mocker, actions, ocr)

    ctx = _ctx()
    await dsl_exec.DSL_EXEC_REGISTRY["claim_on_unknown"](ctx)

    assert actions._test_taps == []  # type: ignore[attr-defined]
    assert ctx.result["reason"] == "no_claim_button"


@pytest.mark.asyncio
async def test_claim_rejected_tap_aborts(mocker) -> None:
    actions = _recording_actions()
    actions.tap.side_effect = None
    actions.tap.return_value = False  # operator rejects
    ocr = _FakeWordOcr([[_box("Claim", cy=1100)], [_box("Claim", cy=1100)]])
    _wire(mocker, actions, ocr)

    ctx = _ctx()
    await dsl_exec.DSL_EXEC_REGISTRY["claim_on_unknown"](ctx)

    assert actions.tap.call_count == 1
    assert ctx.result["reason"] == "tap_rejected"


@pytest.mark.asyncio
async def test_claim_respects_max_taps(mocker) -> None:
    actions = _recording_actions()
    # Every pass keeps showing a Claim button (never clears).
    ocr = _FakeWordOcr([[_box("Claim", cy=1100)] for _ in range(10)])
    _wire(mocker, actions, ocr)

    ctx = _ctx({"max_taps": 2})
    await dsl_exec.DSL_EXEC_REGISTRY["claim_on_unknown"](ctx)

    assert len(actions._test_taps) == 2  # type: ignore[attr-defined]
    assert ctx.result["reason"] == "max_taps"
