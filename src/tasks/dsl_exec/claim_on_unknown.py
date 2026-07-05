"""``exec: claim_on_unknown`` — press a Claim/Collect button on an unknown screen.

The template-free popup detector (``exec: dismiss_popup``) localizes a modal and
taps its X or the *card centre*. That fails for reward pages whose green
**Claim** button sits *below* the white info card (e.g. the Labyrinth Treasure
detail popup): the card OCR carries no "claim" cue, so the page classifies as
``UNKNOWN_MODAL`` / ``no_popup`` and the bot stalls on it indefinitely.

This handler is the missing piece: a full-frame word-box OCR pass that *finds*
the Claim/Collect button by its label anywhere on the frame and taps it. It runs
inside ``dismiss_unknown_popup.yaml`` (``currentNode == unknown``, post-onboarding,
10s-stuck-gated) just before the detector fallback, so it only ever fires on a
screen the bot could not otherwise identify or dismiss.

Safety: a purchase guard aborts before any tap when the frame shows a price /
currency / buy cue — we never press a paid CTA labelled "Claim".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING

from layout.types import Point
from ocr import fuzzy
from tasks import dsl_runtime

if TYPE_CHECKING:
    from ocr.word_boxes import WordBox
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Reward-claim button labels (English + Russian «Белая мгла»). Whole-word fuzzy
# matched, so the body text of a card never trips them — only a button reading
# exactly one of these. Deliberately narrow: no "confirm"/"get"/"ok" which also
# label purchase / dialog CTAs.
_DEFAULT_CLAIM_CUES = (
    "claim",
    "collect",
    "claim all",
    "получить",
    "забрать",
    "собрать",
)

# A price ("$1.99", "1,99"), a currency glyph, or a buy cue → this is a paid
# offer, not a free reward. Mirrors ``popup.classify`` so the two agree.
_PRICE_RE = re.compile(r"\$?\d+[.,]\d{2}")
_CURRENCY_RE = re.compile(r"[$€£¥₩]")
_PURCHASE_CUES = ("usd", "buy", "purchase", "subscribe")

# Per-word match floor. High so a noisy OCR fragment doesn't masquerade as a
# claim label, low enough that "Claim"/"Collcct"-style slips still land.
_MATCH_THRESHOLD = 0.82
_MIN_CUE_LEN = 4

_DEFAULT_MAX_TAPS = 2
# Reward buttons are white text on a bright (green) fill, which tesseract reads
# at noticeably lower confidence than the dark card body text (~0.45-0.50 vs
# ~0.95). Keep the floor below that band so the Claim label survives; the narrow
# fuzzy vocabulary (0.82) + purchase guard prevent a stray low-conf word from
# ever being tapped.
_DEFAULT_MIN_CONF = 0.35
# Let the claim/reveal animation finish before re-reading the frame.
_SETTLE_S = 0.8


def _looks_like_purchase(full_text: str) -> bool:
    """True when the frame text carries a price, currency glyph, or buy cue."""
    text = full_text.lower()
    if _PRICE_RE.search(text) or _CURRENCY_RE.search(text):
        return True
    return any(cue in text for cue in _PURCHASE_CUES)


def _select_claim_target(
    boxes: list[WordBox],
    vocabulary: tuple[str, ...] | list[str],
    *,
    min_conf: float,
) -> WordBox | None:
    """Pick the Claim/Collect button word, or ``None``.

    Whole-word fuzzy match against ``vocabulary``; among matches the *lowest*
    on the frame wins (largest ``cy``). Action buttons sit at the bottom of a
    reward page, so this prefers the button over any body-text mention higher up.
    """
    vocab = list(vocabulary)
    matches: list[WordBox] = []
    for box in boxes:
        if box.conf < min_conf:
            continue
        candidate = " ".join(box.text.split()).lower()
        if len(candidate) < _MIN_CUE_LEN:
            continue
        if fuzzy.match(candidate, vocab, threshold=_MATCH_THRESHOLD) is not None:
            matches.append(box)
    if not matches:
        return None
    return max(matches, key=lambda b: b.cy)


async def _exec_claim_on_unknown(ctx: DslExecContext) -> None:
    """Find a Claim/Collect button by OCR on the current frame and tap it.

    Args (sibling YAML keys on the ``exec:`` step):
    * ``vocabulary`` — override the claim-label list.
    * ``max_taps`` — cap on stacked claims (default ``2``); a second pass catches
      a reward that reveals another Claim.
    * ``min_conf`` — per-word OCR confidence floor (default ``0.45``).
    """
    args = ctx.args or {}
    vocabulary = args.get("vocabulary") or _DEFAULT_CLAIM_CUES
    try:
        max_taps = max(1, int(args.get("max_taps", _DEFAULT_MAX_TAPS)))
    except (TypeError, ValueError):
        max_taps = _DEFAULT_MAX_TAPS
    try:
        min_conf = float(args.get("min_conf", _DEFAULT_MIN_CONF))
    except (TypeError, ValueError):
        min_conf = _DEFAULT_MIN_CONF

    actions = dsl_runtime.bot_actions()
    ocr = dsl_runtime.ocr_client()

    claimed = 0
    for tap_i in range(max_taps):
        if tap_i > 0:
            with contextlib.suppress(Exception):
                actions.invalidate_frame_cache(ctx.instance_id)
        try:
            image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
        except Exception:
            ctx.result.update({"reason": "capture_failed", "claim_action": "error"})
            logger.exception(
                "dsl exec claim_on_unknown: capture failed instance=%s",
                ctx.instance_id,
            )
            return

        try:
            boxes = await asyncio.to_thread(
                ocr.detect_word_boxes, image, min_conf=min_conf
            )
        except Exception:
            ctx.result.update({"reason": "ocr_failed", "claim_action": "error"})
            logger.exception(
                "dsl exec claim_on_unknown: ocr failed instance=%s",
                ctx.instance_id,
            )
            return

        full_text = " ".join(b.text for b in boxes)
        if _looks_like_purchase(full_text):
            ctx.result.update(
                {
                    "reason": "purchase_guard",
                    "claim_action": "skipped",
                    "claim_claimed": claimed,
                }
            )
            logger.info(
                "dsl exec claim_on_unknown: purchase cue on %s — not tapping",
                ctx.instance_id,
            )
            return

        target = _select_claim_target(boxes, vocabulary, min_conf=min_conf)
        if target is None:
            ctx.result.update(
                {
                    "reason": "claimed" if claimed else "no_claim_button",
                    "claim_action": "done" if claimed else "none",
                    "claim_claimed": claimed,
                }
            )
            return

        point = Point(target.cx, target.cy)
        ctx.result.update(
            {
                "claim_action": "tap_pending",
                "claim_text": target.text,
                "claim_tap_x": point.x,
                "claim_tap_y": point.y,
                "claim_claimed": claimed,
            }
        )
        try:
            tapped = bool(
                await asyncio.to_thread(
                    actions.tap,
                    ctx.instance_id,
                    point,
                    approval_region="button.claim",
                    approval_source="claim_on_unknown",
                )
            )
        except Exception:
            ctx.result.update({"reason": "tap_failed", "claim_action": "error"})
            logger.exception(
                "dsl exec claim_on_unknown: tap failed at (%d,%d) instance=%s",
                point.x,
                point.y,
                ctx.instance_id,
            )
            return

        if not tapped:
            ctx.result.update(
                {
                    "reason": "tap_rejected",
                    "claim_action": "blocked",
                    "claim_claimed": claimed,
                }
            )
            logger.info(
                "dsl exec claim_on_unknown: tap at (%d,%d) blocked/rejected on %s",
                point.x,
                point.y,
                ctx.instance_id,
            )
            return

        claimed += 1
        ctx.result.update(
            {
                "claim_action": "tapped",
                "claim_text": target.text,
                "claim_tap_x": point.x,
                "claim_tap_y": point.y,
                "claim_claimed": claimed,
            }
        )
        logger.info(
            "dsl exec claim_on_unknown: instance=%s tap=%d text=%r at (%d,%d)",
            ctx.instance_id,
            claimed,
            target.text,
            point.x,
            point.y,
        )
        await asyncio.sleep(_SETTLE_S)

    ctx.result.update(
        {
            "reason": "max_taps",
            "claim_action": "max_taps",
            "claim_claimed": claimed,
        }
    )
