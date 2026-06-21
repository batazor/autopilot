"""DSL exec: record a freshly-built building into the player profile.

Called from the onboarding build scenario right after it taps "Build". It reads
the building name the scenario just OCR'd from the build-detail title (via the
instance-state ``dsl_last_ocr_*`` breadcrumbs the OCR step leaves), slugifies it
to a canonical building id, and writes ``buildings.levels.<slug>`` to:

- the **durable** SQLite player profile (the source of truth), and
- the **Redis instance-state** hash (the cheap hot-path mirror the onboarding
  phase gate reads — see :mod:`worker.onboarding_phase`).

During onboarding there is no resolved gamer id yet, so the profile is keyed by
the **device id** (instance id) until ``who_i_am`` learns the real one. The
parse core (:func:`_slug`) is pure so it can be unit-tested without Redis.
"""
from __future__ import annotations

import logging
import re

from tasks.dsl_exec.context import (
    DslExecContext,
    _decode_redis_raw,
    _resolve_player_id_for_device_level_exec,
)

logger = logging.getLogger(__name__)

# Only trust a name OCR'd from this region (the build-detail title), so a stale
# OCR from some other step can't be recorded as a building.
_TITLE_REGION = "onboarding.build.title"
_LEVEL_RE = re.compile(r"\blv\.?\s*\d+\b", re.IGNORECASE)
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    """Canonical building id from an OCR'd title.

    ``"Sawmill"`` → ``"sawmill"``; ``"Hunters' Hut Lv. 1"`` → ``"hunters_hut"``.
    """
    stripped = _LEVEL_RE.sub(" ", name or "").lower()
    return _NONWORD_RE.sub("_", stripped).strip("_")


async def _exec_record_onboarding_build(ctx: DslExecContext) -> None:
    r = ctx.redis_client
    if r is None:
        ctx.result.update({"reason": "no_redis_client"})
        return

    inst_key = f"wos:instance:{ctx.instance_id}:state"
    region = _decode_redis_raw(await r.hget(inst_key, "dsl_last_ocr_region"))
    if region != _TITLE_REGION:
        ctx.result.update({"reason": "no_title_ocr", "region": region})
        return
    slug = _slug(_decode_redis_raw(await r.hget(inst_key, "dsl_last_ocr_value")))
    if not slug:
        ctx.result.update({"reason": "empty_slug"})
        return

    try:
        level = int(ctx.args.get("level", 1))
    except (TypeError, ValueError):
        level = 1

    # No gamer id during onboarding → key the profile by the device id until
    # who_i_am resolves the real one.
    player_id = (await _resolve_player_id_for_device_level_exec(ctx)) or ctx.instance_id
    field = f"buildings.levels.{slug}"

    # Durable profile (SQLite) — never downgrade a recorded level.
    try:
        from config.state_store import get_state_store

        store = get_state_store().get_or_create(str(player_id))
        current = int(store.to_flat_dict().get(field, 0) or 0)
        if level > current:
            store.update_from_flat({field: level})
    except Exception:
        logger.exception("record_onboarding_build: durable write failed field=%s", field)

    # Redis instance-state mirror — the onboarding phase gate reads this.
    try:
        await r.hset(inst_key, field, str(level))
    except Exception:
        logger.debug("record_onboarding_build: redis mirror failed", exc_info=True)

    ctx.result.update(
        {"action": "recorded", "building": slug, "level": level, "player_id": player_id}
    )
    logger.info(
        "record_onboarding_build: %s=%d player=%s instance=%s",
        field,
        level,
        player_id,
        ctx.instance_id,
    )


DSL_EXEC_HANDLERS = {"record_onboarding_build": _exec_record_onboarding_build}
