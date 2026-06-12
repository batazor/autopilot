"""DSL ``exec:`` handler for the Dreamscape Memory recall-road levels.

The scenario OCRs the word buttons at the bottom of a level
(``dreamscape_memory.1`` / ``.2`` / ``.3``) into Redis, then calls
``exec: dreamscape_memory_solve``. This handler reads those words back, looks
each one up in the active scene's map (word -> scene coordinate, from the module
scene DB :mod:`config.dreamscape_db`) and taps the matching spot in the scene.
Lookup is exact-first, then fuzzy (``fuzz_threshold``) so OCR character errors
still resolve to the intended item.

Words with no exact or fuzzy map entry are logged and surfaced on ``ctx.result``
as ``unmapped`` so the operator knows what to add via the onboarding flow.

Discovered automatically by ``config.module_exec_registry`` (a module exec.py
with a ``DSL_EXEC_HANDLERS`` dict needs no wiring in ``module.yaml``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import TYPE_CHECKING, Any, NamedTuple

from rapidfuzz import fuzz, process

from config.paths import repo_root
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc
from layout.types import Point, Region
from ocr.preprocess import resolve_preprocess
from ocr.word_cleaning import is_plausible_word_text, normalize_word_text
from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec import DslExecContext

DslExecHandler = Callable[[Any], Awaitable[None]]

logger = logging.getLogger(__name__)

# Default OCR regions to read, in tap order. Override per-step with
# ``regions: [ ... ]`` on the ``exec:`` step.
_DEFAULT_REGIONS: tuple[str, ...] = (
    "dreamscape_memory.1",
    "dreamscape_memory.2",
    "dreamscape_memory.3",
)

# OCR region holding the on-screen level/scene name. Read to auto-select which
# scene map to solve (override per-step with ``level_region:``; empty disables
# name matching and falls back to the operator's active scene).
_DEFAULT_LEVEL_REGION = "dreamscape_memory.level.name"
_DEFAULT_HELP_REGION = "dreamscape_memory.help"
_DEFAULT_HELP_COUNTER_REGION = "dreamscape_memory.help.counter"
_DEFAULT_HELP_COUNT = 2

_DEFAULT_MULTIPLAYER_REGIONS: tuple[str, ...] = (
    "dreamscape_memory_.multiplayer.1",
    "dreamscape_memory_.multiplayer.2",
    "dreamscape_memory_.multiplayer.3",
    "dreamscape_memory_.multiplayer.4",
    "dreamscape_memory_.multiplayer.5",
    "dreamscape_memory_.multiplayer.6",
)

# Strip the season tag from a title ("Aquarium (S3)") / slug ("aquarium-s3") so
# it matches the bare on-screen level name ("Aquarium").
_SEASON_TAG_RE = re.compile(r"\s*\(s\d+\)\s*$", re.IGNORECASE)
_SLUG_SUFFIX_RE = re.compile(r"-(?:s\d+|mp)$", re.IGNORECASE)
_LEVEL_PROGRESS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%.*$", re.IGNORECASE)

# Extra pause between Dreamscape taps. BotActions already waits for a post-tap
# frame boundary before the next capture, so the solver itself should not
# serialize a visible word batch behind an additional sleep.
_DEFAULT_TAP_DELAY_S = 0.0
_DEFAULT_LOOP_TTL_S = 5 * 60.0
_DEFAULT_LOOP_WAIT_S = 0.3
_DEFAULT_LOOP_MAX_ITERATIONS = 3000
_DEFAULT_HELP_CAPTURE_DELAY_S = 0.12
_DEFAULT_HELP_DIFF_GAP_S = 0.12
_HELP_CAPTURE_FRAMES = 3
# After dispatching a tap we keep the slot ``determined`` and wait for the
# background-colour detector to confirm the pill greyed out before promoting it
# to ``clicked``. If the colour has not confirmed within this many iterations we
# re-tap (the tap likely missed), up to ``_DEFAULT_MAX_TAP_ATTEMPTS`` total taps
# before giving up and surfacing the slot as ``rejected`` (a bad map coordinate
# should be visible, not spin forever or masquerade as clicked).
_DEFAULT_TAP_CONFIRM_WAIT_ITERS = 2
_DEFAULT_MAX_TAP_ATTEMPTS = 3
_DEFAULT_WORD_OCR_THRESHOLD = 0.0
_DEFAULT_BATCH_REOPEN_OCR_PROBE_ITERS = 2
_MAX_LIVE_EVENTS = 120
_MIN_UNMAPPED_WORD_LETTERS = 3
# An unmapped word must be read on this many separate iterations before it is
# allowed to spend a (slow, irreversible) helper tap + scene-DB learn. A single
# transient read — e.g. OCR of an animating slot — is never enough; the slot
# usually settles into a real, mappable word on the next frame.
_MIN_UNMAPPED_CONFIRM_READS = 2
_FOUND_WORD_DARK_PIXEL_THRESHOLD = 100
_FOUND_WORD_MIN_MEAN_GRAY = 70
_FOUND_WORD_MIN_DARK_RATIO = 0.035
_FOUND_WORD_MIN_DARK_ROW_RATIO = 0.16
# A word pill has only two states: active (vivid lavender chrome) and found
# (greyed out / desaturated). The pill-background saturation separates them
# cleanly and, unlike the strike-through, does not flicker with the strike-in
# animation — so it is the primary "found" signal. Observed medians: active
# ~124-138, found ~92. The floor rejects near-greyscale non-pill crops (a black
# or washed-out region reads saturation ~0).
_FOUND_WORD_BG_SAT_MIN = 55
_FOUND_WORD_BG_SAT_MAX = 108
# Pixel-based round-start gate (multiplayer). Before the round starts the
# screen sits behind a dark shade and every word pill reads ~0 bright pixels;
# the instant the shade lifts the pills appear with hundreds of near-white
# pixels each (measured on real frames: dark ≈ 0 px / live ≈ 100–1800 px per
# slot at gray ≥ 200). Gating the loop on this check instead of OCR gives a
# near-0-latency round start and skips burning OCR cycles in the lobby.
_START_GATE_BRIGHT_THRESHOLD = 200
_START_GATE_MIN_BRIGHT_PX = 60
_START_GATE_MIN_LIT_SLOTS = 2
_DEFAULT_START_GATE_WAIT_S = 0.1

_LIVE_STATE_FIELD = "dreamscape_memory.solve_state"
_START_SCREEN = "dreamscape_memory"
_TERMINAL_TIME_UP = "dreamscape_memory.time_up"
_TERMINAL_ALL_FOUND = "dreamscape_memory.all_item_found"
_TERMINAL_SCREENS = frozenset({_START_SCREEN, _TERMINAL_TIME_UP, _TERMINAL_ALL_FOUND})
_WIN_TERMINAL_SCREENS = frozenset({_START_SCREEN, _TERMINAL_ALL_FOUND})

# Minimum rapidfuzz WRatio (0–100) for an OCR'd word to be corrected to a mapped
# item when the exact normalized key misses. OCR garbles characters ("Lightening"
# for "Lightning", "Snowmann" for "Snowman"); fuzzy recovery taps them anyway.
# High enough to keep near-collisions (e.g. "Cart"/"Cat") apart. Override per-step
# with ``fuzz_threshold:`` on the ``exec:`` step; ``0`` disables fuzzy matching.
_DEFAULT_FUZZ_THRESHOLD = 88.0
_DEFAULT_FUZZ_AMBIGUITY_MARGIN = 5.0


# ── Pure helpers (unit-tested) ──────────────────────────────────────────────


class TapCandidate(NamedTuple):
    raw_word: str
    raw_key: str
    key: str
    point: Point
    region: str = ""


class PendingClick(NamedTuple):
    key: str
    raw_key: str
    raw_word: str
    point: Point


class SlotFsmState(NamedTuple):
    status: str
    raw_word: str = ""
    raw_key: str = ""
    key: str = ""
    point: Point | None = None


class FuzzyLookup(NamedTuple):
    key: str | None
    ambiguous: bool = False


class HelpTargetTap(NamedTuple):
    word: str
    point: Point


class HelpMotionCandidate(NamedTuple):
    point: Point
    score: float


def _append_event(
    events: list[dict[str, Any]],
    kind: str,
    message: str,
    *,
    iteration: int | None = None,
    **fields: Any,
) -> None:
    event: dict[str, Any] = {
        "at": round(time.time(), 3),
        "kind": kind,
        "message": message,
    }
    if iteration is not None:
        event["iteration"] = iteration
    for key, value in fields.items():
        if value not in (None, "", [], {}, set()):
            event[key] = sorted(value) if isinstance(value, set) else value
    events.append(event)
    if len(events) > _MAX_LIVE_EVENTS:
        del events[: len(events) - _MAX_LIVE_EVENTS]


def _terminal_screen_is_valid(screen: str, *, taps_total: int) -> bool:
    """Guard stale win screens from a previous run before gameplay starts."""
    return screen not in _WIN_TERMINAL_SCREENS or taps_total > 0


_SLOT_UNKNOWN = "unknown"
_SLOT_MAPPED = "mapped"
_SLOT_CLICKED = "clicked"
_SLOT_SETTLED = "settled"
_SLOT_UNMAPPED = "unmapped"
_SLOT_HELP_REQUESTED = "help_requested"
_SLOT_HELP_DETECTING = "help_detecting"
_SLOT_RETRY_EXHAUSTED = "retry_exhausted"
_SLOT_TAP_REJECTED = "tap_rejected"


def _public_slot_fsm_status(state: SlotFsmState | None) -> str:
    if state is None or state.status == _SLOT_UNKNOWN:
        return "unknown"
    # ``determined`` covers a mapped slot whether or not a tap is already in
    # flight: the tap stays "determined" until the background colour confirms it.
    if state.status == _SLOT_MAPPED:
        return "determined"
    # ``clicked`` is reached ONLY after the background colour confirms our tap
    # greyed the pill — never on tap dispatch.
    if state.status == _SLOT_CLICKED:
        return "clicked"
    if state.status == _SLOT_SETTLED:
        return "found"
    if state.status == _SLOT_HELP_REQUESTED:
        return "help_requested"
    if state.status == _SLOT_HELP_DETECTING:
        return "detecting_on_map"
    # A tap that was rejected on dispatch, or that the colour never confirmed
    # after the retry budget, surfaces as ``rejected`` (never ``clicked``).
    if state.status in {_SLOT_TAP_REJECTED, _SLOT_RETRY_EXHAUSTED}:
        return "rejected"
    return "unknown"


def _set_slot(
    slot_states: dict[str, SlotFsmState],
    region: str,
    new_state: SlotFsmState | None,
    *,
    events: list[dict[str, Any]],
    iteration: int,
    instance_id: str = "",
) -> None:
    """Set (or clear, when ``new_state is None``) a slot's FSM state.

    Every per-slot state mutation goes through here so that each word's lifecycle
    transition (``unknown -> determined -> clicked -> found``, plus
    helper/reject/reopen edges) is written to the log and the live event feed the
    moment the public status changes. Routing all writes through one seam keeps
    the state machine observable — a regression (e.g. a found slot flipping back
    to determined) shows up as an explicit transition line, not a silent state
    overwrite. Same-status writes (only the word/point changed) are not logged.
    """
    prev = slot_states.get(region)
    if new_state is None:
        slot_states.pop(region, None)
    else:
        slot_states[region] = new_state
    prev_status = _public_slot_fsm_status(prev)
    new_status = _public_slot_fsm_status(new_state)
    if prev_status == new_status:
        return
    word = (new_state.raw_word if new_state else "") or (prev.raw_word if prev else "")
    logger.info(
        "dreamscape_memory_solve_loop: slot %s: %s -> %s word=%r instance=%s",
        region,
        prev_status,
        new_status,
        word,
        instance_id,
    )
    _append_event(
        events,
        "slot_state",
        f"{region}: {prev_status} → {new_status}" + (f" ({word})" if word else ""),
        iteration=iteration,
        region=region,
        word=word,
        from_status=prev_status,
        to_status=new_status,
    )


def _normalize_word(raw: object) -> str:
    """Lower-case, trim, and collapse inner whitespace for stable map keys."""
    return normalize_word_text(raw)


def _is_actionable_unmapped_word(raw: object) -> bool:
    # Require a minimum letter count AND reject OCR noise (repeated-char runs,
    # all-vowel/all-consonant junk) so garbage reads never reach the costly
    # helper-learn flow or get persisted into the scene DB.
    return is_plausible_word_text(raw, min_letters=_MIN_UNMAPPED_WORD_LETTERS)


def _looks_like_clicked_word_noise(current_key: str, pending: PendingClick) -> bool:
    return _word_keys_overlap(current_key, pending.raw_key, pending.key)


def _tap_signature(region: str, key: str, point: Point) -> tuple[str, str, int, int]:
    return (region, key, point.x, point.y)


def _discard_tap_signatures_for_region(
    signatures: set[tuple[str, str, int, int]],
    region: str,
) -> None:
    signatures.difference_update(sig for sig in signatures if sig[0] == region)


def _word_keys_overlap(current_key: str, *previous_keys: str) -> bool:
    current = re.sub(r"\s+", "", current_key)
    previous = [re.sub(r"\s+", "", key) for key in previous_keys if key]
    return bool(
        current
        and any(
            key in current or (current in key and len(current) >= 3)
            for key in previous
        )
    )


def _normalize_level_name(raw: object) -> str:
    """Normalize OCR'd level titles for scene lookup.

    Dreamscape title OCR often sees separators/progress as text
    (``Practice|Level · 23%``). For scene selection, keep only searchable
    alphanumeric words so that noisy UI chrome does not block an exact match.
    """
    s = str(raw or "").casefold()
    s = _LEVEL_PROGRESS_RE.sub(" ", s)
    s = re.sub(r"(?<=[a-z])[\|/\\]+(?=[a-z])", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _parse_help_counter(raw: object) -> int | None:
    match = re.search(r"\d+", str(raw or ""))
    if match is None:
        return None
    try:
        return max(0, int(match.group(0)))
    except ValueError:
        return None


def _scene_rect(raw: object) -> tuple[float, float, float, float] | None:
    """Parse a ``scene_rect`` (% of game frame) into ``(left, top, w, h)``.

    Returns ``None`` (identity mapping) when absent or malformed — the points
    are then taken as direct game-frame percentages.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return (
            float(raw["left"]),
            float(raw["top"]),
            float(raw["width"]),
            float(raw["height"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("dreamscape_memory_solve: skipping malformed scene_rect %r", raw)
        return None


def _points_to_targets(
    points: object,
    scene_rect: tuple[float, float, float, float] | None = None,
) -> dict[str, tuple[float, float]]:
    """Parse ``[{n, name, xPct, yPct}]`` into ``{normalized_word: (x_pct, y_pct)}``.

    Coordinates are guide-image percentages; when ``scene_rect`` (where the
    scene art sits in the 720x1280 game frame) is given they are mapped into
    game-frame percentages: ``frame = rect_origin + guide/100 * rect_size``.
    With no rect the points are used as-is. Malformed entries are skipped and
    logged rather than aborting the whole solve.
    """
    if not isinstance(points, list):
        return {}
    out: dict[str, tuple[float, float]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        key = _normalize_word(point.get("name"))
        if not key:
            continue
        try:
            x_pct = float(point["xPct"])
            y_pct = float(point["yPct"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "dreamscape_memory_solve: skipping malformed point %r", point
            )
            continue
        if scene_rect is not None:
            left, top, width, height = scene_rect
            x_pct = left + x_pct / 100.0 * width
            y_pct = top + y_pct / 100.0 * height
        out[key] = (x_pct, y_pct)
    return out


def _load_targets() -> dict[str, tuple[float, float]]:
    """Load the active scene's ``{normalized_word: (x_pct, y_pct)}`` from the DB.

    Scene maps live in the module's scene database (:mod:`config.dreamscape_db`);
    exactly one scene is active. No active scene → empty targets (safe no-op).
    """
    from config.dreamscape_db import get_active_scene

    scene = get_active_scene()
    if not scene:
        return {}
    return _points_to_targets(scene.get("points"), _scene_rect(scene.get("scene_rect")))


def _targets_for_scene(scene: dict[str, Any] | None) -> dict[str, tuple[float, float]]:
    """``{normalized_word: (x_pct, y_pct)}`` for a scene detail (empty if None)."""
    if not scene:
        return {}
    return _points_to_targets(scene.get("points"), _scene_rect(scene.get("scene_rect")))


def _scene_base_name(title: object, slug: object) -> str:
    """Bare room name for matching the on-screen level name.

    Titles carry a season tag ("Aquarium (S3)") and slugs a "-s2"/"-s3"/"-mp"
    suffix; the OCR'd level name is the plain room name ("Aquarium").
    """
    name = _SEASON_TAG_RE.sub("", str(title or "")).strip()
    if name:
        return name
    return _SLUG_SUFFIX_RE.sub("", str(slug or "")).replace("-", " ").strip()


def _match_scene_slug(
    level_name: str,
    scenes: list[dict[str, Any]],
    *,
    prefer_season: int | None = None,
    fuzz_threshold: float = _DEFAULT_FUZZ_THRESHOLD,
) -> str | None:
    """Best scene slug for an OCR'd level name (exact-then-fuzzy on room name).

    A room reused across seasons (e.g. "Aquarium" in Season 1 and Season 3) is a
    tie; it breaks toward ``prefer_season`` (the live event, from the active
    scene), then the highest season number.
    """
    key = _normalize_level_name(level_name)
    if not key or not scenes:
        return None

    def rank(scene: dict[str, Any]) -> tuple[int, int]:
        season = int(scene.get("season") or 0)
        return (1 if season == prefer_season else 0, season)

    by_base: dict[str, list[dict[str, Any]]] = {}
    for scene in scenes:
        # A scene is reachable by its title-derived room name and by any
        # operator-supplied alternate name (the same room labeled differently
        # in-game). ``alt_titles`` holds the full list; ``alt_title`` is the
        # legacy single value (still honoured for older callers/tests).
        names = {
            _normalize_level_name(_scene_base_name(scene.get("title"), scene.get("slug")))
        }
        raw_aliases = scene.get("alt_titles")
        alt_values = list(raw_aliases) if isinstance(raw_aliases, list) else []
        if scene.get("alt_title"):
            alt_values.append(scene.get("alt_title"))
        for raw_alt in alt_values:
            alt = _normalize_level_name(
                _SEASON_TAG_RE.sub("", str(raw_alt or "")).strip()
            )
            if alt:
                names.add(alt)
        for base in names:
            if base:
                by_base.setdefault(base, []).append(scene)

    bucket = by_base.get(key) or (
        by_base.get(_fuzzy_key(key, list(by_base), fuzz_threshold) or "") or []
    )
    if not bucket:
        return None
    return str(max(bucket, key=rank)["slug"])


def _match_scene_by_words(
    words: list[str],
    scenes: list[dict[str, Any]],
    *,
    prefer_season: int | None = None,
) -> str | None:
    """Best scene slug for the set of on-screen item words (3→2→1 overlap).

    Thin wrapper over :func:`config.dreamscape_db.match_scene_by_words` (the
    canonical matcher shared with the onboarding API) pinned to the solver's
    unmapped-word letter gate. See that function for the full algorithm.
    """
    from config import dreamscape_db

    return dreamscape_db.match_scene_by_words(
        words,
        scenes,
        prefer_season=prefer_season,
        min_letters=_MIN_UNMAPPED_WORD_LETTERS,
    )


def _resolve_scene(
    words: list[str], fuzz_threshold: float
) -> tuple[dict[str, Any] | None, bool]:
    """``(scene, locked)`` — detect the scene from the on-screen item words.

    The title detector is removed: the scene is identified by the set of words
    shown (see :func:`_match_scene_by_words`). ``locked`` is True only on a real
    word match, so the loop fixes onto it and stops re-detecting; when nothing
    matches we fall back to the operator's **active** scene (the manual override
    selected on the game page) *unlocked*, so detection retries on later frames.
    The active scene also supplies the preferred season for same-name tie-breaks.
    """
    from config import dreamscape_db

    active = dreamscape_db.get_active_scene()
    index = dreamscape_db.scene_word_index()
    prefer = int(active["season"]) if active and "season" in active else None
    slug = _match_scene_by_words(words, index["scenes"], prefer_season=prefer)
    if slug:
        scene = dreamscape_db.get_scene(slug)
        if scene:
            return scene, True
    return active, False


def _select_scene(words: list[str], fuzz_threshold: float) -> dict[str, Any] | None:
    """Scene to solve: detect from the item words, else the active (override) scene.

    This is the single seam tests patch to inject a fixed scene; keep all scene
    selection routed through it.
    """
    scene, _locked = _resolve_scene(words, fuzz_threshold)
    return scene


def _select_scene_ex(
    words: list[str], fuzz_threshold: float
) -> tuple[dict[str, Any] | None, bool]:
    """``(scene, locked)`` for callers that also need the lock flag.

    The scene is sourced through :func:`_select_scene` so a test that patches that
    seam stays isolated from the live DB, while the lock flag comes from the real
    word-set lookup.
    """
    scene = _select_scene(words, fuzz_threshold)
    _resolved, locked = _resolve_scene(words, fuzz_threshold)
    return scene, locked


def _fuzzy_key(
    key: str,
    choices: list[str],
    threshold: float,
) -> str | None:
    """Best fuzzy match for ``key`` among ``choices`` at/above ``threshold``.

    Recovers from OCR noise (a swapped/dropped character) when the exact key
    misses. Returns the matched choice, or ``None`` when fuzzy matching is off
    (``threshold <= 0``), there are no choices, or nothing clears the cutoff.
    """
    return _fuzzy_lookup(key, choices, threshold).key


def _fuzzy_lookup(
    key: str,
    choices: list[str],
    threshold: float,
    *,
    ambiguity_margin: float = _DEFAULT_FUZZ_AMBIGUITY_MARGIN,
) -> FuzzyLookup:
    """Best fuzzy match, unless another choice is nearly as plausible."""
    if threshold <= 0 or not choices:
        return FuzzyLookup(None)
    matches = process.extract(key, choices, scorer=fuzz.WRatio, limit=2)
    if not matches:
        return FuzzyLookup(None)

    best_key, best_score, _best_idx = matches[0]
    if best_score < threshold:
        return FuzzyLookup(None)
    if len(matches) > 1:
        second_key, second_score, _second_idx = matches[1]
        if best_score - second_score < ambiguity_margin:
            logger.info(
                "dreamscape_memory_solve: fuzzy match for %r is ambiguous: "
                "%r=%.1f vs %r=%.1f",
                key,
                best_key,
                best_score,
                second_key,
                second_score,
            )
            return FuzzyLookup(None, ambiguous=True)
    return FuzzyLookup(str(best_key))


def _resolve_taps(
    words: list[str],
    targets: dict[str, tuple[float, float]],
    dev_w: int,
    dev_h: int,
    *,
    fuzz_threshold: float = _DEFAULT_FUZZ_THRESHOLD,
) -> tuple[list[tuple[str, Point]], list[str]]:
    """Split OCR'd words into (word, tap-point) hits and unmapped misses.

    An exact normalized-key lookup is tried first; on a miss the word is fuzzy
    matched against the mapped item names (``fuzz_threshold``, 0 disables) to
    absorb OCR character errors. Percentage coordinates are converted to device
    pixels the same way the DSL click step does: ``px = pct / 100 * dimension``.
    """
    candidates, misses = _resolve_tap_candidates(
        words, targets, dev_w, dev_h, fuzz_threshold=fuzz_threshold
    )
    return [(c.raw_word, c.point) for c in candidates], misses


def _resolve_tap_candidates(
    words: list[str],
    targets: dict[str, tuple[float, float]],
    dev_w: int,
    dev_h: int,
    *,
    fuzz_threshold: float = _DEFAULT_FUZZ_THRESHOLD,
) -> tuple[list[TapCandidate], list[str]]:
    """Resolve OCR'd words and keep the canonical target key for de-duping."""
    candidates, misses = _resolve_region_tap_candidates(
        [("", word) for word in words],
        targets,
        dev_w,
        dev_h,
        fuzz_threshold=fuzz_threshold,
    )
    return candidates, [word for _region, word in misses]


def _resolve_region_tap_candidates(
    word_items: list[tuple[str, str]],
    targets: dict[str, tuple[float, float]],
    dev_w: int,
    dev_h: int,
    *,
    fuzz_threshold: float = _DEFAULT_FUZZ_THRESHOLD,
) -> tuple[list[TapCandidate], list[tuple[str, str]]]:
    """Resolve ``(region, OCR word)`` pairs while preserving the source slot."""
    candidates: list[TapCandidate] = []
    misses: list[tuple[str, str]] = []
    choices = list(targets)
    for region, word in word_items:
        raw_key = _normalize_word(word)
        if not raw_key:
            continue
        coord = targets.get(raw_key)
        target_key = raw_key
        if coord is None:
            lookup = _fuzzy_lookup(raw_key, choices, fuzz_threshold)
            if lookup.key is not None:
                logger.info(
                    "dreamscape_memory_solve: fuzzy-matched %r -> %r",
                    word,
                    lookup.key,
                )
                coord = targets[lookup.key]
                target_key = lookup.key
            elif lookup.ambiguous:
                logger.info(
                    "dreamscape_memory_solve: skipping ambiguous OCR word %r",
                    word,
                )
                continue
        if coord is None:
            misses.append((region, word))
            continue
        x_pct, y_pct = coord
        point = Point(
            int(round(x_pct / 100.0 * dev_w)),
            int(round(y_pct / 100.0 * dev_h)),
        )
        candidates.append(
            TapCandidate(
                raw_word=word,
                raw_key=raw_key,
                key=target_key,
                point=point,
                region=region,
            )
        )
    return candidates, misses


def _parse_duration_s(raw: object, default: float) -> float:
    """Parse DSL-ish durations (``300ms``, ``5m``, number seconds)."""
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return max(0.0, float(raw))
    s = str(raw or "").strip().lower()
    if not s:
        return default
    try:
        if s.endswith("ms"):
            return max(0.0, float(s[:-2].strip()) / 1000.0)
        if s.endswith("s"):
            return max(0.0, float(s[:-1].strip()))
        if s.endswith("m"):
            return max(0.0, float(s[:-1].strip()) * 60.0)
        if s.endswith("h"):
            return max(0.0, float(s[:-1].strip()) * 3600.0)
        return max(0.0, float(s))
    except ValueError:
        return default


def _load_area() -> dict[str, Any]:
    return load_area_doc(repo_root())


def _region_to_px(region_def: dict[str, Any], frame_w: int, frame_h: int) -> Region | None:
    bbox = region_def.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        x = int(round(float(bbox["x"]) / 100.0 * frame_w))
        y = int(round(float(bbox["y"]) / 100.0 * frame_h))
        w = int(round(float(bbox["width"]) / 100.0 * frame_w))
        h = int(round(float(bbox["height"]) / 100.0 * frame_h))
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return Region(x, y, w, h)


def _threshold(region_def: dict[str, Any]) -> float:
    try:
        return float(region_def.get("threshold", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _region_center_for_frame(
    area_doc: dict[str, Any],
    name: str,
    frame_w: int,
    frame_h: int,
) -> Point | None:
    pair = screen_region_by_name(area_doc, name)
    region_def = pair[1] if pair else None
    if not isinstance(region_def, dict):
        return None
    px = _region_to_px(region_def, frame_w, frame_h)
    return px.center() if px is not None else None


def _word_pill_background_saturation(crop: Any) -> float | None:
    """Median HSV saturation of a word pill's background fill.

    Sampled from two short vertical strips just inside the pill's left and right
    ends, vertically centred and inset from the rounded corners. The centred word
    text never reaches there, so this reads the pill chrome itself rather than the
    letters: an active pill keeps the vivid lavender fill (high saturation), while
    a found/struck pill is greyed out (low saturation). Frame-stable — unlike the
    strike-through it does not depend on the word or the strike-in animation.
    """
    if crop is None or not hasattr(crop, "shape") or len(crop.shape) != 3:
        return None
    height, width = int(crop.shape[0]), int(crop.shape[1])
    if width < 20 or height < 8:
        return None
    try:
        import cv2
        import numpy as np
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: cv2/numpy unavailable", exc_info=True)
        return None
    y1, y2 = int(round(height * 0.25)), int(round(height * 0.75))
    sat = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1]
    left = sat[y1:y2, int(round(width * 0.05)) : int(round(width * 0.16))]
    right = sat[y1:y2, int(round(width * 0.84)) : int(round(width * 0.95))]
    bands = np.concatenate([left.ravel(), right.ravel()])
    if bands.size == 0:
        return None
    return float(np.median(bands))


def _is_word_region_visually_found(crop: Any) -> bool:
    """True when a Dreamscape word pill is already greyed out (word found).

    The pill-background colour is the first gate: active chrome is usually vivid,
    while found/struck pills are desaturated. A selected active pill can also be
    desaturated, though, so the found colour must be confirmed by darkened text or
    a strike-through in the center band. A vivid background means the pill is
    active — return early without consulting the dark-text heuristic, since a
    long/dense active word can have enough dark letter pixels to trip it.
    """
    if crop is None or not hasattr(crop, "shape") or len(crop.shape) != 3:
        return False
    height, width = int(crop.shape[0]), int(crop.shape[1])
    if width < 20 or height < 8:
        return False
    try:
        import cv2
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: cv2 unavailable", exc_info=True)
        return False

    # Ignore rounded edges; the center band contains the dark strike-through
    # and darkened text when the word has already been found.
    x1 = int(round(width * 0.08))
    x2 = int(round(width * 0.92))
    y1 = int(round(height * 0.18))
    y2 = int(round(height * 0.82))
    inner = crop[y1:y2, x1:x2]
    if inner.size == 0:
        return False
    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    # A real pill (active or found) is bright; a dark/empty region is neither.
    if float(gray.mean()) < _FOUND_WORD_MIN_MEAN_GRAY:
        return False

    dark = gray < _FOUND_WORD_DARK_PIXEL_THRESHOLD
    dark_ratio = float(dark.mean())
    row_ratio = float(dark.mean(axis=1).max()) if dark.shape[0] else 0.0
    has_dark_strike = (
        dark_ratio >= _FOUND_WORD_MIN_DARK_RATIO
        and row_ratio >= _FOUND_WORD_MIN_DARK_ROW_RATIO
    )

    bg_sat = _word_pill_background_saturation(crop)
    if bg_sat is not None:
        if _FOUND_WORD_BG_SAT_MIN <= bg_sat <= _FOUND_WORD_BG_SAT_MAX:
            return has_dark_strike
        if bg_sat > _FOUND_WORD_BG_SAT_MAX:
            # Vivid lavender chrome → the pill is still active. Do NOT fall through
            # to the dark-text heuristic: a long, dense word ("Grilled Skewer")
            # has enough dark letter pixels to trip it, which would lock an active
            # slot as "found" so it is never OCR'd or tapped.
            return False

    # Fallback when the background sample is unreadable or unusually desaturated.
    return has_dark_strike


def _round_started_pixels(
    image: Any,
    area_doc: dict[str, Any],
    names: list[str],
) -> bool | None:
    """Pixel-only round-start check: are the word pills visible yet?

    Counts near-white pixels (gray ≥ ``_START_GATE_BRIGHT_THRESHOLD``) inside
    each word-slot region. Under the pre-round shade every slot reads ~0; once
    the shade lifts each active pill shows hundreds. The round is considered
    started when at least ``_START_GATE_MIN_LIT_SLOTS`` slots are lit.

    Returns ``None`` when the check cannot run (cv2 missing, no resolvable
    regions, bad frame) so the caller can fail open to the OCR loop.
    """
    if image is None or not hasattr(image, "shape") or len(image.shape) != 3:
        return None
    try:
        import cv2
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: cv2 unavailable", exc_info=True)
        return None
    frame_h, frame_w = int(image.shape[0]), int(image.shape[1])
    lit = 0
    checked = 0
    for name in names:
        pair = screen_region_by_name(area_doc, name)
        region_def = pair[1] if pair else None
        if not isinstance(region_def, dict):
            continue
        px = _region_to_px(region_def, frame_w, frame_h)
        if px is None:
            continue
        crop = image[px.y : px.y + px.h, px.x : px.x + px.w]
        if crop.size == 0:
            continue
        checked += 1
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, bright = cv2.threshold(
            gray, _START_GATE_BRIGHT_THRESHOLD, 255, cv2.THRESH_BINARY
        )
        if int(cv2.countNonZero(bright)) >= _START_GATE_MIN_BRIGHT_PX:
            lit += 1
            if lit >= _START_GATE_MIN_LIT_SLOTS:
                return True
    if checked == 0:
        return None
    return False


def _found_word_regions_from_frame(
    image: Any,
    area_doc: dict[str, Any],
    names: list[str],
) -> set[str]:
    if image is None or not hasattr(image, "shape") or len(image.shape) != 3:
        return set()
    frame_h, frame_w = int(image.shape[0]), int(image.shape[1])
    found: set[str] = set()
    for name in names:
        pair = screen_region_by_name(area_doc, name)
        region_def = pair[1] if pair else None
        if not isinstance(region_def, dict):
            continue
        px = _region_to_px(region_def, frame_w, frame_h)
        if px is None:
            continue
        crop = image[px.y : px.y + px.h, px.x : px.x + px.w]
        if _is_word_region_visually_found(crop):
            found.add(name)
    return found


async def _ocr_current_frame(
    image: Any,
    area_doc: dict[str, Any],
    names: list[str],
) -> dict[str, str]:
    """OCR named regions from the current frame, skipping low-confidence reads."""
    if image is None or not hasattr(image, "shape"):
        return {}
    frame_h, frame_w = int(image.shape[0]), int(image.shape[1])

    regions: list[Region] = []
    ids: list[str] = []
    preprocess: list[str | None] = []
    thresholds: dict[str, float] = {}
    for name in names:
        pair = screen_region_by_name(area_doc, name)
        region_def = pair[1] if pair else None
        if not isinstance(region_def, dict):
            logger.warning("dreamscape_memory_solve_loop: OCR region not found: %s", name)
            continue
        px = _region_to_px(region_def, frame_w, frame_h)
        if px is None:
            logger.warning("dreamscape_memory_solve_loop: OCR region malformed: %s", name)
            continue
        regions.append(px)
        ids.append(name)
        preprocess.append(
            resolve_preprocess(
                explicit=region_def.get("preprocess"),
                type_hint=region_def.get("type"),
            )
        )
        thresholds[name] = (
            _DEFAULT_WORD_OCR_THRESHOLD
            if preprocess[-1] == "word_line"
            else _threshold(region_def)
        )

    if not regions:
        return {}

    results = await dsl_runtime.ocr_client().ocr_regions(
        image,
        regions,
        region_ids=ids,
        region_preprocess=preprocess if any(preprocess) else None,
    )

    out: dict[str, str] = {}
    for result in results:
        rid = str(result.region_id or "").strip()
        text = str(result.text or "").strip()
        confidence = float(result.confidence or 0.0)
        if not rid or not text:
            continue
        if confidence < thresholds.get(rid, 0.0):
            logger.debug(
                "dreamscape_memory_solve_loop: low-confidence OCR region=%s "
                "text=%r confidence=%.3f threshold=%.3f",
                rid,
                text,
                confidence,
                thresholds.get(rid, 0.0),
            )
            continue
        out[rid] = text
    return out


# ── Redis IO ────────────────────────────────────────────────────────────────


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return str(raw).strip()


async def _resolve_player_id(ctx: DslExecContext) -> str:
    pid = str(getattr(ctx, "player_id", "") or "").strip()
    if pid or ctx.redis_client is None:
        return pid
    raw = await ctx.redis_client.hget(
        f"wos:instance:{ctx.instance_id}:state", "active_player"
    )
    return _decode(raw)


async def _read_word(ctx: DslExecContext, player_id: str, field: str) -> str:
    """Read an OCR'd region value, preferring player state, then instance."""
    if ctx.redis_client is None:
        return ""
    keys = []
    if player_id:
        keys.append(f"wos:player:{player_id}:state")
    keys.append(f"wos:instance:{ctx.instance_id}:state")
    for key in keys:
        text = _decode(await ctx.redis_client.hget(key, field))
        if text:
            return text
    return ""


# ── Handler ─────────────────────────────────────────────────────────────────


async def _exec_dreamscape_memory_solve(ctx: DslExecContext) -> None:
    args = ctx.args or {}
    regions = args.get("regions")
    if not isinstance(regions, list) or not regions:
        regions = list(_DEFAULT_REGIONS)
    try:
        tap_delay = float(args.get("tap_delay", _DEFAULT_TAP_DELAY_S))
    except (TypeError, ValueError):
        tap_delay = _DEFAULT_TAP_DELAY_S
    try:
        fuzz_threshold = float(args.get("fuzz_threshold", _DEFAULT_FUZZ_THRESHOLD))
    except (TypeError, ValueError):
        fuzz_threshold = _DEFAULT_FUZZ_THRESHOLD

    player_id = await _resolve_player_id(ctx)
    words = [await _read_word(ctx, player_id, str(r)) for r in regions]
    words = [w for w in words if w]
    if not words:
        logger.info(
            "dreamscape_memory_solve: no OCR words for regions %s (instance=%s)",
            regions,
            ctx.instance_id,
        )
        return

    # Title detector removed: identify the scene from the set of item words read
    # above (falls back to the active/override scene when none match).
    scene = await asyncio.to_thread(_select_scene, words, fuzz_threshold)
    targets = _targets_for_scene(scene)
    actions = dsl_runtime.bot_actions()
    dev_w, dev_h = await asyncio.to_thread(actions.screen_resolution, ctx.instance_id)
    hits, misses = _resolve_taps(
        words, targets, dev_w, dev_h, fuzz_threshold=fuzz_threshold
    )

    tapped: list[str] = []
    for word, point in hits:
        ok = await asyncio.to_thread(actions.tap, ctx.instance_id, point)
        logger.info(
            "dreamscape_memory_solve: %s %r -> (%d,%d) instance=%s",
            "tapped" if ok else "tap-rejected",
            word,
            point.x,
            point.y,
            ctx.instance_id,
        )
        if ok:
            tapped.append(word)
            if tap_delay > 0:
                await asyncio.sleep(tap_delay)

    if misses:
        logger.warning(
            "dreamscape_memory_solve: %d unmapped word(s) — add via onboarding: %s",
            len(misses),
            ", ".join(misses),
        )

    ctx.result.update(
        {
            "scene": scene.get("slug") if scene else "",
            "level_name": str(scene.get("title") or "") if scene else "",
            "words": words,
            "tapped": tapped,
            "unmapped": misses,
        }
    )


_MULTIPLAYER_MODES = frozenset({"multiplayer", "mp", "coop", "co-op"})


def _is_multiplayer_mode(args: dict[str, Any]) -> bool:
    return str(args.get("mode") or "").strip().lower() in _MULTIPLAYER_MODES


def _solver_regions_from_args(args: dict[str, Any]) -> list[str]:
    raw_regions = args.get("regions")
    if isinstance(raw_regions, list) and raw_regions:
        return [str(r).strip() for r in raw_regions if str(r or "").strip()]
    if _is_multiplayer_mode(args):
        return list(_DEFAULT_MULTIPLAYER_REGIONS)
    return list(_DEFAULT_REGIONS)


async def _capture_frame(actions: Any, instance_id: str) -> Any:
    cached = getattr(actions, "capture_screen_bgr_cached", None)
    if cached is not None:
        return await asyncio.to_thread(cached, instance_id, max_age_ms=150.0)
    return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)


async def _capture_fresh_frame(actions: Any, instance_id: str) -> Any:
    cached = getattr(actions, "capture_screen_bgr_cached", None)
    if cached is not None:
        return await asyncio.to_thread(cached, instance_id, max_age_ms=0.0)
    return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)


def _detect_help_highlight_motion_candidate(
    before: Any,
    after: Any,
) -> HelpMotionCandidate | None:
    """Find Dreamscape's pulsing hint target from two otherwise-static frames.

    The Help hint scales the target item up and down; the changed pixels cluster
    on the item's silhouette, so the strongest motion blob's centroid marks it.
    No shape assumption is made (the old animated-ring detector is gone).
    """
    if (
        before is None
        or after is None
        or not hasattr(before, "shape")
        or not hasattr(after, "shape")
        or before.shape != after.shape
        or len(before.shape) != 3
    ):
        return None

    try:
        import cv2
        import numpy as np
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: cv2/numpy unavailable", exc_info=True)
        return None

    frame_h, frame_w = int(before.shape[0]), int(before.shape[1])
    if frame_w <= 0 or frame_h <= 0:
        return None

    # The word bar/help counter animate in the lower UI and the title/timer can
    # tick near the top. The pulsing item lives in the scene art between them.
    roi_top = int(round(frame_h * 0.06))
    roi_bottom = int(round(frame_h * 0.85))
    if roi_bottom <= roi_top:
        return None

    before_roi = before[roi_top:roi_bottom, :]
    after_roi = after[roi_top:roi_bottom, :]
    gray_diff = cv2.absdiff(
        cv2.cvtColor(before_roi, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(after_roi, cv2.COLOR_BGR2GRAY),
    )
    before_v = cv2.cvtColor(before_roi, cv2.COLOR_BGR2HSV)[..., 2]
    after_v = cv2.cvtColor(after_roi, cv2.COLOR_BGR2HSV)[..., 2]
    diff = cv2.max(gray_diff, cv2.absdiff(before_v, after_v))
    diff = cv2.GaussianBlur(diff, (5, 5), 0)

    mean, stddev = cv2.meanStdDev(diff)
    threshold = max(10.0, float(mean[0][0] + stddev[0][0] * 3.0))
    _, mask = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    if int(mask.sum()) == 0:
        return None

    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    kernel_merge = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open)
    mask = cv2.dilate(mask, kernel_merge, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_merge)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # No shape assumption: the item just scales, so take the strongest motion
    # blob and tap its centroid. A size band drops UI ticks (too small) and
    # whole-screen flashes like scene transitions (too large); the score weights
    # blob size by how hard it moved so a faint background shimmer can't win.
    roi_area = float((roi_bottom - roi_top) * frame_w)
    min_area = max(120.0, roi_area * 0.0008)
    max_area = roi_area * 0.45
    best: tuple[float, float] | None = None
    best_score = 0.0
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area or area > max_area:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] <= 0.0:
            continue
        cx = moments["m10"] / moments["m00"]
        cy = moments["m01"] / moments["m00"]
        blob_mask = np.zeros(diff.shape, dtype=np.uint8)
        cv2.drawContours(blob_mask, [contour], -1, 255, thickness=cv2.FILLED)
        mean_motion = float(cv2.mean(diff, mask=blob_mask)[0])
        score = area * mean_motion
        if score > best_score:
            best_score = score
            best = (cx, cy + roi_top)

    if best is None:
        return None
    return HelpMotionCandidate(
        Point(int(round(best[0])), int(round(best[1]))),
        best_score,
    )


def _detect_help_highlight_motion(before: Any, after: Any) -> Point | None:
    candidate = _detect_help_highlight_motion_candidate(before, after)
    return candidate.point if candidate is not None else None


def _detect_help_highlight_motion_multi(frames: list[Any]) -> Point | None:
    """Find a help-hint pulse that repeats across several fresh frame diffs.

    ``frames[0]`` should be a baseline captured immediately before tapping help;
    the remaining frames are fresh post-help captures. A candidate must appear
    in at least two pairwise diffs so one-off changes (word strike-throughs,
    progress ticks, fire sparks) do not get learned as item positions.
    """
    if len(frames) < 2:
        return None
    first = frames[0]
    if first is None or not hasattr(first, "shape") or len(first.shape) != 3:
        return None
    frame_h, frame_w = int(first.shape[0]), int(first.shape[1])
    if frame_w <= 0 or frame_h <= 0:
        return None

    candidates: list[HelpMotionCandidate] = []
    for frame in frames[1:]:
        candidate = _detect_help_highlight_motion_candidate(first, frame)
        if candidate is not None:
            candidates.append(candidate)
    for before, after in pairwise(frames[1:]):
        candidate = _detect_help_highlight_motion_candidate(before, after)
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        return None

    cluster_radius = max(36.0, min(frame_w, frame_h) * 0.055)
    clusters: list[list[HelpMotionCandidate]] = []
    for candidate in sorted(candidates, key=lambda c: c.score, reverse=True):
        placed = False
        for cluster in clusters:
            total_score = sum(max(c.score, 1.0) for c in cluster)
            cx = sum(c.point.x * max(c.score, 1.0) for c in cluster) / total_score
            cy = sum(c.point.y * max(c.score, 1.0) for c in cluster) / total_score
            dx = candidate.point.x - cx
            dy = candidate.point.y - cy
            if (dx * dx + dy * dy) ** 0.5 <= cluster_radius:
                cluster.append(candidate)
                placed = True
                break
        if not placed:
            clusters.append([candidate])

    min_votes = 2 if len(frames) >= 4 else 1
    clusters = [cluster for cluster in clusters if len(cluster) >= min_votes]
    if not clusters:
        return None

    best_cluster = max(
        clusters,
        key=lambda cluster: (len(cluster), sum(c.score for c in cluster)),
    )
    total_score = sum(max(c.score, 1.0) for c in best_cluster)
    x = sum(c.point.x * max(c.score, 1.0) for c in best_cluster) / total_score
    y = sum(c.point.y * max(c.score, 1.0) for c in best_cluster) / total_score
    return Point(int(round(x)), int(round(y)))


def _point_to_scene_percent(
    point: Point,
    frame_w: int,
    frame_h: int,
    scene_rect: dict[str, Any] | None,
) -> tuple[float, float] | None:
    if frame_w <= 0 or frame_h <= 0:
        return None
    x_pct = point.x / float(frame_w) * 100.0
    y_pct = point.y / float(frame_h) * 100.0
    rect = _scene_rect(scene_rect)
    if rect is None:
        return (round(x_pct, 2), round(y_pct, 2))
    left, top, width, height = rect
    if width <= 0 or height <= 0:
        return None
    return (
        round((x_pct - left) / width * 100.0, 2),
        round((y_pct - top) / height * 100.0, 2),
    )


async def _tap_help_highlight_target(
    actions: Any,
    instance_id: str,
    *,
    capture_delay_s: float,
    diff_gap_s: float,
    before_frame: Any | None = None,
    word: str = "",
) -> Point | None:
    try:
        frames: list[Any] = []
        if before_frame is not None:
            frames.append(before_frame)
        for idx in range(_HELP_CAPTURE_FRAMES):
            if idx == 0:
                if capture_delay_s > 0:
                    await asyncio.sleep(capture_delay_s)
            elif diff_gap_s > 0:
                await asyncio.sleep(diff_gap_s)
            frames.append(await _capture_fresh_frame(actions, instance_id))
        point = await asyncio.to_thread(_detect_help_highlight_motion_multi, frames)
    except Exception:
        logger.exception(
            "dreamscape_memory_solve_loop: help highlight capture failed instance=%s",
            instance_id,
        )
        return None

    if point is None:
        logger.info(
            "dreamscape_memory_solve_loop: help highlight motion not detected "
            "for word=%r across %d frame(s) instance=%s",
            word,
            _HELP_CAPTURE_FRAMES,
            instance_id,
        )
        return None
    ok = await asyncio.to_thread(
        actions.tap,
        instance_id,
        point,
        require_approval=False,
    )
    logger.info(
        "dreamscape_memory_solve_loop: %s help-highlight target -> (%d,%d) "
        "reason=help-detected-motion word=%r instance=%s",
        "tapped" if ok else "help-highlight-tap-rejected",
        point.x,
        point.y,
        word,
        instance_id,
    )
    return point if ok else None


async def _detect_terminal_screen(image: Any, hint: str | None = None) -> str:
    """Return a Dreamscape terminal screen id for ``image``, or empty string."""
    if image is None or not hasattr(image, "shape"):
        return ""
    try:
        from navigation.detector import suggest_node_for_image_sync
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: screen detector unavailable", exc_info=True)
        return ""

    try:
        detected = await asyncio.to_thread(suggest_node_for_image_sync, image, hint=hint)
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: terminal screen detect failed", exc_info=True)
        return ""
    screen = str(detected or "").strip()
    return screen if screen in _TERMINAL_SCREENS else ""


async def _write_current_screen(ctx: DslExecContext, screen: str) -> None:
    if not screen or ctx.redis_client is None:
        return
    try:
        await ctx.redis_client.hset(
            f"wos:instance:{ctx.instance_id}:state",
            "current_screen",
            screen,
        )
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: failed to write current_screen", exc_info=True)


async def _check_terminal_screen(
    ctx: DslExecContext,
    image: Any,
    *,
    hint: str | None,
    taps_total: int,
) -> tuple[str, bool]:
    terminal_screen = await _detect_terminal_screen(image, hint=hint)
    if not terminal_screen:
        return "", False
    if _terminal_screen_is_valid(terminal_screen, taps_total=taps_total):
        await _write_current_screen(ctx, terminal_screen)
        logger.info(
            "dreamscape_memory_solve_loop: terminal screen detected %s; stopping instance=%s",
            terminal_screen,
            ctx.instance_id,
        )
        return terminal_screen, True
    logger.info(
        "dreamscape_memory_solve_loop: ignoring pre-game terminal screen %s "
        "(taps_total=%d)",
        terminal_screen,
        taps_total,
    )
    return "", False


def _serialize_slot_states(
    slot_states: dict[str, SlotFsmState],
    regions: list[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for region in regions:
        state = slot_states.get(region)
        row: dict[str, Any] = {
            "status": state.status if state is not None else _SLOT_UNKNOWN,
            "fsm_status": _public_slot_fsm_status(state),
        }
        if state is not None and state.raw_word:
            row["word"] = state.raw_word
        if state is not None and state.raw_key:
            row["raw_key"] = state.raw_key
        if state is not None and state.key:
            row["key"] = state.key
        if state is not None and state.point is not None:
            row["point"] = {"x": state.point.x, "y": state.point.y}
        out[region] = row
    return out


async def _dispatch_mapped_taps(
    ctx: DslExecContext,
    actions: Any,
    *,
    regions: list[str],
    slot_states: dict[str, SlotFsmState],
    pending_clicks: dict[str, PendingClick],
    click_retry_counts: dict[str, int],
    tap_attempt_iter: dict[str, int],
    exhausted_tap_signatures: set[tuple[str, str, int, int]],
    tap_delay_s: float,
    events: list[dict[str, Any]],
    iteration: int,
) -> int:
    """Dispatch a tap for every ``determined`` slot whose tap is not in flight.

    The tap is only SENT here — the slot stays ``determined``. It becomes
    ``clicked`` only when the background-colour detector later confirms the pill
    greyed out (see the confirmation block in the loop). This keeps ``clicked``
    honest: a tap that never landed (or hit the wrong place) never shows as
    clicked, and the slot is re-tapped from the pending-click block instead.
    """
    taps = 0
    for region in regions:
        state = slot_states.get(region)
        if state is None or state.status != _SLOT_MAPPED or state.point is None:
            continue
        if region in pending_clicks:
            # Tap already in flight, still awaiting colour confirmation; the
            # pending-click block owns the re-tap/timeout for it.
            continue
        ok = await asyncio.to_thread(
            actions.tap,
            ctx.instance_id,
            state.point,
            require_approval=False,
        )
        logger.info(
            "dreamscape_memory_solve_loop: %s mapped word=%r key=%r region=%s -> "
            "(%d,%d) reason=ocr-word-mapped-to-scene-point instance=%s "
            "(awaiting colour confirm)",
            "tapped" if ok else "tap-rejected",
            state.raw_word,
            state.key,
            region,
            state.point.x,
            state.point.y,
            ctx.instance_id,
        )
        _append_event(
            events,
            "click",
            f"{'Tapped' if ok else 'Rejected'} {state.raw_word} (awaiting colour confirm)",
            iteration=iteration,
            region=region,
            word=state.raw_word,
            key=state.key,
            x=state.point.x,
            y=state.point.y,
            ok=ok,
        )
        if not ok:
            exhausted_tap_signatures.add(_tap_signature(region, state.key, state.point))
            _set_slot(
                slot_states,
                region,
                state._replace(status=_SLOT_TAP_REJECTED),
                events=events,
                iteration=iteration,
                instance_id=ctx.instance_id,
            )
            continue
        # Keep the slot ``determined``; only record the in-flight tap so the
        # confirmation/re-tap block can track it. clicked_* is populated solely
        # on colour confirmation.
        pending_clicks[region] = PendingClick(
            key=state.key,
            raw_key=state.raw_key,
            raw_word=state.raw_word,
            point=state.point,
        )
        click_retry_counts[region] = 1
        tap_attempt_iter[region] = iteration
        taps += 1
        if tap_delay_s > 0:
            await asyncio.sleep(tap_delay_s)
    return taps


async def _write_live_solve_state(
    ctx: DslExecContext,
    *,
    regions: list[str],
    scene: str,
    level_name: str,
    iterations: int,
    seen: list[str],
    clicked: list[str],
    clicked_keys: set[str],
    clicked_regions: set[str],
    settled_regions: set[str],
    pending_clicks: dict[str, PendingClick],
    region_words: dict[str, str],
    slot_states: dict[str, SlotFsmState],
    events: list[dict[str, Any]],
    status: str = "running",
) -> None:
    if ctx.redis_client is None:
        return
    payload = {
        "status": status,
        "scene": scene,
        "level_name": level_name,
        "regions": regions,
        "iterations": iterations,
        "seen": seen,
        "clicked": clicked,
        "clicked_keys": sorted(clicked_keys),
        "clicked_regions": sorted(clicked_regions),
        "settled_regions": sorted(settled_regions),
        "pending_click_regions": sorted(pending_clicks),
        "region_words": {
            region: word for region, word in region_words.items() if region in regions
        },
        "slot_states": _serialize_slot_states(slot_states, regions),
        "events": events[-_MAX_LIVE_EVENTS:],
        "updated_at": time.time(),
    }
    try:
        await ctx.redis_client.hset(
            f"wos:instance:{ctx.instance_id}:state",
            _LIVE_STATE_FIELD,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception:
        logger.debug("dreamscape_memory_solve_loop: failed to write live state", exc_info=True)


def _running_in_supervisor_process() -> bool:
    argv = [str(arg) for arg in sys.argv]
    return any(arg == "worker.supervisor" for arg in argv)


def _request_local_bot_stop(reason: str) -> dict[str, Any]:
    """Ask the local worker stack to stop without blocking this worker task."""
    try:
        from worker import local_bot

        status = local_bot.bot_status()
    except Exception as exc:
        logger.warning(
            "dreamscape_memory_solve_loop: failed to inspect bot status before stop: %s",
            exc,
        )
        status = {"running": False, "mode": None}

    mode = str(status.get("mode") or "")
    if mode == "embedded":
        try:
            from dashboard.bot_services import request_embedded_bot_stop

            request_embedded_bot_stop()
            logger.warning(
                "dreamscape_memory_solve_loop: requested embedded bot stop (%s)",
                reason,
            )
            return {"requested": True, "mode": mode, "reason": reason}
        except Exception as exc:
            logger.warning(
                "dreamscape_memory_solve_loop: embedded bot stop request failed: %s",
                exc,
            )
            return {"requested": False, "mode": mode, "reason": reason, "error": str(exc)}

    if bool(status.get("running")):
        try:
            from worker import local_bot

            threading.Thread(
                target=local_bot.stop_local_bot,
                kwargs={"join_timeout_s": 0.5},
                daemon=True,
                name="dreamscape-terminal-stop-bot",
            ).start()
            logger.warning(
                "dreamscape_memory_solve_loop: requested local bot stop (%s)",
                reason,
            )
            return {"requested": True, "mode": mode, "reason": reason}
        except Exception as exc:
            logger.warning(
                "dreamscape_memory_solve_loop: local bot stop request failed: %s",
                exc,
            )
            return {"requested": False, "mode": mode, "reason": reason, "error": str(exc)}

    if _running_in_supervisor_process():
        def _terminate_self() -> None:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                logger.exception("dreamscape_memory_solve_loop: self-stop failed")

        threading.Timer(0.25, _terminate_self).start()
        logger.warning(
            "dreamscape_memory_solve_loop: scheduled supervisor self-stop (%s)",
            reason,
        )
        return {"requested": True, "mode": "supervisor-self", "reason": reason}

    logger.info(
        "dreamscape_memory_solve_loop: bot already stopped; no stop request sent (%s)",
        reason,
    )
    return {"requested": False, "mode": mode, "reason": reason}


async def _exec_dreamscape_memory_solve_loop(ctx: DslExecContext) -> None:
    """Stateful realtime Dreamscape solver.

    This replaces the YAML ``ocr`` loop for Dreamscape: each iteration captures a
    fresh frame, OCRs the title + word slots, resolves words against the current
    scene, and taps only target keys that have not already been clicked in this
    scene.
    """
    args = ctx.args or {}
    regions = _solver_regions_from_args(args)
    help_region = str(args.get("help_region", _DEFAULT_HELP_REGION) or "").strip()
    help_counter_region = str(
        args.get("help_counter_region", _DEFAULT_HELP_COUNTER_REGION) or ""
    ).strip()

    ttl_s = _parse_duration_s(args.get("ttl"), _DEFAULT_LOOP_TTL_S)
    wait_s = _parse_duration_s(args.get("wait"), _DEFAULT_LOOP_WAIT_S)
    tap_delay_s = _parse_duration_s(args.get("tap_delay"), _DEFAULT_TAP_DELAY_S)
    # Pixel-based round-start gate: multiplayer rounds open behind a dark
    # shade, so until the word pills light up the loop only runs the cheap
    # bright-pixel check (fast ticks, no OCR). Defaults on for multiplayer;
    # override per-step with ``pixel_start_gate: true|false``.
    start_gate_active = bool(
        args.get("pixel_start_gate", _is_multiplayer_mode(args))
    )
    start_gate_wait_s = _parse_duration_s(
        args.get("start_gate_wait"), _DEFAULT_START_GATE_WAIT_S
    )
    help_capture_delay_s = _parse_duration_s(
        args.get("help_capture_delay"), _DEFAULT_HELP_CAPTURE_DELAY_S
    )
    help_diff_gap_s = _parse_duration_s(
        args.get("help_diff_gap"), _DEFAULT_HELP_DIFF_GAP_S
    )
    try:
        max_iterations = int(args.get("max_iterations", args.get("max", _DEFAULT_LOOP_MAX_ITERATIONS)))
    except (TypeError, ValueError):
        max_iterations = _DEFAULT_LOOP_MAX_ITERATIONS
    max_iterations = max(1, max_iterations)
    try:
        fuzz_threshold = float(args.get("fuzz_threshold", _DEFAULT_FUZZ_THRESHOLD))
    except (TypeError, ValueError):
        fuzz_threshold = _DEFAULT_FUZZ_THRESHOLD
    try:
        tap_confirm_wait = int(
            args.get("tap_confirm_wait", _DEFAULT_TAP_CONFIRM_WAIT_ITERS)
        )
    except (TypeError, ValueError):
        tap_confirm_wait = _DEFAULT_TAP_CONFIRM_WAIT_ITERS
    tap_confirm_wait = max(1, tap_confirm_wait)
    try:
        max_tap_attempts = int(args.get("max_tap_attempts", _DEFAULT_MAX_TAP_ATTEMPTS))
    except (TypeError, ValueError):
        max_tap_attempts = _DEFAULT_MAX_TAP_ATTEMPTS
    max_tap_attempts = max(1, max_tap_attempts)

    area_doc = _load_area()
    actions = dsl_runtime.bot_actions()
    dev_w, dev_h = await asyncio.to_thread(actions.screen_resolution, ctx.instance_id)
    deadline = time.monotonic() + ttl_s if ttl_s > 0 else None

    last_scene_slug = ""
    # The scene is the operator's active scene (selected on the game page). It is
    # resolved once on the first tick and cached for the rest of the run; the
    # title detector is removed, so there is nothing to re-read (see the
    # scene-resolution branch in the loop below).
    title_locked = False
    cached_scene: dict[str, Any] | None = None
    seen_keys: set[str] = set()
    seen_words: list[str] = []
    clicked_keys: set[str] = set()
    clicked_words: list[str] = []
    clicked_regions: set[str] = set()
    settled_regions: set[str] = set()
    region_words: dict[str, str] = {}
    slot_states: dict[str, SlotFsmState] = {}
    pending_clicks: dict[str, PendingClick] = {}
    click_retry_counts: dict[str, int] = {}
    # Iteration at which each in-flight tap was last sent, so the confirmation
    # block can wait a few frames for the colour detector before re-tapping.
    tap_attempt_iter: dict[str, int] = {}
    exhausted_tap_signatures: set[tuple[str, str, int, int]] = set()
    click_retries: list[dict[str, Any]] = []
    click_retry_exhausted: list[dict[str, Any]] = []
    helped_keys: set[str] = set()
    helped_words: list[str] = []
    help_target_taps: list[HelpTargetTap] = []
    learned_help_points: list[dict[str, Any]] = []
    help_learn_errors: list[dict[str, Any]] = []
    help_counter_reads: list[int] = []
    events: list[dict[str, Any]] = []
    help_remaining = _DEFAULT_HELP_COUNT
    unmapped: list[str] = []
    # Count of iterations each (region, normalized-key) miss has been observed,
    # gating the helper on a confirmed read (see _MIN_UNMAPPED_CONFIRM_READS).
    unmapped_seen_counts: dict[tuple[str, str], int] = {}
    unconfirmed_logged: set[tuple[str, str]] = set()
    skipped_clicked: list[str] = []
    iterations = 0
    taps_total = 0
    settled_batch_idle_iterations = 0
    start_gate_waiting = False
    last_level_name = ""
    terminal_screen = ""
    stop_bot_after_result = False
    bot_stop: dict[str, Any] = {}

    _append_event(
        events,
        "start",
        "Dreamscape solver loop started",
        regions=regions,
        help_region=help_region,
        help_counter_region=help_counter_region,
    )
    await _write_live_solve_state(
        ctx,
        regions=regions,
        scene=last_scene_slug,
        level_name=last_level_name,
        iterations=iterations,
        seen=seen_words,
        clicked=clicked_words,
        clicked_keys=clicked_keys,
        clicked_regions=clicked_regions,
        settled_regions=settled_regions,
        pending_clicks=pending_clicks,
        region_words=region_words,
        slot_states=slot_states,
        events=events,
    )

    for iteration in range(max_iterations):
        if deadline is not None and time.monotonic() >= deadline:
            break
        iterations = iteration + 1

        pre_ocr_taps = await _dispatch_mapped_taps(
            ctx,
            actions,
            regions=regions,
            slot_states=slot_states,
            pending_clicks=pending_clicks,
            click_retry_counts=click_retry_counts,
            tap_attempt_iter=tap_attempt_iter,
            exhausted_tap_signatures=exhausted_tap_signatures,
            tap_delay_s=tap_delay_s,
            events=events,
            iteration=iterations,
        )
        if pre_ocr_taps:
            taps_total += pre_ocr_taps
            await _write_live_solve_state(
                ctx,
                regions=regions,
                scene=last_scene_slug,
                level_name=last_level_name,
                iterations=iterations,
                seen=seen_words,
                clicked=clicked_words,
                clicked_keys=clicked_keys,
                clicked_regions=clicked_regions,
                settled_regions=settled_regions,
                pending_clicks=pending_clicks,
                region_words=region_words,
                slot_states=slot_states,
                events=events,
            )
            continue

        try:
            image = await _capture_frame(actions, ctx.instance_id)
        except Exception:
            logger.exception(
                "dreamscape_memory_solve_loop: capture failed instance=%s",
                ctx.instance_id,
            )
            break

        if start_gate_active:
            started = _round_started_pixels(image, area_doc, regions)
            if started is False:
                if not start_gate_waiting:
                    start_gate_waiting = True
                    logger.info(
                        "dreamscape_memory_solve_loop: waiting for shade to lift "
                        "(pixel start gate) instance=%s",
                        ctx.instance_id,
                    )
                    _append_event(
                        events,
                        "start_wait",
                        "Waiting for the shade to lift (pixel start gate)",
                        iteration=iterations,
                    )
                # A stale terminal screen (e.g. time_up from a previous round)
                # also reads dark — keep the terminal detector running so the
                # gate cannot idle on it until the ttl.
                terminal_screen, terminal_stop = await _check_terminal_screen(
                    ctx,
                    image,
                    hint=terminal_screen or last_scene_slug or None,
                    taps_total=taps_total,
                )
                if terminal_stop:
                    stop_bot_after_result = True
                    break
                if start_gate_wait_s > 0:
                    await asyncio.sleep(start_gate_wait_s)
                continue
            start_gate_active = False
            if started:
                # The shade just lifted — the round starts now, so the solve
                # ttl restarts: lobby wait time must not eat into solve time.
                if deadline is not None:
                    deadline = time.monotonic() + ttl_s
                logger.info(
                    "dreamscape_memory_solve_loop: shade lifted; round started "
                    "(pixel start gate) instance=%s",
                    ctx.instance_id,
                )
                _append_event(
                    events,
                    "round_start",
                    "Shade lifted — round started",
                    iteration=iterations,
                )
            # ``started is None`` → the pixel check cannot run (cv2/regions
            # unavailable); fail open to the normal OCR loop.

        greyed_regions = _found_word_regions_from_frame(image, area_doc, regions)
        visually_active = set(regions) - greyed_regions
        # A found word never flips back to active on its own inside a batch — the
        # whole struck set only clears together when the next word set loads. So
        # only reopen once EVERY engaged slot has been solved (all 3/6 found) AND
        # the entire struck set reads active again in the same frame. This stops a
        # single found pill that momentarily mis-reads as active (OCR / strike-in
        # flicker) from being reopened and re-clicked mid-batch.
        engaged_regions = set(region_words) | clicked_regions | settled_regions
        batch_solved = bool(engaged_regions) and engaged_regions <= settled_regions
        new_set_loading = batch_solved and settled_regions <= visually_active
        reopened_regions = set(settled_regions) if new_set_loading else set()
        if reopened_regions:
            # The full word set was solved and a fresh set is loading: drop the
            # per-batch click memory so repeated words in the next set are clicked
            # again, keeping only the append-only clicked/seen logs.
            settled_batch_idle_iterations = 0
            settled_regions.difference_update(reopened_regions)
            for region in reopened_regions:
                pending_clicks.pop(region, None)
                click_retry_counts.pop(region, None)
                tap_attempt_iter.pop(region, None)
                _discard_tap_signatures_for_region(exhausted_tap_signatures, region)
                _set_slot(
                    slot_states,
                    region,
                    None,
                    events=events,
                    iteration=iterations,
                    instance_id=ctx.instance_id,
                )
                region_words.pop(region, None)
                clicked_regions.discard(region)
            clicked_keys.clear()
            seen_keys.clear()
            helped_keys.clear()
            unmapped_seen_counts.clear()
            unconfirmed_logged.clear()
            skipped_clicked.clear()
            logger.info(
                "dreamscape_memory_solve_loop: word set solved; reopened slot(s) "
                "for next batch: %s",
                sorted(reopened_regions),
            )
            _append_event(
                events,
                "batch_reset",
                "Word set solved; reopened slots for the next batch",
                iteration=iterations,
                regions=sorted(reopened_regions),
            )
        # Background colour is the SOLE authority for confirming a tap. An
        # in-flight tap whose pill has now greyed is a CONFIRMED click → promote
        # it to ``clicked`` (and only now record it in clicked_*). A pill that
        # greyed without any tap of ours is ``found`` (already solved / solved by
        # a teammate). Either way the slot is locked (settled_regions).
        newly_clicked = greyed_regions & set(pending_clicks)
        for region in sorted(newly_clicked):
            pending = pending_clicks.pop(region)
            click_retry_counts.pop(region, None)
            tap_attempt_iter.pop(region, None)
            if pending.key not in clicked_keys:
                clicked_keys.add(pending.key)
                clicked_words.append(pending.raw_word)
            clicked_regions.add(region)
            settled_regions.add(region)
            _set_slot(
                slot_states,
                region,
                SlotFsmState(
                    status=_SLOT_CLICKED,
                    raw_word=pending.raw_word,
                    raw_key=pending.raw_key,
                    key=pending.key,
                    point=pending.point,
                ),
                events=events,
                iteration=iterations,
                instance_id=ctx.instance_id,
            )
        external_found = greyed_regions - clicked_regions - set(pending_clicks)
        for region in sorted(external_found):
            prev = slot_states.get(region)
            if region in settled_regions and prev is not None and prev.status in {
                _SLOT_CLICKED,
                _SLOT_SETTLED,
            }:
                continue
            settled_regions.add(region)
            _set_slot(
                slot_states,
                region,
                SlotFsmState(
                    status=_SLOT_SETTLED,
                    raw_word=(prev.raw_word if prev else region_words.get(region, "")),
                    raw_key=prev.raw_key if prev else "",
                    key=prev.key if prev else "",
                    point=prev.point if prev else None,
                ),
                events=events,
                iteration=iterations,
                instance_id=ctx.instance_id,
            )

        pending_regions = [region for region in regions if region not in settled_regions]
        batch_reopen_probe_regions: list[str] = []
        scene_known_before_ocr = bool(last_scene_slug)
        if scene_known_before_ocr and not pending_regions and not pending_clicks and settled_regions:
            settled_batch_idle_iterations += 1
            if settled_batch_idle_iterations >= _DEFAULT_BATCH_REOPEN_OCR_PROBE_ITERS:
                batch_reopen_probe_regions = list(regions)
        else:
            settled_batch_idle_iterations = 0
        # Title detector removed: the scene is detected from the *set of item
        # words on screen*, so the word slots are OCR'd every tick (they are also
        # what the solver taps). The help counter only matters once a scene is
        # locked. ``level_region`` is no longer read.
        ocr_region_names = list(pending_regions)
        for region in batch_reopen_probe_regions:
            if region not in ocr_region_names:
                ocr_region_names.append(region)
        if scene_known_before_ocr and help_region and help_counter_region:
            ocr_region_names.append(help_counter_region)

        ocr_values = await _ocr_current_frame(image, area_doc, ocr_region_names)

        if title_locked:
            scene = cached_scene
            scene_slug = last_scene_slug
        else:
            detected_words = [ocr_values.get(region, "") for region in pending_regions]
            scene, scene_locked = await asyncio.to_thread(
                _select_scene_ex, detected_words, fuzz_threshold
            )
            scene_slug = str(scene.get("slug") or "") if scene else ""
            if scene_locked and scene_slug:
                title_locked = True
                cached_scene = scene
        level_name = last_level_name
        if scene_slug and scene_slug != last_scene_slug:
            if last_scene_slug:
                logger.info(
                    "dreamscape_memory_solve_loop: scene changed %s -> %s; reset clicked memory",
                    last_scene_slug,
                    scene_slug,
                )
            last_scene_slug = scene_slug
            _append_event(
                events,
                "scene",
                f"Matched scene {scene_slug}",
                iteration=iterations,
                scene=scene_slug,
                level_name=level_name,
            )
            seen_keys.clear()
            seen_words.clear()
            clicked_keys.clear()
            clicked_words.clear()
            clicked_regions.clear()
            settled_regions.clear()
            region_words.clear()
            slot_states.clear()
            pending_clicks.clear()
            click_retry_counts.clear()
            tap_attempt_iter.clear()
            exhausted_tap_signatures.clear()
            click_retries.clear()
            click_retry_exhausted.clear()
            helped_keys.clear()
            helped_words.clear()
            help_target_taps.clear()
            learned_help_points.clear()
            help_learn_errors.clear()
            help_counter_reads.clear()
            help_remaining = _DEFAULT_HELP_COUNT
            skipped_clicked.clear()
            unmapped.clear()
            unmapped_seen_counts.clear()
            unconfirmed_logged.clear()
            settled_batch_idle_iterations = 0
            # Per-batch memory was just cleared, so any already-greyed pill is a
            # pre-solved word, not one of ours → mark it ``found``.
            if greyed_regions:
                settled_regions.update(greyed_regions)
                for region in greyed_regions:
                    pending_clicks.pop(region, None)
                    click_retry_counts.pop(region, None)
                    tap_attempt_iter.pop(region, None)
                    _discard_tap_signatures_for_region(exhausted_tap_signatures, region)
                    _set_slot(
                        slot_states,
                        region,
                        SlotFsmState(status=_SLOT_SETTLED),
                        events=events,
                        iteration=iterations,
                        instance_id=ctx.instance_id,
                    )
            if reopened_regions:
                settled_regions.difference_update(reopened_regions)
                for region in reopened_regions:
                    _set_slot(
                        slot_states,
                        region,
                        None,
                        events=events,
                        iteration=iterations,
                        instance_id=ctx.instance_id,
                    )
            pending_regions = [region for region in regions if region not in settled_regions]

        if scene_slug and not scene_known_before_ocr:
            # The scene was just identified from the word slots, which the main
            # OCR above already read this tick. Only the help counter still needs
            # reading (it's skipped until a scene is known), so fetch just that —
            # re-reading the words here would burn an extra OCR pass on the same
            # frame and double-advance staged test word values.
            if help_region and help_counter_region:
                ocr_values.update(
                    await _ocr_current_frame(image, area_doc, [help_counter_region])
                )
            _append_event(
                events,
                "scene_ready",
                "Scene matched from word slots",
                iteration=iterations,
                scene=scene_slug,
            )

        if not scene_slug:
            _append_event(
                events,
                "scene_wait",
                "Waiting for scene title before reading word slots",
                iteration=iterations,
                level_name=level_name,
            )
            terminal_screen, terminal_stop = await _check_terminal_screen(
                ctx,
                image,
                hint=terminal_screen or last_scene_slug or None,
                taps_total=taps_total,
            )
            if terminal_stop:
                stop_bot_after_result = True
                break
            await _write_live_solve_state(
                ctx,
                regions=regions,
                scene=last_scene_slug,
                level_name=last_level_name,
                iterations=iterations,
                seen=seen_words,
                clicked=clicked_words,
                clicked_keys=clicked_keys,
                clicked_regions=clicked_regions,
                settled_regions=settled_regions,
                pending_clicks=pending_clicks,
                region_words=region_words,
                slot_states=slot_states,
                events=events,
            )
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            continue

        if batch_reopen_probe_regions:
            changed_probe_regions: set[str] = set()
            for region in batch_reopen_probe_regions:
                current_word = ocr_values.get(region, "")
                current_key = _normalize_word(current_word)
                if not current_key:
                    continue
                previous = slot_states.get(region)
                previous_keys = [
                    previous.raw_key if previous else "",
                    previous.key if previous else "",
                    _normalize_word(region_words.get(region, "")),
                ]
                if _word_keys_overlap(current_key, *previous_keys):
                    continue
                changed_probe_regions.add(region)
            if changed_probe_regions:
                reopened_regions = set(regions) & settled_regions
                settled_batch_idle_iterations = 0
                settled_regions.difference_update(reopened_regions)
                for region in reopened_regions:
                    pending_clicks.pop(region, None)
                    click_retry_counts.pop(region, None)
                    tap_attempt_iter.pop(region, None)
                    _discard_tap_signatures_for_region(exhausted_tap_signatures, region)
                    _set_slot(
                        slot_states,
                        region,
                        None,
                        events=events,
                        iteration=iterations,
                        instance_id=ctx.instance_id,
                    )
                    region_words.pop(region, None)
                    clicked_regions.discard(region)
                clicked_keys.clear()
                seen_keys.clear()
                helped_keys.clear()
                unmapped_seen_counts.clear()
                unconfirmed_logged.clear()
                skipped_clicked.clear()
                pending_regions = [
                    region for region in regions if region not in settled_regions
                ]
                logger.info(
                    "dreamscape_memory_solve_loop: OCR probe reopened solved word "
                    "set; changed slot(s): %s",
                    sorted(changed_probe_regions),
                )
                _append_event(
                    events,
                    "batch_reset",
                    "Word set solved; OCR probe reopened slots for the next batch",
                    iteration=iterations,
                    regions=sorted(reopened_regions),
                    changed_regions=sorted(changed_probe_regions),
                )

        word_items = [
            (region, word)
            for region in pending_regions
            if (word := ocr_values.get(region, ""))
        ]
        word_by_region = dict(word_items)
        if word_items:
            _append_event(
                events,
                "ocr",
                "Read word slots",
                iteration=iterations,
                words=[
                    {"region": region, "word": word}
                    for region, word in word_items
                ],
            )
        region_words.update(word_by_region)
        if help_region and help_counter_region:
            counter = _parse_help_counter(ocr_values.get(help_counter_region, ""))
            if counter is not None:
                help_remaining = min(help_remaining, counter)
                help_counter_reads.append(counter)
                _append_event(
                    events,
                    "helper",
                    f"Helper counter read: {counter}",
                    iteration=iterations,
                    remaining=help_remaining,
                )

        known_target_clicked = False
        blocked_new_word_regions: set[str] = set()
        changed_pending_regions: set[str] = set()
        # In-flight taps that the colour detector did NOT confirm this frame (the
        # confirmation block above already popped the confirmed ones). For each,
        # either the slot's word genuinely changed (reopen) or the pill is still
        # active — give the colour a few frames, then re-tap to recover from a
        # tap that missed, bounded so a bad map coordinate cannot spin forever.
        for region in list(pending_clicks):
            pending = pending_clicks[region]
            current_word = word_by_region.get(region, "")
            current_key = _normalize_word(current_word)

            # Keep the slot locked from re-mapping while a tap is in flight.
            blocked_new_word_regions.add(region)

            word_changed = (
                bool(current_key)
                and current_key not in {pending.raw_key, pending.key}
                and not _looks_like_clicked_word_noise(current_key, pending)
            )
            if word_changed:
                # The slot now shows a different word even though ours was never
                # confirmed by colour — abandon the in-flight tap and re-map.
                pending_clicks.pop(region, None)
                click_retry_counts.pop(region, None)
                tap_attempt_iter.pop(region, None)
                changed_pending_regions.add(region)
                blocked_new_word_regions.discard(region)
                _set_slot(
                    slot_states,
                    region,
                    None,
                    events=events,
                    iteration=iterations,
                    instance_id=ctx.instance_id,
                )
                _append_event(
                    events,
                    "reopened",
                    f"Word changed before colour confirm: {pending.raw_word} -> {current_word}",
                    iteration=iterations,
                    region=region,
                    word=pending.raw_word,
                    key=pending.key,
                    current_word=current_word,
                )
                continue

            attempts = click_retry_counts.get(region, 1)
            waited = iterations - tap_attempt_iter.get(region, iterations)
            if waited < tap_confirm_wait:
                # Still giving the colour detector time to confirm this tap.
                continue
            if attempts >= max_tap_attempts:
                # The colour never confirmed after the tap budget: surface the
                # slot as ``rejected`` (likely a wrong map coordinate) instead of
                # spinning or pretending it was clicked.
                pending_clicks.pop(region, None)
                click_retry_counts.pop(region, None)
                tap_attempt_iter.pop(region, None)
                exhausted_tap_signatures.add(
                    _tap_signature(region, pending.key, pending.point)
                )
                _set_slot(
                    slot_states,
                    region,
                    SlotFsmState(
                        status=_SLOT_TAP_REJECTED,
                        raw_word=pending.raw_word,
                        raw_key=pending.raw_key,
                        key=pending.key,
                        point=pending.point,
                    ),
                    events=events,
                    iteration=iterations,
                    instance_id=ctx.instance_id,
                )
                click_retry_exhausted.append(
                    {
                        "region": region,
                        "word": pending.raw_word,
                        "key": pending.key,
                        "retries": attempts,
                    }
                )
                logger.warning(
                    "dreamscape_memory_solve_loop: tap for %r key=%r region=%s never "
                    "colour-confirmed after %d attempts instance=%s",
                    pending.raw_word,
                    pending.key,
                    region,
                    attempts,
                    ctx.instance_id,
                )
                _append_event(
                    events,
                    "tap_unconfirmed",
                    f"Tap never colour-confirmed for {pending.raw_word} after {attempts} attempts",
                    iteration=iterations,
                    region=region,
                    word=pending.raw_word,
                    key=pending.key,
                    retries=attempts,
                )
                continue

            ok = await asyncio.to_thread(
                actions.tap,
                ctx.instance_id,
                pending.point,
                require_approval=False,
            )
            logger.info(
                "dreamscape_memory_solve_loop: %s re-tap word=%r key=%r region=%s -> "
                "(%d,%d) reason=colour-unconfirmed-retry instance=%s "
                "(awaiting colour confirm)",
                "tapped" if ok else "tap-retry-rejected",
                pending.raw_word,
                pending.key,
                region,
                pending.point.x,
                pending.point.y,
                ctx.instance_id,
            )
            _append_event(
                events,
                "retry",
                f"{'Re-tapped' if ok else 'Rejected re-tap'} {pending.raw_word} "
                "(awaiting colour confirm)",
                iteration=iterations,
                region=region,
                word=pending.raw_word,
                key=pending.key,
                x=pending.point.x,
                y=pending.point.y,
                ok=ok,
            )
            if ok:
                known_target_clicked = True
                next_attempt = attempts + 1
                click_retry_counts[region] = next_attempt
                tap_attempt_iter[region] = iterations
                click_retries.append(
                    {
                        "region": region,
                        "word": pending.raw_word,
                        "key": pending.key,
                        "retry": next_attempt,
                    }
                )
                taps_total += 1
                if tap_delay_s > 0:
                    await asyncio.sleep(tap_delay_s)
            else:
                pending_clicks.pop(region, None)
                click_retry_counts.pop(region, None)
                tap_attempt_iter.pop(region, None)
                exhausted_tap_signatures.add(
                    _tap_signature(region, pending.key, pending.point)
                )
                _set_slot(
                    slot_states,
                    region,
                    SlotFsmState(
                        status=_SLOT_TAP_REJECTED,
                        raw_word=pending.raw_word,
                        raw_key=pending.raw_key,
                        key=pending.key,
                        point=pending.point,
                    ),
                    events=events,
                    iteration=iterations,
                    instance_id=ctx.instance_id,
                )

        new_word_items = [
            (region, word)
            for region, word in word_items
            if region not in blocked_new_word_regions
        ]
        words = [word for _region, word in new_word_items]
        for word in words:
            key = _normalize_word(word)
            if key and key not in seen_keys:
                seen_keys.add(key)
                seen_words.append(word)

        targets = _targets_for_scene(scene)
        candidates, misses = _resolve_region_tap_candidates(
            new_word_items, targets, dev_w, dev_h, fuzz_threshold=fuzz_threshold
        )
        for _miss_region, miss in misses:
            if _is_actionable_unmapped_word(miss) and miss not in unmapped:
                unmapped.append(miss)
                _append_event(
                    events,
                    "unmapped",
                    f"Unmapped word: {miss}",
                    iteration=iterations,
                    region=_miss_region,
                    word=miss,
                    key=_normalize_word(miss),
                )
            if _is_actionable_unmapped_word(miss) and _miss_region:
                miss_key = _normalize_word(miss)
                unmapped_seen_counts[(_miss_region, miss_key)] = (
                    unmapped_seen_counts.get((_miss_region, miss_key), 0) + 1
                )
                _set_slot(
                    slot_states,
                    _miss_region,
                    SlotFsmState(
                        status=_SLOT_UNMAPPED,
                        raw_word=miss,
                        raw_key=miss_key,
                        key=miss_key,
                    ),
                    events=events,
                    iteration=iterations,
                    instance_id=ctx.instance_id,
                )

        for candidate in candidates:
            if (
                candidate.region
                and _tap_signature(candidate.region, candidate.key, candidate.point)
                in exhausted_tap_signatures
            ):
                _append_event(
                    events,
                    "skip_rejected",
                    f"Skipped exhausted tap {candidate.raw_word}",
                    iteration=iterations,
                    region=candidate.region,
                    word=candidate.raw_word,
                    key=candidate.key,
                    x=candidate.point.x,
                    y=candidate.point.y,
                )
                continue
            if candidate.key in clicked_keys:
                if candidate.raw_word not in skipped_clicked:
                    skipped_clicked.append(candidate.raw_word)
                _append_event(
                    events,
                    "skip_clicked",
                    f"Skipped already-clicked key {candidate.key}",
                    iteration=iterations,
                    region=candidate.region,
                    word=candidate.raw_word,
                    key=candidate.key,
                )
                continue
            if candidate.region:
                region_words[candidate.region] = candidate.raw_word
            if candidate.region:
                _set_slot(
                    slot_states,
                    candidate.region,
                    SlotFsmState(
                        status=_SLOT_MAPPED,
                        raw_word=candidate.raw_word,
                        raw_key=candidate.raw_key,
                        key=candidate.key,
                        point=candidate.point,
                    ),
                    events=events,
                    iteration=iterations,
                    instance_id=ctx.instance_id,
                )
                _append_event(
                    events,
                    "mapped",
                    f"Mapped {candidate.raw_word} -> {candidate.key}",
                    iteration=iterations,
                    region=candidate.region,
                    word=candidate.raw_word,
                    raw_key=candidate.raw_key,
                    key=candidate.key,
                    x=candidate.point.x,
                    y=candidate.point.y,
                )

        mapped_taps = await _dispatch_mapped_taps(
            ctx,
            actions,
            regions=regions,
            slot_states=slot_states,
            pending_clicks=pending_clicks,
            click_retry_counts=click_retry_counts,
            tap_attempt_iter=tap_attempt_iter,
            exhausted_tap_signatures=exhausted_tap_signatures,
            tap_delay_s=tap_delay_s,
            events=events,
            iteration=iterations,
        )
        if mapped_taps:
            taps_total += mapped_taps
            known_target_clicked = True

        # Defer the helper for any unmapped word seen on only a single
        # iteration — a transient read of an animating slot usually settles
        # into a real, mappable word next frame. Log the first deferral so the
        # solver log shows why the helper held off.
        for miss_region, miss in misses:
            if not (_is_actionable_unmapped_word(miss) and miss_region):
                continue
            miss_key = _normalize_word(miss)
            if miss_key in helped_keys:
                continue
            if unmapped_seen_counts.get((miss_region, miss_key), 0) >= _MIN_UNMAPPED_CONFIRM_READS:
                continue
            if (miss_region, miss_key) not in unconfirmed_logged:
                unconfirmed_logged.add((miss_region, miss_key))
                _append_event(
                    events,
                    "helper_unconfirmed",
                    f"Awaiting a confirmed read before helping {miss}",
                    iteration=iterations,
                    region=miss_region,
                    word=miss,
                    key=miss_key,
                )

        help_region_name, help_word = next(
            (
                (miss_region, miss)
                for miss_region, miss in misses
                if _is_actionable_unmapped_word(miss)
                and (key := _normalize_word(miss))
                and key not in helped_keys
                and unmapped_seen_counts.get((miss_region, key), 0)
                >= _MIN_UNMAPPED_CONFIRM_READS
            ),
            ("", ""),
        )
        if help_region_name in changed_pending_regions:
            _append_event(
                events,
                "helper_deferred",
                f"Deferred helper for fresh word {help_word}",
                iteration=iterations,
                region=help_region_name,
                word=help_word,
                key=_normalize_word(help_word),
            )
            help_region_name, help_word = "", ""
        if known_target_clicked and help_word and help_region:
            logger.info(
                "dreamscape_memory_solve_loop: deferring help for unmapped %r "
                "until mapped clicks settle instance=%s",
                help_word,
                ctx.instance_id,
            )
            _append_event(
                events,
                "helper_deferred",
                f"Deferred helper for {help_word} until mapped clicks settle",
                iteration=iterations,
                region=help_region_name,
                word=help_word,
                key=_normalize_word(help_word),
            )
        elif help_word and help_region and help_remaining > 0:
            help_point = _region_center_for_frame(area_doc, help_region, dev_w, dev_h)
            help_key = _normalize_word(help_word)
            if help_point is None:
                logger.warning(
                    "dreamscape_memory_solve_loop: help region not found/malformed: %s",
                    help_region,
                )
                help_learn_errors.append(
                    {"word": help_word, "scene": scene_slug, "reason": "help_region_missing"}
                )
                _append_event(
                    events,
                    "helper_error",
                    f"Helper region missing for {help_word}",
                    iteration=iterations,
                    word=help_word,
                    scene=scene_slug,
                    reason="help_region_missing",
                )
                helped_keys.add(help_key)
            else:
                if help_region_name:
                    region_words[help_region_name] = help_word
                    _set_slot(
                        slot_states,
                        help_region_name,
                        SlotFsmState(
                            status=_SLOT_HELP_REQUESTED,
                            raw_word=help_word,
                            raw_key=help_key,
                            key=help_key,
                        ),
                        events=events,
                        iteration=iterations,
                        instance_id=ctx.instance_id,
                    )
                    await _write_live_solve_state(
                        ctx,
                        regions=regions,
                        scene=last_scene_slug,
                        level_name=last_level_name,
                        iterations=iterations,
                        seen=seen_words,
                        clicked=clicked_words,
                        clicked_keys=clicked_keys,
                        clicked_regions=clicked_regions,
                        settled_regions=settled_regions,
                        pending_clicks=pending_clicks,
                        region_words=region_words,
                        slot_states=slot_states,
                        events=events,
                    )
                help_baseline = None
                try:
                    help_baseline = await _capture_fresh_frame(actions, ctx.instance_id)
                except Exception:
                    logger.debug(
                        "dreamscape_memory_solve_loop: pre-help baseline capture failed",
                        exc_info=True,
                    )
                ok = await asyncio.to_thread(
                    actions.tap,
                    ctx.instance_id,
                    help_point,
                    require_approval=False,
                )
                logger.info(
                    "dreamscape_memory_solve_loop: %s help-pill word=%r -> (%d,%d) "
                    "reason=unmapped-word-request-help instance=%s",
                    "tapped" if ok else "help-tap-rejected",
                    help_word,
                    help_point.x,
                    help_point.y,
                    ctx.instance_id,
                )
                _append_event(
                    events,
                    "helper_click",
                    f"{'Tapped' if ok else 'Rejected'} helper for {help_word}",
                    iteration=iterations,
                    region=help_region_name,
                    word=help_word,
                    key=help_key,
                    x=help_point.x,
                    y=help_point.y,
                    ok=ok,
                )
                if ok:
                    helped_keys.add(help_key)
                    helped_words.append(help_word)
                    help_remaining = max(0, help_remaining - 1)
                    taps_total += 1
                    if help_region_name:
                        _set_slot(
                            slot_states,
                            help_region_name,
                            SlotFsmState(
                                status=_SLOT_HELP_DETECTING,
                                raw_word=help_word,
                                raw_key=help_key,
                                key=help_key,
                            ),
                            events=events,
                            iteration=iterations,
                            instance_id=ctx.instance_id,
                        )
                    await _write_live_solve_state(
                        ctx,
                        regions=regions,
                        scene=last_scene_slug,
                        level_name=last_level_name,
                        iterations=iterations,
                        seen=seen_words,
                        clicked=clicked_words,
                        clicked_keys=clicked_keys,
                        clicked_regions=clicked_regions,
                        settled_regions=settled_regions,
                        pending_clicks=pending_clicks,
                        region_words=region_words,
                        slot_states=slot_states,
                        events=events,
                    )
                    target_point = await _tap_help_highlight_target(
                        actions,
                        ctx.instance_id,
                        capture_delay_s=help_capture_delay_s,
                        diff_gap_s=help_diff_gap_s,
                        before_frame=help_baseline,
                        word=help_word,
                    )
                    if target_point is not None:
                        help_target_taps.append(HelpTargetTap(help_word, target_point))
                        clicked_keys.add(help_key)
                        clicked_words.append(help_word)
                        if help_region_name:
                            clicked_regions.add(help_region_name)
                            click_retry_counts.pop(help_region_name, None)
                            _set_slot(
                                slot_states,
                                help_region_name,
                                SlotFsmState(
                                    status=_SLOT_CLICKED,
                                    raw_word=help_word,
                                    raw_key=help_key,
                                    key=help_key,
                                    point=target_point,
                                ),
                                events=events,
                                iteration=iterations,
                                instance_id=ctx.instance_id,
                            )
                        taps_total += 1
                        _append_event(
                            events,
                            "helper_target",
                            f"Tapped helper-highlight target for {help_word}",
                            iteration=iterations,
                            region=help_region_name,
                            word=help_word,
                            key=help_key,
                            x=target_point.x,
                            y=target_point.y,
                        )
                    else:
                        help_learn_errors.append(
                            {
                                "word": help_word,
                                "scene": scene_slug,
                                "reason": "target_not_detected",
                            }
                        )
                        _append_event(
                            events,
                            "helper_error",
                            f"Helper target not detected for {help_word}",
                            iteration=iterations,
                            word=help_word,
                            scene=scene_slug,
                            reason="target_not_detected",
                        )
                    if tap_delay_s > 0:
                        await asyncio.sleep(tap_delay_s)
                else:
                    help_learn_errors.append(
                        {"word": help_word, "scene": scene_slug, "reason": "help_tap_rejected"}
                    )
                    _append_event(
                        events,
                        "helper_error",
                        f"Helper tap rejected for {help_word}",
                        iteration=iterations,
                        word=help_word,
                        scene=scene_slug,
                        reason="help_tap_rejected",
                    )
        elif help_word and help_region:
            logger.info(
                "dreamscape_memory_solve_loop: no help remaining for unmapped %r instance=%s",
                help_word,
                ctx.instance_id,
            )
            _append_event(
                events,
                "helper_empty",
                f"No helper remaining for {help_word}",
                iteration=iterations,
                word=help_word,
                remaining=help_remaining,
            )

        if not word_items and not pending_clicks:
            terminal_screen, terminal_stop = await _check_terminal_screen(
                ctx,
                image,
                hint=terminal_screen or last_scene_slug or None,
                taps_total=taps_total,
            )
            if terminal_stop:
                stop_bot_after_result = True
                break

        await _write_live_solve_state(
            ctx,
            regions=regions,
            scene=last_scene_slug,
            level_name=last_level_name,
            iterations=iterations,
            seen=seen_words,
            clicked=clicked_words,
            clicked_keys=clicked_keys,
            clicked_regions=clicked_regions,
            settled_regions=settled_regions,
                pending_clicks=pending_clicks,
                region_words=region_words,
                slot_states=slot_states,
                events=events,
            )

        if wait_s > 0:
            await asyncio.sleep(wait_s)

    ctx.result.update(
        {
            "scene": last_scene_slug,
            "level_name": last_level_name,
            "regions": regions,
            "iterations": iterations,
            "seen": seen_words,
            "clicked": clicked_words,
            "clicked_keys": sorted(clicked_keys),
            "clicked_regions": sorted(clicked_regions),
            "settled_regions": sorted(settled_regions),
            "pending_click_regions": sorted(pending_clicks),
            "region_words": {
                region: word for region, word in region_words.items() if region in regions
            },
            "slot_states": _serialize_slot_states(slot_states, regions),
            "events": events[-_MAX_LIVE_EVENTS:],
            "click_retries": click_retries,
            "click_retry_exhausted": click_retry_exhausted,
            "helped": helped_words,
            "helped_keys": sorted(helped_keys),
            "help_target_taps": [
                {"word": tap.word, "x": tap.point.x, "y": tap.point.y}
                for tap in help_target_taps
            ],
            "learned_help_points": learned_help_points,
            "help_learn_errors": help_learn_errors,
            "help_counter_reads": help_counter_reads,
            "help_remaining": help_remaining,
            "skipped_clicked": skipped_clicked,
            "taps": taps_total,
            "unmapped": unmapped,
            "terminal_screen": terminal_screen,
            "status": (
                "won"
                if terminal_screen in {_TERMINAL_ALL_FOUND, _START_SCREEN}
                else "lost"
                if terminal_screen == _TERMINAL_TIME_UP
                else "stopped"
            ),
            "bot_stop": bot_stop,
        }
    )
    await _write_live_solve_state(
        ctx,
        regions=regions,
        scene=last_scene_slug,
        level_name=last_level_name,
        iterations=iterations,
        seen=seen_words,
        clicked=clicked_words,
        clicked_keys=clicked_keys,
        clicked_regions=clicked_regions,
        settled_regions=settled_regions,
        pending_clicks=pending_clicks,
        region_words=region_words,
        slot_states=slot_states,
        events=events,
        status=str(ctx.result.get("status") or "stopped"),
    )
    if stop_bot_after_result:
        bot_stop.update(
            await asyncio.to_thread(
                _request_local_bot_stop,
                f"terminal screen detected: {terminal_screen}",
            )
        )


DSL_EXEC_HANDLERS: dict[str, DslExecHandler] = {
    "dreamscape_memory_solve": _exec_dreamscape_memory_solve,
    "dreamscape_memory_solve_loop": _exec_dreamscape_memory_solve_loop,
}
