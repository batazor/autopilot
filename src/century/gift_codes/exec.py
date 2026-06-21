"""DSL ``exec:`` handlers for gift-code scrape + redeem (both games).

Wired into :data:`tasks.dsl_exec._CORE_DSL_EXEC_REGISTRY` so the dashboard
"Scrape now" / "Redeem now" buttons keep working. The scheduler's global
poller (:meth:`scheduler.runner.SchedulerRunner._run_gift_codes_polling`)
shares the per-game redeem lock with these handlers so manual + scheduled
triggers don't race.

WOS and Kingshot share scaffolding (lock, state, notifications) and differ
only in the underlying ``poll_once`` / ``run_gift_code_redeemer`` callables
and Redis key suffixes.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from century.gift_codes import kingshot as ks_mod
from century.gift_codes import wos as wos_mod
from dashboard.notifications import push_ui_notification

if TYPE_CHECKING:
    from tasks.dsl_exec import DslExecContext

# ``tasks.dsl_exec`` would normally provide ``DslExecHandler`` and
# ``_decode_redis_raw``, but importing it at module load creates a circular
# import: dsl_exec calls ``build_dsl_exec_registry()`` at the bottom of its
# own module body, which in turn imports this file. Inline the tiny pieces
# we actually need so the engine boot order stays one-way.
DslExecHandler = Callable[[Any], Awaitable[None]]


def _decode_redis_raw(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw).strip()


logger = logging.getLogger(__name__)


@dataclass
class _GameSpec:
    """One game's bindings for the gift-code DSL handlers.

    Not frozen so test fixtures can monkey-patch ``poll_once`` /
    ``run_redeemer`` per game without rebuilding the registry.
    """

    game_id: str
    handler_prefix: str          # exec name prefix, e.g. "gift_code" or "kingshot_gift_code"
    notif_label: str             # human label in notification messages, e.g. "" or "Kingshot "
    lock_key: str                # Redis lock key (shared with scheduler)
    state_key: str               # Redis hash where status is persisted for the UI
    background_name: str         # asyncio.Task name
    poll_once: object            # async () -> list[str]
    run_redeemer: object         # async (...) -> GiftRedeemSummary
    payload_extra: dict[str, object]  # extra fields to merge into UI payloads


_LOCK_TTL_SECONDS = 2 * 60 * 60
_STATE_TTL_SECONDS = 7 * 24 * 60 * 60

_BACKGROUND_GIFT_REDEEM_TASKS: set[asyncio.Task[None]] = set()


_SPECS: tuple[_GameSpec, ...] = (
    _GameSpec(
        game_id="wos",
        handler_prefix="gift_code",
        notif_label="",
        lock_key="wos:gift_code_redeem:lock",
        state_key="wos:gift_code_redeem:state",
        background_name="gift-code-redeem-background",
        poll_once=wos_mod.poll_once,
        run_redeemer=wos_mod.run_gift_code_redeemer,
        payload_extra={},
    ),
    _GameSpec(
        game_id="kingshot",
        handler_prefix="kingshot_gift_code",
        notif_label="Kingshot ",
        lock_key="wos:gift_code_redeem:lock:kingshot",
        state_key="wos:gift_code_redeem:state:kingshot",
        background_name="kingshot-gift-code-redeem-background",
        poll_once=ks_mod.poll_once,
        run_redeemer=ks_mod.run_gift_code_redeemer,
        payload_extra={"game": "kingshot"},
    ),
)


# ── Scrape ─────────────────────────────────────────────────────────────────


def _make_scrape_handler(spec: _GameSpec) -> DslExecHandler:
    name = f"exec.{spec.handler_prefix}_scrape"

    async def _handler(ctx: DslExecContext) -> None:
        try:
            new = await spec.poll_once()  # type: ignore[operator]
        except Exception:
            logger.exception("dsl %s: scraper failed", name)
            return
        if new:
            logger.info("dsl %s: found %d new code(s): %s", name, len(new), ", ".join(new))
            await push_ui_notification(
                ctx.redis_client,
                ctx.instance_id,
                kind=name,
                message=f"New {spec.notif_label}gift codes found: {', '.join(new)}",
                level="info",
                payload={"codes": new, **spec.payload_extra},
            )
        else:
            logger.info("dsl %s: no new codes", name)

    return _handler


# ── Redeem (background, locked) ────────────────────────────────────────────


async def _acquire_lock(redis_client: object | None, key: str, token: str) -> bool:
    if redis_client is None:
        return not any(not t.done() for t in _BACKGROUND_GIFT_REDEEM_TASKS)
    try:
        ok = await redis_client.set(key, token, nx=True, ex=_LOCK_TTL_SECONDS)  # type: ignore[attr-defined]
    except Exception:
        logger.exception("gift-code redeem lock acquire failed (key=%s)", key)
        return False
    return bool(ok)


async def _release_lock(redis_client: object | None, key: str, token: str) -> None:
    if redis_client is None:
        return
    try:
        raw = await redis_client.get(key)  # type: ignore[attr-defined]
        if _decode_redis_raw(raw) == token:
            await redis_client.delete(key)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("gift-code redeem lock release failed (key=%s)", key, exc_info=True)


async def _write_state(
    redis_client: object | None, key: str, **fields: object
) -> None:
    if redis_client is None:
        return
    mapping = {str(k): str(v) for k, v in fields.items() if v is not None}
    if not mapping:
        return
    try:
        await redis_client.hset(key, mapping=mapping)  # type: ignore[attr-defined]
        await redis_client.expire(key, _STATE_TTL_SECONDS)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("gift-code redeem state write failed (key=%s)", key, exc_info=True)


async def _run_redeem_background(
    spec: _GameSpec, ctx: DslExecContext, token: str
) -> None:
    started_at = time.time()
    await _write_state(
        ctx.redis_client,
        spec.state_key,
        status="running",
        started_at=started_at,
        instance_id=ctx.instance_id,
        token=token,
        **dict(spec.payload_extra),
    )
    await push_ui_notification(
        ctx.redis_client,
        ctx.instance_id,
        kind=f"exec.{spec.handler_prefix}_redeem.started",
        message=f"{spec.notif_label}Gift code redeem started in background".strip(),
        level="info",
        payload={"started_at": started_at, **spec.payload_extra},
    )

    try:
        summary = await spec.run_redeemer()  # type: ignore[operator]
    except Exception as exc:
        finished_at = time.time()
        logger.exception(
            "dsl exec.%s_redeem: background redeemer failed", spec.handler_prefix
        )
        await _write_state(
            ctx.redis_client,
            spec.state_key,
            status="failed",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            error=f"{type(exc).__name__}: {exc!s}",
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind=f"exec.{spec.handler_prefix}_redeem.failed",
            message=f"{spec.notif_label}Gift code redeem failed: {type(exc).__name__}".strip(),
            level="error",
            payload={"error": f"{type(exc).__name__}: {exc!s}", **spec.payload_extra},
        )
        return
    finally:
        await _release_lock(ctx.redis_client, spec.lock_key, token)

    finished_at = time.time()
    counts = summary.counts_by_status()
    total = len(summary.results)
    if total:
        counts_s = ", ".join(f"{k}={v}" for k, v in counts.items())
        logger.info(
            "dsl exec.%s_redeem: background done total=%d %s",
            spec.handler_prefix, total, counts_s,
        )
        await _write_state(
            ctx.redis_client,
            spec.state_key,
            status="done",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            total=total,
            counts=counts_s,
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind=f"exec.{spec.handler_prefix}_redeem",
            message=f"{spec.notif_label}Gift code redeem done: {counts_s}".strip(),
            level="info",
            payload={**summary.to_dict(), **spec.payload_extra},
        )
    else:
        logger.info(
            "dsl exec.%s_redeem: background done, nothing pending", spec.handler_prefix
        )
        await _write_state(
            ctx.redis_client,
            spec.state_key,
            status="done",
            finished_at=finished_at,
            duration_s=f"{finished_at - started_at:.1f}",
            total=0,
            counts="",
        )
        await push_ui_notification(
            ctx.redis_client,
            ctx.instance_id,
            kind=f"exec.{spec.handler_prefix}_redeem",
            message=f"{spec.notif_label}Gift code redeem done: nothing pending".strip(),
            level="info",
            payload={**summary.to_dict(), **spec.payload_extra},
        )


def _make_redeem_handler(spec: _GameSpec) -> DslExecHandler:
    async def _handler(ctx: DslExecContext) -> None:
        token = uuid.uuid4().hex
        if not await _acquire_lock(ctx.redis_client, spec.lock_key, token):
            logger.info(
                "dsl exec.%s_redeem: already running — skip background start",
                spec.handler_prefix,
            )
            await push_ui_notification(
                ctx.redis_client,
                ctx.instance_id,
                kind=f"exec.{spec.handler_prefix}_redeem.already_running",
                message=f"{spec.notif_label}Gift code redeem is already running".strip(),
                level="info",
            )
            return

        await _write_state(
            ctx.redis_client,
            spec.state_key,
            status="queued",
            queued_at=time.time(),
            instance_id=ctx.instance_id,
            token=token,
            **dict(spec.payload_extra),
        )
        task = asyncio.create_task(
            _run_redeem_background(spec, ctx, token),
            name=spec.background_name,
        )
        _BACKGROUND_GIFT_REDEEM_TASKS.add(task)

        def _on_done(done: asyncio.Task[None]) -> None:
            _BACKGROUND_GIFT_REDEEM_TASKS.discard(done)
            with suppress(asyncio.CancelledError):
                exc = done.exception()
                if exc is not None:
                    logger.error(
                        "%s background task crashed",
                        spec.background_name,
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

        task.add_done_callback(_on_done)
        logger.info("dsl exec.%s_redeem: started background task", spec.handler_prefix)

    return _handler


def _build_handlers() -> dict[str, DslExecHandler]:
    out: dict[str, DslExecHandler] = {}
    for spec in _SPECS:
        out[f"{spec.handler_prefix}_scrape"] = _make_scrape_handler(spec)
        out[f"{spec.handler_prefix}_redeem"] = _make_redeem_handler(spec)
    return out


DSL_EXEC_HANDLERS: dict[str, DslExecHandler] = _build_handlers()
