"""Tunable defaults, regexes, and string enums for the Dreamscape Memory solver.

Pure data with no solver dependencies — imported by the other ``solver`` modules
and re-exported from ``exec.py`` so its handlers/loop refer to them unchanged.
"""

from __future__ import annotations

import re

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

# Slot FSM status values (internal; ``_public_slot_fsm_status`` maps them to the
# operator-facing vocabulary).
_SLOT_UNKNOWN = "unknown"
_SLOT_MAPPED = "mapped"
_SLOT_CLICKED = "clicked"
_SLOT_SETTLED = "settled"
_SLOT_UNMAPPED = "unmapped"
_SLOT_HELP_REQUESTED = "help_requested"
_SLOT_HELP_DETECTING = "help_detecting"
_SLOT_RETRY_EXHAUSTED = "retry_exhausted"
_SLOT_TAP_REJECTED = "tap_rejected"

_MULTIPLAYER_MODES = frozenset({"multiplayer", "mp", "coop", "co-op"})
