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
# Read-level floor is looser than the helper floor above: real 2-letter items
# exist («Ёж», «Як») and must stay readable, while 1-letter shreds ('ь' for
# «Мышь») only feed fuzzy-ambiguity spam and can never be an item.
_MIN_READ_WORD_LETTERS = 2
# Fuzzy recovery needs enough signal to be meaningful: WRatio on a 2-3 letter
# shred degenerates to substring matching («Па» -> «фотоаппарат» at 90) and
# taps the wrong item. Short words still map via EXACT lookup («Ёж», «Як»).
_MIN_FUZZY_WORD_LETTERS = 4
# A helper-learn is credited to OUR highlight tap only when the pill greys
# within this window after the tap. The operator plays alongside the solver:
# a grey that arrives later means a human finger found the item, and the
# solver's (possibly mis-detected) highlight point must not be learned —
# that is exactly how «Колокол» got attached to Feather's coordinates.
_LEARN_CONFIRM_WINDOW_S = 4.0
# An unmapped word must be read on this many separate iterations before it is
# allowed to spend a (slow, irreversible) helper tap + scene-DB learn. A single
# transient read — e.g. OCR of an animating slot — is never enough; the slot
# usually settles into a real, mappable word on the next frame.
_MIN_UNMAPPED_CONFIRM_READS = 2
# A read only counts toward the helper confirmation above when OCR itself was
# confident in it. Dialog text bleeding into the slot band reads stably but
# weakly (0.0–0.4), so without this floor a covered screen "confirms" garbage
# and burns a helper tap on it ("В РАБ" → help). Real pill words read 0.9+.
_MIN_UNMAPPED_CONFIRM_CONF = 0.8
# Long RU item names wrap onto TWO lines inside the pill; the labeled OCR band
# is a single-line strip through the middle and reads garbage on them. A weak
# first read (below the confidence floor) retries with the band grown
# vertically by this fraction of its height on each side, in block mode
# (PSM 6) — measured on a live two-line pill: narrow single-line read
# "океана"@0.06 vs grown block read "Бамбуковая корзина"@0.94.
_TWO_LINE_RETRY_CONF = 0.6
_TWO_LINE_GROW_FRAC = 0.55
# Last-resort pass for tall two-line pills the first retry still misses
# («Канистра с топливом» read as '' at grow 0.55): grow the band almost a full
# pill height each side and upscale — reads the second line ('топливом'@0.65),
# which fuzzy recovery maps to the item.
_TWO_LINE_GROW_FRAC_WIDE = 0.9
_TWO_LINE_UPSCALE = 3

# A word pill has exactly TWO background colours; classify by nearest centroid.
# Medians measured over 18 pills / 6 real frames (720x1280 RU client): active
# pale-lavender ~(224,183,178) BGR, struck slate ~(207,147,132) BGR — centroid
# gap ~62. A background farther than the cutoff from BOTH references is not a
# pill (dark shade, popup, animation frame).
_PILL_BG_ACTIVE_BGR = (224.0, 183.0, 178.0)
_PILL_BG_STRUCK_BGR = (207.0, 147.0, 132.0)
_PILL_BG_MAX_REF_DIST = 60.0

# Pill template bank (solver/pill_bank.py): a word's pill renders
# pixel-identically every round at the fixed resolution, so a tight crop of
# the WHITE TEXT matched with TM_CCOEFF_NORMED beats OCR outright on repeat
# words. Thresholds calibrated on 86 labeled slot crops from 96 real lossless
# frames (720x1280 RU client): same-word pairs across frames/slots score
# ≥0.999 with text-mask IoU ≥0.997; the closest different-word impostor
# («мяч» vs «меч») reaches only 0.914 / 0.807, and an active-pill template
# never exceeds 0.45 against a struck pill. Whole-band matching was rejected:
# the pill fill dominates and «мяч»/«меч» collide at 0.965 there.
_PILL_TMPL_SCORE_THR = 0.96
_PILL_TMPL_IOU_THR = 0.92
# White-glyph binarization floor (gray ≥ this = text) shared by template
# extraction and the IoU gate; the pale-lavender fill sits far below it.
_PILL_TMPL_TEXT_THR = 200
_PILL_TMPL_MARGIN_PX = 3
# A real word's glyphs light hundreds of pixels; fewer means an empty band or
# stray sparkle — nothing worth storing.
_PILL_TMPL_MIN_TEXT_PX = 40
# Renderings per key: single-player and multiplayer pills may differ, plus one
# spare; an unmatched rendering beyond the cap is dropped loudly, because a
# key needing many variants means the key itself is being mis-assigned.
_PILL_TMPL_MAX_VARIANTS = 4
# Synthetic confidence injected for template-matched words: above every OCR
# gate in the loop (the match margin is far stronger evidence than a Tesseract
# self-score, see the calibration numbers above).
_PILL_TMPL_MATCH_CONF = 0.99
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
