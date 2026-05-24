"""DSL ``exec:`` handlers for the gift_codes module."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress

from dashboard.notifications import push_ui_notification
from modules.gift_codes.redeemer import run_gift_code_redeemer
from modules.gift_codes.scraper import poll_once
from tasks.dsl_exec import DslExecContext, DslExecHandler, _decode_redis_raw

logger = logging.getLogger(__name__)

_GIFT_REDEEM_LOCK_KEY = "wos:gift_code_redeem:lock"
_GIFT_REDEEM_STATE_KEY = "wos:gift_code_redeem:state"
_GIFT_REDEEM_LOCK_TTL_SECONDS = 2 * 60 * 60
_BACKGROUND_GIFT_REDEEM_TASKS: set[asyncio.Task[None]] = set()


async def _exec_gift_code_scrape(ctx: DslExecContext) -> None:
    """Scrape wosrewards.com for new gift codes and upsert into the SQLite gift_codes table."""
    try:
        new = await poll_once()
    except Exception:
        logger.exception("dsl exec gift_code_scrape: scraper failed")
        return
    if new:
        logger.info("dsl exec gift_code_scrape: found %d new code(s): %s", len(new), ", ".join(new))
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_scrape",
            message=f"New gift codes found: {', '.join(new)}",
            level="info",
            payload={"codes": new},
        )
    else:
        logger.info("dsl exec gift_code_scrape: no new codes")


async def _acquire_gift_redeem_lock(ctx: DslExecContext, token: str) -> bool:
    if ctx.redis_client is None:
        return not any(not t.done() for t in _BACKGROUND_GIFT_REDEEM_TASKS)
    try:
        ok = await ctx.redis_client.set(
            _GIFT_REDEEM_LOCK_KEY,
            token,
            nx=True,
            ex=_GIFT_REDEEM_LOCK_TTL_SECONDS,
        )
    except Exception:
        logger.exception("dsl exec gift_code_redeem: lock acquire failed")
        return False
    return bool(ok)


async def _release_gift_redeem_lock(ctx: DslExecContext, token: str) -> None:
    if ctx.redis_client is None:
        return
    try:
        raw = await ctx.redis_client.get(_GIFT_REDEEM_LOCK_KEY)
        if _decode_redis_raw(raw) == token:
            await ctx.redis_client.delete(_GIFT_REDEEM_LOCK_KEY)
    except Exception:
        logger.debug("dsl exec gift_code_redeem: lock release failed", exc_info=True)


async def _write_gift_redeem_state(ctx: DslExecContext, **fields: object) -> None:
    if ctx.redis_client is None:
        return
    mapping = {str(k): str(v) for k, v in fields.items() if v is not None}
    if not mapping:
        return
    try:
        await ctx.redis_client.hset(_GIFT_REDEEM_STATE_KEY, mapping=mapping)
        await ctx.redis_client.expire(_GIFT_REDEEM_STATE_KEY, 7 * 24 * 60 * 60)
    except Exception:
        logger.debug("dsl exec gift_code_redeem: state write failed", exc_info=True)


async def _run_gift_code_redeem_background(ctx: DslExecContext, token: str) -> None:
    started_at = time.time()
    await _write_gift_redeem_state(
        ctx,
        status="running",
        started_at=started_at,
        instance_id=ctx.instance_id,
        token=token,
    )
    await push_ui_notification(
        ctx.redis_client,
        ctx.instance_id,
        kind="exec.gift_code_redeem.started",
        message="Gift code redeem started in background",
        level="info",
        payload={"started_at": started_at},
    )

    try:
        summary = await run_gift_code_redeemer()
    except Exception as exc:
        finished_at = time.time()
        logger.exception("dsl exec gift_code_redeem: background redeemer failed")
        await _write_gift_redeem_state(
            ctx,
            status="failed",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            error=f"{type(exc).__name__}: {exc!s}",
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem.failed",
            message=f"Gift code redeem failed: {type(exc).__name__}",
            level="error",
            payload={"error": f"{type(exc).__name__}: {exc!s}"},
        )
        return
    finally:
        await _release_gift_redeem_lock(ctx, token)

    finished_at = time.time()
    counts = summary.counts_by_status()
    total = len(summary.results)
    if total:
        counts_s = ", ".join(f"{k}={v}" for k, v in counts.items())
        logger.info("dsl exec gift_code_redeem: background done total=%d %s", total, counts_s)
        await _write_gift_redeem_state(
            ctx,
            status="done",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            total=total,
            counts=counts_s,
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem",
            message=f"Gift code redeem done: {counts_s}",
            level="info",
            payload=summary.to_dict(),
        )
    else:
        logger.info("dsl exec gift_code_redeem: background done, nothing pending")
        await _write_gift_redeem_state(
            ctx,
            status="done",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            total=0,
            counts="",
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem",
            message="Gift code redeem done: nothing pending",
            level="info",
            payload=summary.to_dict(),
        )


async def _exec_gift_code_redeem(ctx: DslExecContext) -> None:
    """Start gift-code redemption in the background."""
    token = uuid.uuid4().hex
    if not await _acquire_gift_redeem_lock(ctx, token):
        logger.info("dsl exec gift_code_redeem: already running — skip background start")
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind="exec.gift_code_redeem.already_running",
            message="Gift code redeem is already running",
            level="info",
        )
        return

    await _write_gift_redeem_state(
        ctx,
        status="queued",
        queued_at=time.time(),
        instance_id=ctx.instance_id,
        token=token,
    )
    task = asyncio.create_task(
        _run_gift_code_redeem_background(ctx, token),
        name="gift-code-redeem-background",
    )
    _BACKGROUND_GIFT_REDEEM_TASKS.add(task)

    def _on_done(done: asyncio.Task[None]) -> None:
        _BACKGROUND_GIFT_REDEEM_TASKS.discard(done)
        with suppress(asyncio.CancelledError):
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "gift-code redeem background task crashed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

    task.add_done_callback(_on_done)
    logger.info("dsl exec gift_code_redeem: started background task")


DSL_EXEC_HANDLERS: dict[str, DslExecHandler] = {
    "gift_code_scrape": _exec_gift_code_scrape,
    "gift_code_redeem": _exec_gift_code_redeem,
}
