"""Human-like behaviour layer — randomised delays, curved swipes, idle breaks.

Every method in this module wraps AdbController calls with patterns that make
the bot indistinguishable from a slow human player:

  - Random inter-action delays sampled from realistic distributions
  - ±jitter on all coordinates (already in AdbController; this adds macro-level noise)
  - Occasional "misclick + correction" simulation
  - Periodic idle breaks (screen dimming, no input for N minutes)
  - Session-level timing: avoid perfectly regular task intervals
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta

from actions.tap import AdbController
from layout.types import Point

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delay distributions
# ---------------------------------------------------------------------------

def _think_delay() -> float:
    """Short pause before tapping — simulates human reaction (0.1–0.6 s)."""
    return random.gauss(mu=0.25, sigma=0.08)


def _read_delay() -> float:
    """Longer pause after a screen change — simulates reading (0.4–1.5 s)."""
    return random.gauss(mu=0.8, sigma=0.2)


def _slow_tap_delay() -> float:
    """Extra hesitation before an important confirm button (0.3–1.2 s)."""
    return random.uniform(0.3, 1.2)


def _between_tasks_delay() -> float:
    """Pause between independent tasks (2–8 s)."""
    return random.uniform(2.0, 8.0)


def _clamp_positive(val: float, lo: float = 0.05) -> float:
    return max(lo, val)


# ---------------------------------------------------------------------------
# Human-like tap helpers
# ---------------------------------------------------------------------------

async def human_tap(ctrl: AdbController, point: Point) -> None:
    """Tap with pre-tap think delay."""
    await asyncio.sleep(_clamp_positive(_think_delay()))
    ctrl.tap(point)


async def human_confirm_tap(ctrl: AdbController, point: Point) -> None:
    """Tap a confirm/submit button with a longer hesitation delay."""
    await asyncio.sleep(_clamp_positive(_slow_tap_delay()))
    ctrl.tap(point)


async def human_swipe(
    ctrl: AdbController,
    start: Point,
    end: Point,
    *,
    duration_ms: int | None = None,
) -> None:
    """Swipe with natural duration variance (±20% of base duration)."""
    base_ms = duration_ms or random.randint(250, 450)
    varied_ms = int(base_ms * random.uniform(0.8, 1.2))
    await asyncio.sleep(_clamp_positive(_think_delay()))
    ctrl.swipe(start, end, timedelta(milliseconds=varied_ms))


async def human_wait_after_screen_change() -> None:
    """Wait after navigating to a new screen (simulates reading the UI)."""
    await asyncio.sleep(_clamp_positive(_read_delay()))


async def human_between_tasks() -> None:
    """Pause between distinct bot tasks."""
    await asyncio.sleep(_clamp_positive(_between_tasks_delay()))


# ---------------------------------------------------------------------------
# Occasional misclick simulation
# ---------------------------------------------------------------------------

async def maybe_misclick(
    ctrl: AdbController, point: Point, *, probability: float = 0.04
) -> None:
    """With low probability, tap a wrong spot then correct to the real target.

    Simulates fat-finger moments that real players occasionally make.
    """
    if random.random() < probability:
        wrong = Point(
            point.x + random.randint(30, 80) * random.choice([-1, 1]),
            point.y + random.randint(10, 40) * random.choice([-1, 1]),
        )
        ctrl.tap(wrong)
        await asyncio.sleep(random.uniform(0.3, 0.7))
    await asyncio.sleep(_clamp_positive(_think_delay()))
    ctrl.tap(point)


# ---------------------------------------------------------------------------
# Idle / break simulation
# ---------------------------------------------------------------------------

async def maybe_idle_break(
    ctrl: AdbController,
    *,
    probability: float = 0.05,
    min_seconds: float = 30.0,
    max_seconds: float = 180.0,
) -> None:
    """With low probability, do nothing for a while (simulates phone put down).

    During the idle period no input is sent, which is the strongest signal a
    player is just watching the screen — not botting.
    """
    if random.random() < probability:
        duration = random.uniform(min_seconds, max_seconds)
        logger.debug("Idle break for %.0f s", duration)
        await asyncio.sleep(duration)


# ---------------------------------------------------------------------------
# Session-level timing jitter
# ---------------------------------------------------------------------------

def jitter_cooldown(base_seconds: int, *, spread_fraction: float = 0.10) -> int:
    """Add ±spread_fraction random noise to a cooldown period.

    Prevents tasks from firing at perfectly regular intervals, which is a
    strong bot detection signal in server-side telemetry.

    Example: jitter_cooldown(3600, spread_fraction=0.10) → 3240–3960 s
    """
    spread = int(base_seconds * spread_fraction)
    return base_seconds + random.randint(-spread, spread)


def jitter_session_start(base_hour: int, *, spread_minutes: int = 20) -> int:
    """Return a jittered start minute offset within a session hour.

    Prevents the bot from always logging in at exactly HH:00.
    Returns offset in seconds from the base_hour.
    """
    offset_minutes = random.randint(-spread_minutes, spread_minutes)
    return base_hour * 3600 + offset_minutes * 60
