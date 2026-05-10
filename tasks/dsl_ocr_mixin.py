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
from typing import Any

from actions.tap import BotActions
from config.log_ansi import scenario_log_label as _scen
from layout.area_lookup import screen_region_by_name
from layout.types import Region

logger = logging.getLogger(__name__)


class DslOcrMixin:
    redis_client: Any
    player_id: str | None
    _ocr_client: Any

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
            from ocr.client import OcrClient

            self._ocr_client = OcrClient()
        return self._ocr_client

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
        """OCR a named region and persist the result to Redis.

        Step shape::

            - ocr: <region_name>
              store: <field>          # default = region name
              scope: player|instance  # default = "player"
                                     # falls back to instance when no player_id
              type: integer|string    # default = inherits area.json `type`
              threshold: 0.7          # confidence floor; default = inherits area.json `threshold`

        The decoded value is written to ``wos:player:<player_id>:state`` (player scope) or
        ``wos:instance:<instance_id>:state`` (instance scope) under ``<store>``, alongside
        ``<store>_text`` (raw OCR text), ``<store>_confidence`` and ``<store>_at`` for
        debugging. Low-confidence reads are logged and skipped, never persisted.
        """
        pair = screen_region_by_name(area_doc, region, state_flat=self._state_flat()) if region else None
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
            image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
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

        try:
            result = await self._get_ocr_client().ocr_region(
                image, Region(px, py, pw, ph)
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

        for step in steps:
            region = str(step.get("ocr") or "").strip()
            if not region:
                continue
            pair = screen_region_by_name(area_doc, region, state_flat=self._state_flat())
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
            image = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
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

        try:
            results = await self._get_ocr_client().ocr_regions(
                image,
                [region_px for _step, _region, _region_def, region_px in requests],
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
        if confidence < threshold:
            logger.warning(
                "dsl_scenario: OCR low confidence — skipping store. scenario=%s region=%s "
                "text=%r confidence=%.3f threshold=%.3f",
                _scen(scenario_key),
                region,
                text,
                confidence,
                threshold,
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
            digits = re.sub(r"\D+", "", text)
            if not digits:
                logger.warning(
                    "dsl_scenario: OCR integer cast failed — empty digits. "
                    "scenario=%s region=%s text=%r",
                    _scen(scenario_key),
                    region,
                    text,
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
            value = digits

        store_field = str(step.get("store") or region).strip()
        if not store_field:
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
                scope,
                _scen(scenario_key),
            )
            scope = "player"

        if self.redis_client is None:
            logger.info(
                "dsl_scenario: OCR result not persisted (no redis client). "
                "scenario=%s region=%s field=%s value=%s confidence=%.3f",
                _scen(scenario_key),
                region,
                store_field,
                value,
                confidence,
            )
            await self._ocr_audit_step(
                instance_id,
                region=region,
                step=step,
                status="no_redis_client",
                threshold_s=thr_s,
                confidence_s=conf_s,
                raw_text=text,
                value_s=str(value),
            )
            return

        if scope == "player" and self.player_id:
            redis_key = f"wos:player:{self.player_id}:state"
        elif scope == "player" and store_field == "player_id" and value:
            # `who_i_am` is intentionally a device-level probe. Once OCR tells
            # us the in-game id, let the rest of the scenario (e.g. fetch_player)
            # continue under that real identity.
            self.player_id = str(value)
            redis_key = f"wos:player:{self.player_id}:state"
        else:
            redis_key = f"wos:instance:{instance_id}:state"

        mapping: dict[str, str] = {
            store_field: str(value),
            f"{store_field}_text": text,
            f"{store_field}_confidence": f"{confidence:.4f}",
            f"{store_field}_at": str(time.time()),
        }
        try:
            await self.redis_client.hset(redis_key, mapping=mapping)
            if store_field == "player_id" and value:
                await self.redis_client.hset(
                    f"wos:instance:{instance_id}:state",
                    mapping={
                        "active_player": str(self.player_id or value),
                        "active_player_at": str(time.time()),
                    },
                )
        except Exception:
            logger.exception(
                "dsl_scenario: failed to persist OCR result (scenario=%s region=%s key=%s)",
                _scen(scenario_key),
                region,
                redis_key,
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

        await self._ocr_audit_step(
            instance_id,
            region=region,
            step=step,
            status="stored",
            threshold_s=thr_s,
            confidence_s=conf_s,
            raw_text=text,
            value_s=str(value),
        )
        logger.info(
            "dsl_scenario: OCR stored scenario=%s region=%s key=%s field=%s value=%s "
            "confidence=%.3f",
            _scen(scenario_key),
            region,
            redis_key,
            store_field,
            value,
            confidence,
        )
