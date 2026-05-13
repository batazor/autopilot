"""``_append_trace_row`` enriches every row with diagnostic fields so the
"step failed" line in the UI carries the numbers the operator needs to
reason about *why* it failed — match score vs threshold, OCR text/value,
duration, scenario-relative time.

These tests pin the auto-extraction directly on the mixin's appender so
the enrichment contract survives refactors of the surrounding executor.
"""

from __future__ import annotations

import time
from typing import Any

from tasks.dsl_persist_mixin import DslPersistMixin


class _Host(DslPersistMixin):
    """Minimal harness — every attribute the mixin reads is right here so
    the test isn't coupled to ``DslScenarioTask``'s full dataclass."""

    def __init__(self, *, with_timing: bool = True) -> None:
        self.redis_client = None
        self.player_id = ""
        self._steps_trace: list[dict[str, Any]] = []
        self._scenario_started_at = time.time() if with_timing else None
        self._step_start_times = {} if with_timing else None


def test_append_extracts_region_from_action_key() -> None:
    """The operative ``region`` is auto-derived from the step's action key
    so every row gets it without each call site threading it through."""
    h = _Host()
    h._append_trace_row(0, {"click": "btn.confirm"}, "ok")
    assert h._steps_trace[0]["region"] == "btn.confirm"

    h2 = _Host()
    h2._append_trace_row(1, {"while_match": "popup.close", "max": 3}, "ok")
    assert h2._steps_trace[0]["region"] == "popup.close"


def test_append_flattens_match_row_into_score_and_coords() -> None:
    """``match_row=`` kwarg fans out into ``match_score`` + bbox/tap fields —
    so a failed match shows the actual score, not just "stopped"."""
    h = _Host()
    match_row = {
        "score": 0.7123,
        "matched": False,
        "top_left": [120, 240],
        "template_w": 64,
        "template_h": 32,
        "tap_x_pct": 30.0,
        "tap_y_pct": 60.0,
        "reason": "score_below_threshold",
    }
    h._append_trace_row(
        2, {"match": "btn.upgrade", "threshold": 0.92}, "stopped",
        reason="match_failed", match_row=match_row,
    )
    row = h._steps_trace[0]
    assert row["match_score"] == 0.7123
    assert row["matched"] is False
    assert row["top_left"] == [120, 240]
    assert row["template_w"] == 64
    assert row["template_h"] == 32
    assert row["tap_x_pct"] == 30.0
    assert row["tap_y_pct"] == 60.0
    assert row["match_detail"] == "score_below_threshold"
    # Step-level threshold is preserved next to the match score so the
    # comparison the operator wants to do is right on one row.
    assert row["threshold"] == 0.92
    # Explicit kwarg ``reason`` wins over the match row's ``reason``.
    assert row["reason"] == "match_failed"


def test_append_flattens_ocr_row_into_text_and_confidence() -> None:
    """``ocr_row=`` carries the audit snapshot ``_ocr_audit_step`` writes —
    so a ``low_confidence`` skip shows the actual reading."""
    h = _Host()
    ocr_row = {
        "region": "page.heroes.unit.name",
        "store": "page.heroes.unit.name",
        "status": "low_confidence",
        "threshold": 0.85,
        "confidence": 0.71,
        "text": "Cl0ris",
        "value": "Cl0ris",
    }
    h._append_trace_row(3, {"ocr": "page.heroes.unit.name"}, "ok", ocr_row=ocr_row)
    row = h._steps_trace[0]
    assert row["ocr_text"] == "Cl0ris"
    assert row["ocr_value"] == "Cl0ris"
    assert row["ocr_confidence"] == 0.71
    assert row["ocr_status"] == "low_confidence"
    assert row["threshold"] == 0.85
    assert row["region"] == "page.heroes.unit.name"


def test_append_stamps_scenario_relative_time() -> None:
    """``t`` (seconds from scenario start) lets the UI plot a wall-clock
    timeline without each row carrying an absolute timestamp."""
    h = _Host()
    h._scenario_started_at = time.time() - 1.5
    h._append_trace_row(0, {"wait": "1s"}, "ok")
    assert 1.0 <= h._steps_trace[0]["t"] <= 3.0


def test_append_stamps_duration_ms_on_terminal_top_level_rows() -> None:
    """Terminal rows at a top-level index get ``duration_ms`` so the
    UI shows "this step took 1.2s" without diffing consecutive rows.

    Nested rows (``i`` containing ``.``) are skipped — only the outermost
    step gets one duration stamp."""
    h = _Host()
    # Simulate a step starting "earlier" by pre-seeding the start time.
    h._step_start_times["5"] = time.time() - 0.42
    h._append_trace_row(5, {"click": "btn"}, "ok")
    row = h._steps_trace[0]
    assert "duration_ms" in row
    assert 300 <= row["duration_ms"] <= 800
    # Nested rows don't get duration — they live under their parent index.
    h._step_start_times["6"] = time.time() - 0.1
    h._append_trace_row("6.0.0", {"click": "btn"}, "ok")
    assert "duration_ms" not in h._steps_trace[1]


def test_append_skips_duration_when_timing_not_initialized() -> None:
    """Tests that exercise the appender without an active scenario (older
    fixtures) must not crash; ``t`` and ``duration_ms`` simply absent."""
    h = _Host(with_timing=False)
    h._append_trace_row(0, {"click": "x"}, "ok")
    row = h._steps_trace[0]
    assert "t" not in row
    assert "duration_ms" not in row
    # Other auto-extractions still work.
    assert row["region"] == "x"


def test_iter_rows_do_not_consume_step_start_time() -> None:
    """``iter`` marker rows live inside while_match/loop bodies and must
    not start the duration clock for the parent step — otherwise the
    parent's later "ok" row would report iter-to-finish time, not full
    step time."""
    h = _Host()
    h._append_trace_row(4, None, "iter", summary="iter 0")
    # No start time registered for top-level index "4" yet.
    assert "4" not in h._step_start_times
    # Now the real start.
    h._step_start_times["4"] = time.time() - 0.05
    h._append_trace_row(4, {"while_match": "claim"}, "ok", iterations=3)
    assert "duration_ms" in h._steps_trace[1]
