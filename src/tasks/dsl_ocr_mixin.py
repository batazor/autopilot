"""OCR-related methods for ``DslScenarioTask``.

Pulled out of ``tasks/dsl_scenario.py`` to keep the host file focused. The mixin
relies on these instance attributes provided by the host:

- ``redis_client`` — async redis or ``None``
- ``player_id`` — current player binding (may be reassigned by ``ocr: player_id``)
- ``_ocr_client`` — lazy singleton OCR client
- ``_state_flat()`` — flat per-player state dict for version-aware lookups

External callers should still import ``DslScenarioTask`` from
``tasks.dsl_scenario``; this module is internal.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from config.event_timers import store_event_timer
from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from layout.types import Region
from ocr.preprocess import parse_digit_count, resolve_preprocess
from tasks.dsl_scenario_helpers import (
    _event_timer_name_from_spec,
    _parse_hms_to_seconds,
    _read_active_player,
    _read_current_screen,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from adb import BotActions
    from tasks._dsl_task_host import _DslTaskHost as _Base
else:
    _Base = object

def parse_ocr_integer(text: str) -> int | None:
    """Strip non-digits from ``text`` and return the int — None if no digits.

    Game UI labels OCR as ``"1,234,567"`` / ``"1 234 567"`` / ``"12345"``;
    the digit-only pass handles thousands separators uniformly.
    """
    digits = re.sub(r"\D+", "", str(text or ""))
    if not digits:
        return None
    return int(digits)


# OCR may reuse a recent framebuffer instead of issuing a fresh ADB screencap
# when sibling ``match`` / ``while_match`` steps just warmed the per-instance
# cache. Capped at 300 ms so timer/countdown reads still see a fresh frame —
# longer than that and a HH:MM:SS region could drift by a full second.
# Explicit ``wait:`` steps additionally invalidate the cache (see the
# ``dsl_scenario_*_mixin`` wait paths), so a deliberate pause never serves
# the OCR a pre-pause frame.
_OCR_FRAME_CACHE_MAX_AGE_MS: float = 300.0


def _safe_float_or_none(s: str) -> float | None:
    """Best-effort parse of the stringified floats used by ``_ocr_audit_step``.

    Returns ``None`` for empty / unparseable strings so trace consumers can
    tell "no value" from "value 0.0".
    """
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class DslOcrMixin(_Base):
    redis_client: Any
    player_id: str | None
    _ocr_client: Any
    # Snapshot of the latest ``ocr:`` step result so ``_append_trace_row``
    # can flatten it into the trace via ``ocr_row=self._last_ocr_row``. Set
    # by ``_ocr_audit_step`` (the universal exit point — both failure paths
    # and ``stored`` go through it).
    _last_ocr_row: dict[str, Any] | None

    def _state_flat(self) -> dict[str, Any] | None: ...  # provided by host

    async def _persist_dsl_last_ocr(self, instance_id: str, mapping: dict[str, str]) -> None:
        """Expose last ``ocr:`` step outcome on instance Redis hash for Click approvals UI."""
        if self.redis_client is None:
            return
        full = dict(mapping)
        full["dsl_last_ocr_at"] = str(time.time())
        try:
            await self.redis_client.hset(f"wos:instance:{instance_id}:state", mapping=full)
        except Exception:
            logger.debug("dsl_scenario: persist dsl_last_ocr failed", exc_info=True)

    def _get_ocr_client(self) -> Any:
        if self._ocr_client is None:
            from services import get_ocr_client

            self._ocr_client = get_ocr_client()
        return self._ocr_client

    @staticmethod
    def _cached_capture(actions: BotActions, instance_id: str) -> Any:
        """Capture via ``capture_screen_bgr_cached`` with the OCR staleness gate.

        Test fakes that predate the cache helper still expose only
        ``capture_screen_bgr`` — fall back transparently so unit tests aren't
        forced to mock the cached entry point. Production ``BotActions`` always
        provides both.
        """
        cached_capture = getattr(actions, "capture_screen_bgr_cached", None)
        if cached_capture is None:
            return actions.capture_screen_bgr(instance_id)
        return cached_capture(instance_id, max_age_ms=_OCR_FRAME_CACHE_MAX_AGE_MS)

    async def _ocr_audit_step(
        self,
        instance_id: str,
        *,
        region: str,
        step: dict[str, Any],
        status: str,
        threshold_s: str = "",
        confidence_s: str = "",
        raw_text: str = "",
        value_s: str = "",
    ) -> None:
        planned_store = str(step.get("store") or region).strip()
        # Cache the result for ``_append_trace_row`` consumers (inline mixin
        # passes this as ``ocr_row=`` so the trace shows confidence/value/text
        # right next to the failure status). Every ocr exit funnels through
        # here so this snapshot is always the latest one.
        self._last_ocr_row = {
            "region": region,
            "store": planned_store,
            "status": status,
            "threshold": _safe_float_or_none(threshold_s),
            "confidence": _safe_float_or_none(confidence_s),
            "text": raw_text,
            "value": value_s,
        }
        await self._persist_dsl_last_ocr(
            instance_id,
            {
                "dsl_last_ocr_region": region,
                "dsl_last_ocr_store": planned_store,
                "dsl_last_ocr_status": status,
                "dsl_last_ocr_threshold": threshold_s,
                "dsl_last_ocr_confidence": confidence_s,
                "dsl_last_ocr_raw_text": raw_text,
                "dsl_last_ocr_value": value_s,
            },
        )

    async def _ocr_region(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
    ) -> None:
        """OCR a named region and persist the result.

        Step shape::

            - ocr: <region_name>
              store: <field>          # Redis player/instance hash. Ephemeral —
                                      # used to share data between scenario steps
                                      # via cond/match text matches.
              state: <dotted.path>    # SQLite state dot-path. Long-lived; drives
                                      # arithmetic ``cond`` (e.g.
                                      # ``exploration.state.myPower * 1.2 >= ...``).
              scope: player|instance  # default = "player" (applies to ``store:``)
              type: integer|string|time   # default = inherits area.json `type`
                                          # ``time`` parses HH:MM:SS / MM:SS to total seconds.
              throttle_push: <scenario>   # optional; with ``type: time`` writes a
                                          # push_ttl marker (TTL = parsed seconds) so
                                          # any overlay push or `push_scenario:` for
                                          # ``<scenario>`` is dropped until the marker
                                          # expires. Pairs with the chapter-task idiom
                                          # "building is upgrading → don't re-fire".
              threshold: 0.7          # confidence floor; default = inherits area.json `threshold`

        ``store:`` and ``state:`` are independent — set either, both, or neither.
        If neither is given, the legacy default ``store: <region_name>`` is used.
        Low-confidence reads are logged and skipped, never persisted to either side.
        """
        # Discard any prior step's snapshot so the inline-mixin trace appender
        # doesn't paint this row with stale values when the OCR call bails on
        # an early gate (``region_not_found`` / ``invalid_bbox`` / etc.).
        self._last_ocr_row = None
        current_screen = await _read_current_screen(instance_id, self.redis_client)
        pair = (
            screen_region_by_name(
                area_doc,
                region,
                state_flat=self._state_flat(),
                screen_id=current_screen or None,
            )
            if region
            else None
        )
        if pair is None or not isinstance(pair[1].get("bbox"), dict):
            logger.warning(
                "dsl_scenario: ocr region not found in area.json: %s (scenario=%s)",
                region,
                _scen(scenario_key),
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="region_not_found"
            )
            return

        region_def = pair[1]
        bbox = region_def["bbox"]
        try:
            px = int(round(float(bbox["x"]) / 100.0 * dev_w))
            py = int(round(float(bbox["y"]) / 100.0 * dev_h))
            pw = int(round(float(bbox["width"]) / 100.0 * dev_w))
            ph = int(round(float(bbox["height"]) / 100.0 * dev_h))
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "dsl_scenario: invalid ocr bbox for region %s (scenario=%s)",
                region,
                _scen(scenario_key),
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="invalid_bbox"
            )
            return
        if pw <= 0 or ph <= 0:
            logger.warning(
                "dsl_scenario: ocr region has zero size: %s (scenario=%s)",
                region,
                _scen(scenario_key),
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="zero_bbox"
            )
            return

        try:
            image = await asyncio.to_thread(
                self._cached_capture, actions, instance_id
            )
        except Exception:
            logger.exception(
                "dsl_scenario: capture_screen_bgr failed for ocr (scenario=%s region=%s)",
                _scen(scenario_key),
                region,
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="capture_failed"
            )
            return

        # ``preprocess:`` selects the backend pipeline. Step wins, then
        # area.json region, then a ``type:``-derived default
        # ``time`` / ``int`` / ``integer`` → Tesseract ``fast_line``.
        preprocess = resolve_preprocess(
            explicit=step.get("preprocess") or region_def.get("preprocess"),
            type_hint=step.get("type") or region_def.get("type"),
        )
        raw_digit_count = step.get("digit_count", region_def.get("digit_count"))
        digit_count = parse_digit_count(raw_digit_count)
        try:
            digit_x0 = int(step.get("digit_x0", region_def.get("digit_x0", 0)) or 0)
        except (TypeError, ValueError):
            digit_x0 = 0

        try:
            result = await self._get_ocr_client().ocr_region(
                image,
                Region(px, py, pw, ph),
                region_id=region,
                preprocess=preprocess,
                digit_count=digit_count,
                digit_x0=digit_x0,
            )
        except Exception:
            logger.exception(
                "dsl_scenario: OCR call failed (scenario=%s region=%s)",
                _scen(scenario_key),
                region,
            )
            await self._ocr_audit_step(
                instance_id, region=region, step=step, status="ocr_call_failed"
            )
            return

        await self._persist_ocr_result(
            instance_id=instance_id,
            scenario_key=scenario_key,
            step=step,
            region=region,
            region_def=region_def,
            result=result,
        )

    async def _ocr_region_bulk(
        self,
        *,
        actions: BotActions,
        area_doc: dict[str, Any],
        instance_id: str,
        dev_w: int,
        dev_h: int,
        scenario_key: str,
        steps: list[dict[str, Any]],
    ) -> None:
        requests: list[tuple[dict[str, Any], str, dict[str, Any], Region]] = []

        current_screen = await _read_current_screen(instance_id, self.redis_client)
        for step in steps:
            region = str(step.get("ocr") or "").strip()
            if not region:
                continue
            pair = screen_region_by_name(
                area_doc,
                region,
                state_flat=self._state_flat(),
                screen_id=current_screen or None,
            )
            if pair is None or not isinstance(pair[1].get("bbox"), dict):
                logger.warning(
                    "dsl_scenario: ocr region not found in area.json: %s (scenario=%s)",
                    region,
                    _scen(scenario_key),
                )
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="region_not_found"
                )
                continue

            region_def = pair[1]
            bbox = region_def["bbox"]
            try:
                px = int(round(float(bbox["x"]) / 100.0 * dev_w))
                py = int(round(float(bbox["y"]) / 100.0 * dev_h))
                pw = int(round(float(bbox["width"]) / 100.0 * dev_w))
                ph = int(round(float(bbox["height"]) / 100.0 * dev_h))
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "dsl_scenario: invalid ocr bbox for region %s (scenario=%s)",
                    region,
                    _scen(scenario_key),
                )
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="invalid_bbox"
                )
                continue
            if pw <= 0 or ph <= 0:
                logger.warning(
                    "dsl_scenario: ocr region has zero size: %s (scenario=%s)",
                    region,
                    _scen(scenario_key),
                )
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="zero_bbox"
                )
                continue
            requests.append((step, region, region_def, Region(px, py, pw, ph)))

        if not requests:
            return

        try:
            image = await asyncio.to_thread(
                self._cached_capture, actions, instance_id
            )
        except Exception:
            logger.exception(
                "dsl_scenario: capture_screen_bgr failed for bulk ocr "
                "(scenario=%s regions=%s)",
                _scen(scenario_key),
                [region for _step, region, _def, _px in requests],
            )
            for step, region, _region_def, _region_px in requests:
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="capture_failed"
                )
            return

        # Per-step ``preprocess:`` resolution mirrors the single-region path:
        # step wins over the area.json region, then ``type:`` auto-derives
        # ``fast_line`` for timer / integer regions. The whole list is only
        # forwarded when at least one entry is non-empty, so a backend that
        # predates the field doesn't see an unknown key on every batch.
        bulk_preprocess: list[str | None] = [
            resolve_preprocess(
                explicit=step.get("preprocess") or region_def.get("preprocess"),
                type_hint=step.get("type") or region_def.get("type"),
            )
            for step, _region, region_def, _region_px in requests
        ]
        bulk_digit_count: list[int | None] = [
            parse_digit_count(step.get("digit_count", region_def.get("digit_count")))
            for step, _region, region_def, _region_px in requests
        ]
        bulk_digit_x0: list[int] = []
        for step, _region, region_def, _region_px in requests:
            try:
                bulk_digit_x0.append(
                    int(step.get("digit_x0", region_def.get("digit_x0", 0)) or 0)
                )
            except (TypeError, ValueError):
                bulk_digit_x0.append(0)
        try:
            results = await self._get_ocr_client().ocr_regions(
                image,
                [region_px for _step, _region, _region_def, region_px in requests],
                region_ids=[region for _step, region, _region_def, _region_px in requests],
                region_preprocess=bulk_preprocess if any(bulk_preprocess) else None,
                region_digit_count=(
                    bulk_digit_count if any(v is not None for v in bulk_digit_count) else None
                ),
                region_digit_x0=bulk_digit_x0 if any(bulk_digit_x0) else None,
            )
        except Exception:
            logger.exception(
                "dsl_scenario: bulk OCR call failed (scenario=%s regions=%s)",
                _scen(scenario_key),
                [region for _step, region, _def, _px in requests],
            )
            for step, region, _region_def, _region_px in requests:
                await self._ocr_audit_step(
                    instance_id, region=region, step=step, status="ocr_call_failed"
                )
            return

        logger.info(
            "dsl_scenario: bulk OCR scenario=%s regions=%s",
            _scen(scenario_key),
            [region for _step, region, _def, _px in requests],
        )
        for (step, region, region_def, _region_px), result in zip(
            requests, results, strict=False
        ):
            await self._persist_ocr_result(
                instance_id=instance_id,
                scenario_key=scenario_key,
                step=step,
                region=region,
                region_def=region_def,
                result=result,
            )

    async def _persist_ocr_result(
        self,
        *,
        instance_id: str,
        scenario_key: str,
        step: dict[str, Any],
        region: str,
        region_def: dict[str, Any],
        result: Any,
    ) -> None:
        raw_threshold = step.get("threshold")
        if raw_threshold is None:
            raw_threshold = region_def.get("threshold")
        try:
            threshold = float(raw_threshold) if raw_threshold is not None else 0.0
        except (TypeError, ValueError):
            threshold = 0.0

        thr_s = f"{threshold:.6g}"
        text = (result.text or "").strip()
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        conf_s = f"{confidence:.4f}"
        scen_label = _scen(scenario_key)

        # Two independent persistence channels (resolved up front so every skip
        # path can still name the intended target):
        #   - ``store: <field>`` → Redis (ephemeral, scenario-step scope).
        #   - ``state: <dotted.path>`` → SQLite state store (long-lived, drives
        #     arithmetic ``cond`` via state_flat).
        # Backward-compat: if neither is given, default to ``store: <region>``.
        raw_store = step.get("store")
        raw_state = step.get("state")
        if raw_store is None and raw_state is None:
            store_redis_field = region.strip() if region else ""
            state_yaml_path = ""
        else:
            store_redis_field = str(raw_store or "").strip()
            state_yaml_path = str(raw_state or "").strip()
        event_timer_name = _event_timer_name_from_spec(step.get("event_timer"))
        planned_target = store_redis_field or state_yaml_path or event_timer_name
        # Single name used in early-skip logs (low_confidence, integer_cast_failed, etc).
        planned_store_field = planned_target

        if confidence < threshold:
            logger.warning(
                "dsl_scenario: store skipped field=%s reason=low_confidence "
                "value=%r confidence=%.3f threshold=%.3f region=%s scenario=%s",
                planned_store_field or "?",
                text,
                confidence,
                threshold,
                region,
                scen_label,
            )
            await self._ocr_audit_step(
                instance_id,
                region=region,
                step=step,
                status="low_confidence",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
            )
            return

        type_hint = str(step.get("type") or region_def.get("type") or "string").strip().lower()
        value: str = text
        if type_hint in {"int", "integer"}:
            parsed = parse_ocr_integer(text)
            if parsed is None:
                logger.warning(
                    "dsl_scenario: store skipped field=%s reason=integer_cast_failed "
                    "value=%r region=%s scenario=%s",
                    planned_store_field or "?",
                    text,
                    region,
                    scen_label,
                )
                await self._ocr_audit_step(
                    instance_id,
                    region=region,
                    step=step,
                    status="integer_cast_failed",
                    threshold_s=thr_s,
                    confidence_s=conf_s,
                    raw_text=text,
                )
                return
            value = str(parsed)
        elif type_hint == "time":
            seconds = _parse_hms_to_seconds(text)
            if seconds is None:
                logger.warning(
                    "dsl_scenario: store skipped field=%s reason=time_cast_failed "
                    "value=%r region=%s scenario=%s",
                    planned_store_field or "?",
                    text,
                    region,
                    scen_label,
                )
                await self._ocr_audit_step(
                    instance_id,
                    region=region,
                    step=step,
                    status="time_cast_failed",
                    threshold_s=thr_s,
                    confidence_s=conf_s,
                    raw_text=text,
                )
                return
            value = str(seconds)

        # Optional ``throttle_push: <scenario>`` writes a push_ttl marker so
        # subsequent overlay pushes of that scenario (and explicit re-pushes
        # via ``push_scenario:``) are silently dropped until the marker
        # expires. Pairs with ``type: time`` for the "building is upgrading,
        # don't re-fire chapter task until done" idiom (see
        # ``modules/core/building/common/scenarios/building.upgrade.yaml`` ↔
        # ``modules/core/building/common/analyze/analyze.yaml``). Same key shape
        # and scope as ``_enqueue_push_scenarios_from_overlay``'s push-level
        # ttl throttle so a single marker covers both push sources.
        throttle_target_raw = step.get("throttle_push")
        throttle_target_name = ""
        if isinstance(throttle_target_raw, str):
            throttle_target_name = throttle_target_raw.strip()
        elif isinstance(throttle_target_raw, dict):
            throttle_target_name = str(throttle_target_raw.get("name") or "").strip()
        throttle_written = False
        if (
            throttle_target_name
            and type_hint == "time"
            and self.redis_client is not None
        ):
            try:
                throttle_seconds = int(value)
            except (TypeError, ValueError):
                throttle_seconds = 0
            if throttle_seconds > 0:
                scope_pid = (self.player_id or "").strip()
                if not scope_pid:
                    with suppress(Exception):
                        raw_ap = await self.redis_client.hget(
                            f"wos:instance:{instance_id}:state", "active_player"
                        )
                        ap = (
                            raw_ap.decode()
                            if isinstance(raw_ap, (bytes, bytearray))
                            else (raw_ap or "")
                        )
                        scope_pid = str(ap).strip()
                throttle_key = (
                    f"wos:player:{scope_pid}:push_ttl:{throttle_target_name}"
                    if scope_pid
                    else f"wos:instance:{instance_id}:push_ttl:{throttle_target_name}"
                )
                with suppress(Exception):
                    await self.redis_client.set(
                        throttle_key, "1", ex=int(throttle_seconds)
                    )
                    throttle_written = True
                    logger.info(
                        "dsl_scenario: throttle_push set scenario=%s ttl=%ds "
                        "key=%s source_region=%s active_scenario=%s",
                        throttle_target_name, throttle_seconds, throttle_key,
                        region, scen_label,
                    )

        # Optional ``event_timer: <event_name>`` stores a durable reset timer
        # snapshot in SQLite player state. Unlike the Redis ``store:`` value,
        # this survives worker restarts and keeps dotted event names intact as
        # dict keys (``event_timers["shop.artisans_trove"]``).
        event_timer_written = False
        if event_timer_name:
            timer_seconds = _parse_hms_to_seconds(text)
            if timer_seconds is None:
                logger.warning(
                    "dsl_scenario: event_timer skipped event=%s reason=time_parse_failed "
                    "value=%r region=%s scenario=%s",
                    event_timer_name,
                    text,
                    region,
                    scen_label,
                )
            else:
                scope_pid = str(self.player_id or "").strip()
                if not scope_pid:
                    scope_pid = await _read_active_player(instance_id, self.redis_client)
                if not scope_pid:
                    logger.warning(
                        "dsl_scenario: event_timer skipped event=%s reason=no_active_player "
                        "value=%r region=%s scenario=%s",
                        event_timer_name,
                        text,
                        region,
                        scen_label,
                    )
                else:
                    event_timer_written = store_event_timer(
                        player_id=scope_pid,
                        event_name=event_timer_name,
                        raw_text=text,
                        remaining_s=timer_seconds,
                        recorded_at=time.time(),
                        source_region=region,
                        confidence=confidence,
                    )
                    if event_timer_written:
                        logger.info(
                            "dsl_scenario: event_timer ok event=%s remaining=%ds "
                            "player=%s region=%s scenario=%s",
                            event_timer_name,
                            timer_seconds,
                            scope_pid,
                            region,
                            scen_label,
                        )

        if (
            not store_redis_field
            and not state_yaml_path
            and not throttle_written
            and not event_timer_name
        ):
            logger.warning(
                "dsl_scenario: persist skipped reason=no_target "
                "value=%r region=%s scenario=%s — specify `store:` and/or `state:`",
                value, region, scen_label,
            )
            await self._ocr_audit_step(
                instance_id,
                region=region,
                step=step,
                status="empty_store_field",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
            )
            return

        scope = str(step.get("scope") or "player").strip().lower()
        if scope not in {"player", "instance"}:
            logger.warning(
                "dsl_scenario: unknown ocr scope %r — defaulting to 'player' (scenario=%s)",
                scope, scen_label,
            )
            scope = "player"

        # ----- Redis (ephemeral) write -----
        redis_written = False
        redis_key = ""
        if store_redis_field:
            if self.redis_client is None:
                logger.warning(
                    "dsl_scenario: store skipped field=%s reason=no_redis_client "
                    "value=%r confidence=%.4f region=%s scenario=%s",
                    store_redis_field, value, confidence, region, scen_label,
                )
                # No early return: the ``state:`` write below may still be useful.
            else:
                if scope == "player" and self.player_id:
                    redis_key = f"wos:player:{self.player_id}:state"
                elif scope == "player" and store_redis_field == "player_id" and value:
                    # `who_i_am` is a device-level probe. Once OCR tells us the in-game id,
                    # let the rest of the scenario (e.g. fetch_player) continue under it.
                    self.player_id = str(value)
                    redis_key = f"wos:player:{self.player_id}:state"
                else:
                    redis_key = f"wos:instance:{instance_id}:state"

                mapping: dict[str, str] = {
                    store_redis_field: str(value),
                    f"{store_redis_field}_text": text,
                    f"{store_redis_field}_confidence": f"{confidence:.4f}",
                    f"{store_redis_field}_at": str(time.time()),
                }
                try:
                    await self.redis_client.hset(redis_key, mapping=mapping)
                    if store_redis_field == "player_id" and value:
                        identified = str(self.player_id or value)
                        await self.redis_client.hset(
                            f"wos:instance:{instance_id}:state",
                            mapping={
                                "active_player": identified,
                                "active_player_at": str(time.time()),
                            },
                        )
                        # Durably remember the identity so a worker restart can
                        # restore ``active_player`` and skip the ``who_i_am`` probe
                        # (config.devices_db). Best-effort — never fail the OCR step.
                        with suppress(Exception):
                            from config.devices import set_last_active_player

                            set_last_active_player(instance_id, identified)
                    redis_written = True
                    if scope == "player" and self.player_id:
                        from dashboard.dashboard_events import (
                            publish_dashboard_event_throttled_async,
                        )

                        await publish_dashboard_event_throttled_async(
                            self.redis_client,
                            topic="player",
                            player_id=str(self.player_id),
                            reason="ocr_store",
                        )
                    logger.info(
                        "dsl_scenario: store ok field=%s value=%r key=%s scope=%s "
                        "confidence=%.4f region=%s scenario=%s",
                        store_redis_field, value, redis_key, scope, confidence,
                        region, scen_label,
                    )
                except Exception:
                    logger.exception(
                        "dsl_scenario: store failed field=%s value=%r reason=redis_write_failed "
                        "key=%s region=%s scenario=%s",
                        store_redis_field, value, redis_key, region, scen_label,
                    )
                    await self._ocr_audit_step(
                        instance_id,
                        region=region,
                        step=step,
                        status="redis_write_failed",
                        threshold_s=thr_s,
                        confidence_s=conf_s,
                        raw_text=text,
                        value_s=str(value),
                    )
                    return

        # ----- SQLite state (long-lived) write -----
        state_written = False
        if state_yaml_path:
            if scope != "player" or not self.player_id:
                logger.warning(
                    "dsl_scenario: state skipped path=%s reason=no_player_scope "
                    "value=%r region=%s scenario=%s",
                    state_yaml_path, value, region, scen_label,
                )
            else:
                typed_value: Any = value
                # ``time`` was already converted to a seconds string above;
                # both integer and time types persist as Python ``int`` to the
                # SQLite state store so arithmetic ``cond`` (e.g.
                # ``timer_remaining_s < 300``) works without casts.
                if type_hint in {"int", "integer", "time"}:
                    with suppress(TypeError, ValueError):
                        typed_value = int(value)
                try:
                    from config.state_store import get_state_store

                    store = get_state_store().get_or_create(str(self.player_id))
                    store.update_from_flat({state_yaml_path: typed_value})
                    state_written = True
                    logger.info(
                        "dsl_scenario: state ok path=%s value=%r player=%s "
                        "confidence=%.4f region=%s scenario=%s",
                        state_yaml_path, typed_value, self.player_id, confidence,
                        region, scen_label,
                    )
                except Exception:
                    logger.exception(
                        "dsl_scenario: state failed path=%s value=%r reason=state_store_write "
                        "region=%s scenario=%s",
                        state_yaml_path, value, region, scen_label,
                    )

        await self._ocr_audit_step(
            instance_id,
            region=region,
            step=step,
            status=(
                "stored"
                if (redis_written or state_written or event_timer_written)
                else "no_redis_client"
            ),
            threshold_s=thr_s,
            confidence_s=conf_s,
            raw_text=text,
            value_s=str(value),
        )
