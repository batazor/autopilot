"""Click-approval gating for ADB input (Redis-backed)."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, cast

import redis

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None
_APPROVAL_POLL_SECONDS = 0.2
_APPROVAL_PREVIEW_REFRESH_SECONDS = 2.0
_APPROVAL_MISSING_CURRENT_REPUBLISH_LIMIT = 3
# Approval mode: there is intentionally NO non-decision exit from the wait
# loop — no wall-clock deadline AND no heartbeat-loss abort. The decision is
# always the operator's. The trade-off: closing the approvals page WILL hang
# the worker on this task until the page is reopened and a decision is given.
#
# How long we wait for the per-instance ``current`` slot to free up before
# giving up (only relevant if a previous request is still in flight on the
# same instance). Independent from the operator's review time.
_APPROVAL_PUBLISH_WAIT_SECONDS = 60.0
# Redis TTL for non-waiting approval states (approved/rejected/executing).
# Waiting requests in approval mode are stored without expiry: approval mode is
# operator-paced and must not age out while the bot is waiting for a decision.
APPROVAL_CURRENT_TTL_SECONDS = 300
_CLICK_APPROVAL_DISABLED = frozenset({"0", "false", "no", "off"})
_TIMELINE_MAX_ROWS = 5000
_TIMELINE_TTL_SECONDS = 3600
# Operator-issued ``skip`` decisions queued by ``_require_approval`` for the
# tap/swipe/type_text helpers to consume. Skip means "don't execute this ADB
# action but don't abort the scenario either" — the caller treats it as a
# successful no-op and proceeds to the next step.
_skipped_req_ids: set[str] = set()
# Copied from ``tasks.dsl_scenario`` Redis audit fields for Click approvals UI.
_DSL_APPROVAL_AUDIT_KEYS: tuple[str, ...] = (
    "dsl_last_match_region",
    "dsl_last_match_threshold",
    "dsl_last_match_score",
    "dsl_last_match_matched",
    "dsl_last_match_detail",
    "dsl_last_match_at",
    "dsl_last_match_top_left_x",
    "dsl_last_match_top_left_y",
    "dsl_last_match_template_w",
    "dsl_last_match_template_h",
    "dsl_last_match_search_region",
    "dsl_last_match_tap_x_pct",
    "dsl_last_match_tap_y_pct",
    "dsl_last_match_tap_match_x_pct",
    "dsl_last_match_tap_match_y_pct",
    "dsl_last_ocr_region",
    "dsl_last_ocr_store",
    "dsl_last_ocr_status",
    "dsl_last_ocr_threshold",
    "dsl_last_ocr_confidence",
    "dsl_last_ocr_raw_text",
    "dsl_last_ocr_value",
    "dsl_last_ocr_at",
)


def click_approval_enabled(instance_id: str) -> bool:
    """Return whether UI click-approval gating is on for ``instance_id``.

    Default is **enabled** when the Redis key is missing (opt-out via explicit ``0`` /
    ``false`` / ``no`` / ``off``).
    """
    enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
    raw = str(_r_get(enabled_key) or "").strip().lower()
    if not raw:
        return True
    return raw not in _CLICK_APPROVAL_DISABLED


def _redis() -> redis.Redis:
    """Lazy sync Redis client for UI click approvals."""
    global _redis_client
    if _redis_client is None:
        from config.loader import load_settings
        from config.redis_metrics import instrument_redis_client

        settings = load_settings()
        _redis_client = redis.Redis.from_url(settings.redis.url, decode_responses=True)
        instrument_redis_client(_redis_client, component="approvals")
    return _redis_client


# ``redis-py`` stubs declare return types as ``ResponseT`` (``Awaitable[T] | T``)
# so the same source compiles for the sync and async clients. ty flags every
# ``_r_get(...)`` / ``hgetall(...)`` callsite because of the ``Awaitable``
# arm of that union. These tiny wrappers narrow back to the concrete sync
# return — this module only ever uses ``redis.Redis`` (sync), and
# ``decode_responses=True`` guarantees ``str`` rather than ``bytes``.
def _r_get(key: str) -> str | None:
    return cast("str | None", _redis().get(key))


def _r_hgetall(key: str) -> dict[str, str]:
    return cast("dict[str, str]", _redis().hgetall(key))


def _clear_stale_approval_current(
    *,
    instance_id: str,
    current_key: str,
    new_context: dict[str, object],
) -> None:
    """Clear an old pending approval from a different owner.

    The approval slot is intentionally single-entry.  If a previous bot run
    exits while a request is pending, that stale JSON can block the next task
    from publishing its own request.

    Do not use wall-clock age here. In approval mode, waiting is intentional.
    Reap only when the owner task/scenario trying to publish is clearly
    different from the owner captured on the existing waiting request.
    """
    try:
        raw = _r_get(current_key)
        if not raw:
            return
        doc = json.loads(raw)
        if str(doc.get("status") or "").strip().lower() != "waiting":
            return
        old_ctx = doc.get("context")
        old_task_id = ""
        old_scenario = ""
        if isinstance(old_ctx, dict):
            old_task_id = str(old_ctx.get("current_task_id") or "").strip()
            old_scenario = str(old_ctx.get("scenario") or "").strip()
        new_task_id = str(new_context.get("current_task_id") or "").strip()
        new_scenario = str(new_context.get("scenario") or "").strip()

        should_clear = False
        if old_task_id and new_task_id:
            should_clear = old_task_id != new_task_id
        elif old_scenario and new_scenario:
            should_clear = old_scenario != new_scenario
        elif not old_scenario and new_scenario:
            # Prior approval is orphaned (worker died before it wrote
            # ``current_scenario``, or a pre-DSL publisher owned it).
            should_clear = True
        elif not new_scenario:
            # New publisher cannot identify itself; do not clobber a known owner.
            return

        if not should_clear:
            return
        _redis().delete(current_key)
        logger.info(
            "Click approval: cleared stale request for %s "
            "(old task=%r scenario=%r, new task=%r scenario=%r)",
            instance_id,
            old_task_id,
            old_scenario,
            new_task_id,
            new_scenario,
        )
    except Exception:
        logger.debug("Failed to clear stale approval current", exc_info=True)


def _record_approval_block(
    instance_id: str,
    *,
    req_id: str | None,
    reason: str,
    detail: str = "",
) -> None:
    try:
        mapping = {
            "last_approval_block_at": str(time.time()),
            "last_approval_block_reason": reason,
        }
        if req_id:
            mapping["last_approval_request_id"] = req_id
        if detail:
            mapping["last_approval_block_detail"] = detail
        _redis().hset(f"wos:instance:{instance_id}:state", mapping=mapping)
    except Exception:
        logger.debug("Failed to record approval block reason", exc_info=True)


def _emit_approval_timeline(
    instance_id: str,
    *,
    event: str,
    req_id: str | None,
    reason: str = "",
    detail: str = "",
    ctx: dict[str, object] | None = None,
) -> None:
    """Best-effort approval event for the Debug Timeline."""
    try:
        ctx = ctx or {}
        row = {
            "t": time.time(),
            "event": event,
            "instance_id": instance_id,
            "request_id": req_id or "",
            "reason": reason,
            "detail": detail,
            "task_id": str(ctx.get("current_task_id") or ""),
            "task_type": str(ctx.get("current_task_type") or ""),
            "scenario": str(ctx.get("scenario") or ""),
            "player_id": str(ctx.get("current_task_player") or ""),
            "current_screen": str(ctx.get("current_screen") or ""),
        }
        key = f"wos:debug:timeline:{instance_id}"
        client = _redis()
        client.lpush(key, json.dumps(row, ensure_ascii=False, default=str))
        client.ltrim(key, 0, _TIMELINE_MAX_ROWS - 1)
        client.expire(key, _TIMELINE_TTL_SECONDS)
    except Exception:
        logger.debug("Failed to emit approval timeline event", exc_info=True)


def _approval_owner_still_current(instance_id: str, ctx: dict[str, object]) -> bool:
    try:
        raw = _r_hgetall(f"wos:instance:{instance_id}:state")
    except Exception:
        logger.debug("Failed to read approval owner state", exc_info=True)
        return True
    old_task_id = str(ctx.get("current_task_id") or "").strip()
    new_task_id = str(raw.get("current_task_id") or "").strip()
    if old_task_id and new_task_id and old_task_id != new_task_id:
        return False
    old_scenario = str(ctx.get("scenario") or "").strip()
    new_scenario = str(raw.get("current_scenario") or "").strip()
    return not (old_scenario and new_scenario and old_scenario != new_scenario)


def _require_approval(instance_id: str, payload: dict[str, object]) -> tuple[bool, str | None]:
    """If approval mode is enabled, block until UI approves/rejects.

    Contract (no stack):
    - At most one pending request per instance stored at
      ``wos:ui:click_approval:current:<instance_id>``.
    - UI writes decision to the request-specific ``response_key`` from the payload,
      then may delete ``current`` immediately so the approvals page clears preview;
      this path must still honor approve (poll ``response_key`` before inferring reject).
    """
    if not click_approval_enabled(instance_id):
        return True, None

    preview_capturer = payload.get("_preview_capturer")
    last_preview_refresh_at = 0.0

    def _refresh_preview_if_due(target: dict[str, object], *, force: bool = False) -> None:
        nonlocal last_preview_refresh_at
        if not callable(preview_capturer):
            return
        now = time.time()
        if not force and (now - last_preview_refresh_at) < _APPROVAL_PREVIEW_REFRESH_SECONDS:
            return
        try:
            # ``payload`` is ``dict[str, object]`` so ``_preview_capturer`` lands
            # here as ``object`` — narrow via ``callable(...)`` above, then
            # ``cast`` so ty knows the call is safe. Adb tap helpers stuff a
            # ``Callable[[dict[str, object]], None]`` in this slot.
            cast("Callable[[dict[str, object]], None]", preview_capturer)(target)
        except Exception:
            logger.debug("approval preview refresh failed for %s", instance_id, exc_info=True)
            return
        last_preview_refresh_at = now

    hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
    if not _r_get(hb_key):
        # Approval always required — wait until the approvals page is opened.
        logger.info(
            "Click approval: page not open, waiting for operator to open it (%s)", instance_id
        )
        while not _r_get(hb_key):
            _refresh_preview_if_due(payload)
            time.sleep(_APPROVAL_POLL_SECONDS)
        logger.info("Click approval: page opened — proceeding (%s)", instance_id)

    current_key = f"wos:ui:click_approval:current:{instance_id}"

    req_id = f"adb:{instance_id}:{uuid.uuid4().hex[:12]}"
    resp_key = f"wos:ui:click_approval:response:{req_id}"

    # Attach context for debugging ("who" + "why").
    ctx: dict[str, object] = {}
    payload_type = ""
    approval_source = ""
    approval_context: dict[str, object] = {}
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "").strip().lower()
        approval_source = str(payload.get("approval_source") or "").strip().lower()
        raw_approval_context = payload.get("approval_context")
        if isinstance(raw_approval_context, dict):
            approval_context = cast("dict[str, object]", dict(raw_approval_context))
    try:
        inst_state_key = f"wos:instance:{instance_id}:state"
        raw = _r_hgetall(inst_state_key)
        if raw:
            # ``current_task_region`` is the task-level region (set by the worker once
            # per task item). For screen-node updates it is irrelevant: they only
            # update ``current_screen`` and never tap a region. Including
            # the stale value here would make the approvals UI draw a misleading
            # region overlay carried over from the previous step.
            task_region = (raw.get("current_task_region") or "").strip()
            if payload_type == "set_node" or approval_source == "navigation":
                task_region = ""
            ctx = {
                "current_screen": (raw.get("current_screen") or "").strip(),
                "current_task_id": (raw.get("current_task_id") or "").strip(),
                "current_task_type": (raw.get("current_task_type") or "").strip(),
                "current_task_player": (raw.get("current_task_player") or "").strip(),
                "current_task_started_at": (
                    raw.get("current_task_started_at") or ""
                ).strip(),
                "current_task_region": task_region,
                "current_task_threshold": (raw.get("current_task_threshold") or "").strip(),
                "current_task_score": (raw.get("current_task_score") or "").strip(),
                "current_task_text": (raw.get("current_task_text") or "").strip(),
                "current_task_confidence": (raw.get("current_task_confidence") or "").strip(),
                "current_task_template_bright_ratio": (
                    raw.get("current_task_template_bright_ratio") or ""
                ).strip(),
                "current_task_patch_bright_ratio": (
                    raw.get("current_task_patch_bright_ratio") or ""
                ).strip(),
                "current_task_match_top_left_x": (
                    raw.get("current_task_match_top_left_x") or ""
                ).strip(),
                "current_task_match_top_left_y": (
                    raw.get("current_task_match_top_left_y") or ""
                ).strip(),
                "current_task_template_w": (raw.get("current_task_template_w") or "").strip(),
                "current_task_template_h": (raw.get("current_task_template_h") or "").strip(),
                "current_task_tap_match_x_pct": (
                    raw.get("current_task_tap_match_x_pct") or ""
                ).strip(),
                "current_task_tap_match_y_pct": (
                    raw.get("current_task_tap_match_y_pct") or ""
                ).strip(),
                # YAML scenario key while a `DslScenarioTask` is running.
                "scenario": (raw.get("current_scenario") or "").strip(),
            }
            if approval_source:
                ctx["approval_source"] = approval_source
            if approval_context:
                for k, v in approval_context.items():
                    ctx[f"approval_{k}"] = str(v).strip()
            for audit_k in _DSL_APPROVAL_AUDIT_KEYS:
                ctx[audit_k] = (raw.get(audit_k) or "").strip()
            # ``last_overlay_*`` fields are global "most recent overlay match"
            # snapshots written by the overlay engine — they're NOT scoped to
            # the currently running task. Only borrow them as fallbacks when
            # the overlay rule that wrote them matched THIS task's region.
            # Without this guard, a ``tap_reconnect_button`` approval picks up
            # stale ``"Appoint Survivor..."`` text from the previous overlay
            # cycle and the operator gets misleading context.
            last_overlay_region = (raw.get("last_overlay_match_region") or "").strip()
            overlay_fb_safe = bool(task_region) and last_overlay_region == task_region
            if overlay_fb_safe:
                if not ctx["current_task_threshold"]:
                    fb = (raw.get("last_overlay_match_threshold") or "").strip()
                    if fb:
                        ctx["current_task_threshold"] = fb
                if not ctx["current_task_score"]:
                    fb = (raw.get("last_overlay_match_score") or "").strip()
                    if fb:
                        ctx["current_task_score"] = fb
                if not ctx["current_task_text"]:
                    fb = (raw.get("last_overlay_text") or "").strip()
                    if fb:
                        ctx["current_task_text"] = fb
                if not ctx["current_task_confidence"]:
                    fb = (raw.get("last_overlay_confidence") or "").strip()
                    if fb:
                        ctx["current_task_confidence"] = fb
                if not ctx["current_task_template_bright_ratio"]:
                    fb = (raw.get("last_overlay_template_bright_ratio") or "").strip()
                    if fb:
                        ctx["current_task_template_bright_ratio"] = fb
                if not ctx["current_task_patch_bright_ratio"]:
                    fb = (raw.get("last_overlay_patch_bright_ratio") or "").strip()
                    if fb:
                        ctx["current_task_patch_bright_ratio"] = fb
            last_match_region = (raw.get("dsl_last_match_region") or "").strip()
            if last_match_region and last_match_region == task_region:
                fallback_fields = {
                    "current_task_match_top_left_x": "dsl_last_match_top_left_x",
                    "current_task_match_top_left_y": "dsl_last_match_top_left_y",
                    "current_task_template_w": "dsl_last_match_template_w",
                    "current_task_template_h": "dsl_last_match_template_h",
                    "current_task_tap_match_x_pct": "dsl_last_match_tap_match_x_pct",
                    "current_task_tap_match_y_pct": "dsl_last_match_tap_match_y_pct",
                }
                for ctx_key, raw_key in fallback_fields.items():
                    if not ctx.get(ctx_key):
                        fb = (raw.get(raw_key) or "").strip()
                        if fb:
                            ctx[ctx_key] = fb
    except Exception:
        ctx = {}

    # Fixed-coordinate taps may pass ``region`` on the payload — mirror into ``context``
    # so the approvals page shows a label even when Redis ``current_task_region`` is still empty.
    ar_hint = ""
    if isinstance(payload, dict):
        ar_hint = str(payload.get("region") or "").strip()
    if ar_hint:
        ctx = dict(ctx)
        ctx["approval_region"] = ar_hint

    # Drop empty strings — every Redis hash field is materialized as ``""``
    # when missing, and pre-populating all 30+ audit keys floods the payload
    # with noise. UI consumers everywhere read via ``ctx.get(k) or ""`` so
    # absent and empty are interchangeable. Keep ``"0"`` and other falsy-but-
    # meaningful strings (e.g. ``current_task_patch_bright_ratio: "0"``).
    ctx = {k: v for k, v in ctx.items() if not (isinstance(v, str) and v == "")}

    p = dict(payload)
    # ``_preview_capturer`` is a private "refresh this payload's preview"
    # callback the caller attaches via ``_approval_payload_with_preview``. We
    # invoke it RIGHT BEFORE serialising for publish so the screenshot the
    # operator sees matches the screen at decision time — not a stale frame
    # captured at the start of phase-1's possibly-second-long publish wait.
    # Pop it off the dict so it never gets JSON-serialised into Redis.
    p.pop("_preview_capturer", None)
    p.pop("source", None)
    p.update(
        {
            "request_id": req_id,
            "instance_id": instance_id,
            "created_at": time.time(),
            "status": "waiting",
            "response_key": resp_key,
            "context": ctx,
        }
    )

    try:
        from config.tracing import inject_context_into

        inject_context_into(p)
    except ImportError:
        pass

    _clear_stale_approval_current(
        instance_id=instance_id,
        current_key=current_key,
        new_context=ctx,
    )
    _redis().delete(resp_key)
    started_at = time.time()
    # Phase 1: try to publish the request into the per-instance "current" slot.
    # ``nx=True`` so we never overwrite an in-flight approval for this instance.
    # This is bounded ONLY by ``_APPROVAL_PUBLISH_WAIT_SECONDS`` because it is
    # not waiting on the operator — only on the previous request to clear.
    publish_deadline = started_at + _APPROVAL_PUBLISH_WAIT_SECONDS
    while time.time() < publish_deadline:
        # Refresh preview + created_at on every retry: if the slot was held
        # for several poll intervals, the cached preview captured at
        # ``_attach_approval_preview`` time is already drifting. Re-capture so
        # the published payload always carries a recent screenshot.
        _refresh_preview_if_due(p, force=True)
        p["created_at"] = time.time()
        if _redis().set(
            current_key,
            json.dumps(p),
            nx=True,
        ):
            break
        time.sleep(_APPROVAL_POLL_SECONDS)
    else:
        logger.info("ADB input blocked: approval slot busy for %s", instance_id)
        _record_approval_block(
            instance_id,
            req_id=None,
            reason="publish_slot_busy",
            detail=current_key,
        )
        _emit_approval_timeline(
            instance_id,
            event="approval.blocked",
            req_id=None,
            reason="publish_slot_busy",
            detail=current_key,
            ctx=ctx,
        )
        return False, None

    # Phase 2: wait for an operator decision. There is NO wall-clock timeout
    # AND NO heartbeat-loss abort — the decision is always the operator's.
    # The loop only exits when:
    #   - ``response_key`` is set to "approve" / "reject" by the UI;
    #   - a foreign request_id has taken over the slot (treated as rejected,
    #     since the slot can only be reused by another request after this
    #     ``current`` key has been explicitly cleared or has expired).
    #
    # The UI deletes ``current`` immediately after writing the response so the
    # preview clears; we therefore check ``response_key`` BEFORE inferring
    # "reject" from a foreign / missing ``current`` payload.
    decision: str | None = None
    decision_reason = ""
    decision_detail = ""
    missing_current_republishes = 0
    while True:
        raw_resp = _r_get(resp_key)
        if raw_resp:
            decision = str(raw_resp).strip().lower()
            decision_reason = f"response:{decision}"
            break
        try:
            raw_cur = _r_get(current_key)
            if not raw_cur:
                if not _approval_owner_still_current(instance_id, ctx):
                    decision = "reject"
                    decision_reason = "current_missing_owner_changed"
                    decision_detail = req_id
                    logger.warning(
                        "Click approval: current request disappeared for %s and owner changed "
                        "(req=%s); not republishing stale request",
                        instance_id,
                        req_id,
                    )
                    break
                if missing_current_republishes < _APPROVAL_MISSING_CURRENT_REPUBLISH_LIMIT:
                    missing_current_republishes += 1
                    p["created_at"] = time.time()
                    p["republished_at"] = time.time()
                    if _redis().set(current_key, json.dumps(p), nx=True):
                        logger.warning(
                            "Click approval: current request disappeared for %s "
                            "(req=%s); republished same request (%d/%d)",
                            instance_id,
                            req_id,
                            missing_current_republishes,
                            _APPROVAL_MISSING_CURRENT_REPUBLISH_LIMIT,
                        )
                        _emit_approval_timeline(
                            instance_id,
                            event="approval.republished",
                            req_id=req_id,
                            reason="current_missing",
                            detail=f"{missing_current_republishes}/{_APPROVAL_MISSING_CURRENT_REPUBLISH_LIMIT}",
                            ctx=ctx,
                        )
                    time.sleep(_APPROVAL_POLL_SECONDS)
                    continue
                decision = "reject"
                decision_reason = "current_missing_after_republish"
                decision_detail = f"limit={_APPROVAL_MISSING_CURRENT_REPUBLISH_LIMIT}"
                break
            cur_doc = json.loads(raw_cur)
            cur_req_id = str(cur_doc.get("request_id") or "")
            if cur_req_id != req_id:
                decision = "reject"
                decision_reason = "foreign_request"
                decision_detail = cur_req_id
                logger.warning(
                    "Click approval: request for %s replaced by foreign request "
                    "(ours=%s foreign=%s)",
                    instance_id,
                    req_id,
                    cur_req_id,
                )
                break
        except Exception:
            logger.debug("Failed to read current approval request", exc_info=True)

        # Refresh preview/payload without a TTL. Waiting approval requests must
        # not silently expire while approval mode is on; owner mismatch / worker
        # boot cleanup are responsible for clearing invalid requests.
        try:
            _refresh_preview_if_due(p)
            raw_cur = _r_get(current_key)
            if raw_cur and json.loads(raw_cur).get("request_id") == req_id:
                _redis().set(
                    current_key,
                    json.dumps(p),
                )
        except Exception:
            logger.debug("Failed to refresh current approval payload", exc_info=True)

        time.sleep(_APPROVAL_POLL_SECONDS)

    if decision in {"approve", "reject", "skip"}:
        # Persist decision time on the current payload for UI/debug.
        try:
            raw_cur = _r_get(current_key)
            if raw_cur:
                doc = json.loads(raw_cur)
                if doc.get("request_id") == req_id:
                    doc["decision"] = decision
                    doc["approved_at"] = time.time() if decision == "approve" else None
                    doc["rejected_at"] = time.time() if decision == "reject" else None
                    doc["skipped_at"] = time.time() if decision == "skip" else None
                    doc["status"] = {
                        "approve": "approved",
                        "reject": "rejected",
                        "skip": "skipped",
                    }[decision]
                    _redis().set(
                        current_key,
                        json.dumps(doc),
                        ex=APPROVAL_CURRENT_TTL_SECONDS,
                    )
        except Exception:
            logger.debug("Failed to mark decision timestamps", exc_info=True)

    # On reject/skip/timeout, clear slot so the bot can proceed.
    if decision != "approve":
        _record_approval_block(
            instance_id,
            req_id=req_id,
            reason=decision_reason or f"decision:{decision or 'unknown'}",
            detail=decision_detail,
        )
        _emit_approval_timeline(
            instance_id,
            event="approval.blocked",
            req_id=req_id,
            reason=decision_reason or f"decision:{decision or 'unknown'}",
            detail=decision_detail,
            ctx=ctx,
        )
        logger.info(
            "Click approval blocked input for %s: decision=%s reason=%s req=%s",
            instance_id,
            decision,
            decision_reason or "unknown",
            req_id,
        )
        try:
            raw_cur = _r_get(current_key)
            if raw_cur and json.loads(raw_cur).get("request_id") == req_id:
                _redis().delete(current_key)
            _redis().delete(resp_key)
        except Exception:
            logger.debug("approval cleanup failed", exc_info=True)

    # Operator-skipped: queue the req_id so the next call to
    # ``_consume_skip`` (from tap()/swipe()/type_text()/set_node handler)
    # short-circuits the ADB action while still returning ok=True to keep
    # the caller from aborting the scenario.
    if decision == "skip" and req_id:
        _skipped_req_ids.add(req_id)

    # Operator-rejected: stamp a per-instance timestamp the scenario executor
    # uses to tell "navigation failed because the operator pressed Reject" from
    # "navigation failed because the route or verify broke". Without this
    # signal both paths land in the same nav-failed branch that blanks
    # ``current_screen`` — for a reject the screen identity is still valid
    # (no tap fired), so blanking it forces a needless re-detection cycle.
    if decision == "reject":
        try:
            _redis().hset(
                f"wos:instance:{instance_id}:state",
                "last_approval_reject_at",
                str(time.time()),
            )
        except Exception:
            logger.debug("Failed to record last_approval_reject_at", exc_info=True)

    return decision in {"approve", "skip"}, req_id


def _consume_skip(req_id: str | None) -> bool:
    """True (and pops the marker) when the most recent approval for
    ``req_id`` was an operator ``skip``. Callers use this between
    ``_require_approval`` returning ok=True and actually issuing the ADB
    action — skip means "treat as successful no-op, do not tap"."""
    if not req_id:
        return False
    if req_id in _skipped_req_ids:
        _skipped_req_ids.discard(req_id)
        return True
    return False


