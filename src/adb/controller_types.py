"""Shared dataclasses and tiny pure helpers for the ``AdbController`` mixins."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from layout.types import Point


@dataclass(frozen=True)
class ProcessDetection:
    """Structured result of a game-process liveness probe.

    ``found`` is the answer; ``pids`` lists every matching process (the main
    process plus sub-processes like ``com.gof.global:render``) when the winning
    method can report PIDs — ``dumpsys``/``am stack`` confirm presence but yield
    no PIDs, so ``pids`` is empty there even when ``found`` is true.
    ``method_used`` is the detection method that produced the verdict
    (``"none"`` if every method failed). ``error`` is ``None`` on a clean
    verdict — including a clean *not running* — and is only set when **every**
    method failed (ADB error/timeout), so callers can tell "process is dead"
    apart from "we could not ask".
    """

    found: bool
    pids: list[int]
    method_used: str
    error: str | None = None


@dataclass(frozen=True)
class _ShellOutcome:
    """Full result of one ADB shell invocation (``rc`` is ``None`` on timeout)."""

    rc: int | None
    stdout: str
    stderr: str

    @property
    def timed_out(self) -> bool:
        return self.rc is None


@dataclass
class _MethodOutcome:
    """Per-method detection result.

    ``error is None`` means the method ran cleanly; ``matched`` is then the
    authoritative found/not-found. ``error`` set means the method itself failed
    (timeout / non-zero rc) and the verdict is unknown — fall through to the
    next method.
    """

    matched: bool = False
    pids: list[int] = field(default_factory=list)
    error: str | None = None


def _parse_pids(text: str) -> list[int]:
    """Extract integer PIDs from whitespace-separated ``pidof`` output."""
    return [int(tok) for tok in text.split() if tok.isdigit()]


def _mentions_package(text: str, pkg: str) -> bool:
    """True when ``text`` contains ``pkg`` as a package/component token."""

    if not text or not pkg:
        return False
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(pkg)}(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def _gauss_between(lo: float, hi: float) -> float:
    """Gaussian sample centred on the band midpoint, clamped to ``[lo, hi]``.

    σ is a quarter of the band, so ~95% of draws land inside before clamping.
    Replaces uniform draws in input timing/aim: human values cluster around a
    typical point instead of spreading flat across the band.
    """

    if hi <= lo:
        return float(lo)
    mid = (lo + hi) / 2.0
    sigma = (hi - lo) / 4.0
    return min(float(hi), max(float(lo), random.gauss(mid, sigma)))


def _jitter(value: int, spread: int) -> int:
    """Apply ±spread pixel jitter, gaussian around the aim point (σ = spread/2)."""

    if spread <= 0:
        return value
    offset = int(round(random.gauss(0.0, spread / 2.0)))
    return value + max(-spread, min(spread, offset))


def _jittered_point(
    point: Point,
    *,
    spread: int,
    bounds: tuple[int, int] | None = None,
) -> Point:
    """Apply independent coordinate jitter and keep the result on-screen."""

    x = _jitter(point.x, spread)
    y = _jitter(point.y, spread)
    if bounds is not None:
        w, h = bounds
        x = _clamp(x, 0, max(0, w - 1))
        y = _clamp(y, 0, max(0, h - 1))
    return Point(x, y)


def _tap_offset_spread() -> int:
    """Small per-tap coordinate spread: one run chooses ±1, ±2, or ±3 px."""

    return random.randint(1, 3)
