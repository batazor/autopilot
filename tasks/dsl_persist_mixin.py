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

logger = logging.getLogger(__name__)


class DslPersistMixin:
    redis_client: Any
    player_id: str | None

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
