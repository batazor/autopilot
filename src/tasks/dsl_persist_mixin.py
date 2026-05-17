"""Lifecycle / state-persist methods for ``DslScenarioTask``.

These thin helpers push scenario-runtime state to Redis hashes (so UI panels —
click approvals, debug, queue — can introspect what the worker is doing) and
the per-player state store. The host class supplies:

- ``redis_client`` — async redis or ``None``
- ``player_id`` — current player binding

External callers should still import ``DslScenarioTask`` from
``tasks.dsl_scenario``; this module is internal.
"""
from __future__ import annotations

import logging
import time
from contextlib import suppress
from typing import Any

from config.state_store import get_state_store
from tasks.dsl_scenario_helpers import _dsl_step_summary

logger = logging.getLogger(__name__)


_TERMINAL_TRACE_STATUSES: frozenset[str] = frozenset(
    {"ok", "stopped", "skipped", "skipped_empty", "early_exit", "failed"}
)
"""Statuses that close out a top-level step. Rows tagged with one of these
get a ``duration_ms`` stamp; other statuses (``iter``, etc.) don't."""

_REGION_KEYS_IN_PRIORITY_ORDER: tuple[str, ...] = (
    "click",
    "long_click",
    "match",
    "while_match",
    "ocr",
)
"""Keys to scan on a step dict to derive the operative ``region`` for the
trace row. First non-empty wins — the runtime evaluates these in the same
order, so the same convention keeps the trace honest."""


def _trace_enrich_with_match_row(row: dict[str, Any], match_row: dict[str, Any]) -> None:
    """Flatten the dict returned by ``_match_region`` into trace fields.

    Surfaces ``match_score`` (the headline number a "low_match" failure
    hinges on) plus the bbox/tap coordinates UI panels need to render a
    crop later. ``setdefault`` everywhere so explicit kwargs to
    ``_append_trace_row`` still win.
    """
    sc = match_row.get("score")
    if isinstance(sc, (int, float)):
        row.setdefault("match_score", round(float(sc), 4))
    if "matched" in match_row:
        row.setdefault("matched", bool(match_row.get("matched")))
    tl = match_row.get("top_left")
    if isinstance(tl, (list, tuple)) and len(tl) >= 2:
        with suppress(TypeError, ValueError):
            row.setdefault("top_left", [int(float(tl[0])), int(float(tl[1]))])
    for k in (
        "template_w",
        "template_h",
        "tap_x_pct",
        "tap_y_pct",
        "tap_match_x_pct",
        "tap_match_y_pct",
        "search_region",
    ):
        v = match_row.get(k)
        if v is not None:
            row.setdefault(k, v)
    detail = match_row.get("reason")
    if isinstance(detail, str) and detail.strip():
        row.setdefault("match_detail", detail.strip())


def _trace_enrich_with_ocr_row(row: dict[str, Any], ocr_row: dict[str, Any]) -> None:
    """Flatten an OCR snapshot (``_persist_ocr_result``'s view) into trace fields.

    ``ocr_text`` / ``ocr_value`` distinguish raw OCR output from the typed /
    parsed value the scenario actually stored. ``ocr_confidence`` paired with
    ``threshold`` answers "step failed because confidence X < threshold Y".
    """
    for src, dst in (
        ("text", "ocr_text"),
        ("value", "ocr_value"),
        ("confidence", "ocr_confidence"),
    ):
        v = ocr_row.get(src)
        if v is not None:
            row.setdefault(dst, v)
    thr = ocr_row.get("threshold")
    if isinstance(thr, (int, float)):
        row.setdefault("threshold", float(thr))
    status_ocr = ocr_row.get("status")
    if isinstance(status_ocr, str) and status_ocr.strip():
        row.setdefault("ocr_status", status_ocr.strip())


class DslPersistMixin:
    redis_client: Any
    player_id: str | None
    # Shared trace state owned by ``DslScenarioExecuteMixin._execute`` —
    # nested handlers in ``DslScenarioInlineMixin`` append to the same list
    # via :meth:`_append_trace_row`.
    _steps_trace: list[dict[str, Any]] | None
    # Scenario-relative timing — :meth:`DslScenarioExecuteMixin.execute`
    # seeds ``_scenario_started_at`` and an empty ``_step_start_times`` at
    # scenario boot so every appended trace row carries ``t`` and (for
    # terminal top-level rows) ``duration_ms``. Out of an active scenario
    # the attributes may be ``None``; the appender tolerates that.
    _scenario_started_at: float | None
    _step_start_times: dict[str, float] | None

    def _append_trace_row(
        self,
        i: Any,
        step_obj: Any,
        status: str,
        **kw: Any,
    ) -> None:
        """Append one row to the active scenario's ``steps_trace``.

        Safe to call from any code path during ``_execute()``. No-op when the
        trace list isn't initialized (e.g. outside an active ``_execute``).

        Auto-enriches the row with:

        - ``t`` — seconds since scenario start (set by execute mixin).
        - ``duration_ms`` — wall-clock time spent on the step, stamped on the
          terminal row of each top-level index (``i`` has no ``.`` separators).
        - ``region`` — the operative area-region from the step's action key.
        - ``threshold`` — copied from ``step.threshold`` if the YAML set one.

        Two special kwargs ``match_row=`` / ``ocr_row=`` accept a dict from
        ``_match_region`` / ``_persist_ocr_result`` and flatten it into
        ``match_score`` / ``ocr_value`` / etc. so the UI sees the actual
        numbers a "step failed" diagnosis hinges on, without each call site
        threading individual fields through manually.
        """
        trace = getattr(self, "_steps_trace", None)
        if not isinstance(trace, list):
            return
        # Allow callers to override the summary (e.g. "iter 0" markers that
        # don't correspond to a real DSL step dict).
        summary_override = kw.pop("summary", None)
        match_row = kw.pop("match_row", None)
        ocr_row = kw.pop("ocr_row", None)
        if summary_override is not None:
            summ = str(summary_override)
        else:
            summ = (
                _dsl_step_summary(step_obj)
                if isinstance(step_obj, dict)
                else "(non-dict)"
            )
        i_s = str(i)
        row: dict[str, Any] = {"i": i_s, "summary": summ, "status": status}

        now = time.time()
        started_at = getattr(self, "_scenario_started_at", None)
        if isinstance(started_at, (int, float)):
            row["t"] = round(now - float(started_at), 3)

        # Per-step wall-clock timer. Nested iters share the parent's top-level
        # index (e.g. while_match at ``i=6`` spawns rows at ``6.0``, ``6.0.0``)
        # — only stamp duration on rows with a bare integer ``i`` so we don't
        # double-count inside the loop body.
        is_top_level = i_s and "." not in i_s
        starts = getattr(self, "_step_start_times", None)
        if is_top_level and isinstance(starts, dict):
            if status != "iter" and i_s not in starts:
                starts[i_s] = now
            if status in _TERMINAL_TRACE_STATUSES and i_s in starts:
                row["duration_ms"] = int(round((now - starts.pop(i_s)) * 1000))

        if isinstance(step_obj, dict):
            for k in _REGION_KEYS_IN_PRIORITY_ORDER:
                v = step_obj.get(k)
                if isinstance(v, str) and v.strip():
                    row.setdefault("region", v.strip())
                    break
            step_thr = step_obj.get("threshold")
            if isinstance(step_thr, (int, float)):
                row.setdefault("threshold", float(step_thr))

        if isinstance(match_row, dict):
            _trace_enrich_with_match_row(row, match_row)
        if isinstance(ocr_row, dict):
            _trace_enrich_with_ocr_row(row, ocr_row)

        # Explicit caller kwargs still win — they override auto-extracted
        # values so per-step overrides (e.g. ``threshold=…`` set inline) stick.
        for k, v in kw.items():
            if v is not None:
                row[k] = v
        trace.append(row)

    async def _write_step_context(self, instance_id: str, *, scenario: str) -> None:
        if self.redis_client is None:
            return
        with suppress(Exception):
            await self.redis_client.hset(
                f"wos:instance:{instance_id}:state",
                mapping={"current_scenario": scenario},
            )

    async def _clear_step_context(self, instance_id: str) -> None:
        if self.redis_client is None:
            return
        with suppress(Exception):
            await self.redis_client.hset(
                f"wos:instance:{instance_id}:state",
                mapping={
                    "current_scenario": "",
                    "last_active_scenario": "",
                    "last_active_scenario_priority": "",
                    "last_active_scenario_player": "",
                    "last_active_scenario_step": "",
                    "last_active_scenario_trace": "",
                    "nav_target": "",
                },
            )

    async def _reset_dsl_audit_state(self, instance_id: str) -> None:
        """Wipe the per-step audit snapshot (``dsl_last_match`` / ``dsl_last_ocr`` /
        ``dsl_last_color``) at scenario start.

        Without this, the click-approvals UI keeps showing the *previous* scenario's
        guard outcome until the new scenario runs its own step — which makes the
        inspector look like it's lagging by one scenario. We deliberately wipe at
        scenario START rather than END so the fields survive past the scenario
        boundary for post-mortem debugging until the next task picks up.
        """
        if self.redis_client is None:
            return
        with suppress(Exception):
            await self.redis_client.hset(
                f"wos:instance:{instance_id}:state",
                mapping={
                    "dsl_last_match_region": "",
                    "dsl_last_match_score": "",
                    "dsl_last_match_threshold": "",
                    "dsl_last_match_matched": "",
                    "dsl_last_match_detail": "",
                    "dsl_last_match_at": "",
                    "dsl_last_match_search_region": "",
                    "dsl_last_match_top_left_x": "",
                    "dsl_last_match_top_left_y": "",
                    "dsl_last_match_template_w": "",
                    "dsl_last_match_template_h": "",
                    "dsl_last_match_tap_x_pct": "",
                    "dsl_last_match_tap_y_pct": "",
                    "dsl_last_match_tap_match_x_pct": "",
                    "dsl_last_match_tap_match_y_pct": "",
                    "dsl_last_ocr_region": "",
                    "dsl_last_ocr_store": "",
                    "dsl_last_ocr_status": "",
                    "dsl_last_ocr_threshold": "",
                    "dsl_last_ocr_confidence": "",
                    "dsl_last_ocr_raw_text": "",
                    "dsl_last_ocr_value": "",
                    "dsl_last_ocr_at": "",
                    "dsl_last_color_region": "",
                    "dsl_last_color_status": "",
                    "dsl_last_color_want": "",
                    "dsl_last_color_dominant": "",
                    "dsl_last_color_share": "",
                    "dsl_last_color_threshold": "",
                    "dsl_last_color_at": "",
                },
            )

    async def _persist_dsl_last_color(self, instance_id: str, mapping: dict[str, str]) -> None:
        """Expose last ``color_check:`` step outcome on instance Redis hash for UI/debug."""
        if self.redis_client is None:
            return
        full = dict(mapping)
        full["dsl_last_color_at"] = str(time.time())
        try:
            await self.redis_client.hset(f"wos:instance:{instance_id}:state", mapping=full)
        except Exception:
            logger.debug("dsl_scenario: persist dsl_last_color failed", exc_info=True)

    def _state_flat(self) -> dict[str, Any] | None:
        """Flat per-player state for version-aware region lookup.

        Returns ``None`` (default-version semantics) when no player is bound or
        the state store is unreachable, so a missing/broken state never breaks
        region resolution — it just falls back to default regions.
        """
        pid = str(self.player_id or "").strip()
        if not pid:
            return None
        try:
            store = get_state_store().get_or_create(pid)
            return store.to_flat_dict()
        except Exception as exc:  # noqa: BLE001 — diagnostic, fallback to default
            logger.debug("dsl: _state_flat fallback for player=%s: %s", pid, exc)
            return None
