"""Troop training cost + time per tier — the wostools-calculator MODEL, re-encoded.

Loads ``games/wos/db/troop_training.yaml`` (per-tier named-resource cost + time;
shared across the three troop types). Pure, lru-cached, same shape as
:mod:`core.building.fc_costs` / :mod:`core.resources.troop_stats`. Promotion
(T_n → T_{n+1}) is the per-tier *difference*, so no separate data is needed.

Ships against a STUB data file (the numbers aren't published in fetchable form);
every accessor degrades to empty/zero when a tier is missing, so the planner keeps
today's behaviour until the table is filled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from games.wos.core.building.planner.schedule import apply_speed

if TYPE_CHECKING:
    from collections.abc import Mapping

# games/wos/troops/planner/training_costs.py → parents[2] = games/wos
DEFAULT_TRAINING_PATH = Path(__file__).resolve().parents[2] / "db" / "troop_training.yaml"

_DAYS_RE = re.compile(r"(\d+)\s*d")
_HMS_RE = re.compile(r"(\d+):(\d+):(\d+)")


def parse_duration(value: Any) -> int:
    """Seconds from ``"00:01:34"``, ``"7d"``, ``"1d 02:03:04"``. ``0`` if empty."""
    if value is None:
        return 0
    s = str(value).strip()
    total = 0
    d = _DAYS_RE.search(s)
    if d:
        total += int(d.group(1)) * 86_400
    hms = _HMS_RE.search(s)
    if hms:
        h, m, sec = (int(x) for x in hms.groups())
        total += h * 3_600 + m * 60 + sec
    return total


@dataclass(frozen=True, slots=True)
class TrainTier:
    """One tier's per-troop training cost + time."""

    tier: int
    cost: Mapping[str, int]          # per ONE troop: meat / wood / coal / iron
    time_s: int                      # per ONE troop


@lru_cache(maxsize=2)
def load_training_costs(path: str | Path | None = None) -> dict[int, TrainTier]:
    """Parse the training table → ``tier → TrainTier`` (empty when the stub is empty)."""
    p = Path(path) if path else DEFAULT_TRAINING_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[int, TrainTier] = {}
    for row in doc.get("tiers") or []:
        if not isinstance(row, dict) or "tier" not in row:
            continue
        tier = int(row["tier"])
        out[tier] = TrainTier(
            tier=tier,
            cost={str(k): int(v) for k, v in (row.get("cost") or {}).items()},
            time_s=parse_duration(row.get("time")),
        )
    return out


def _scale(cost: Mapping[str, int], n: int) -> dict[str, int]:
    return {k: v * n for k, v in cost.items()}


def tier_cost_time(
    tier: int, *, batch: int = 1, table: Mapping[int, TrainTier] | None = None
) -> tuple[dict[str, int], int]:
    """Fresh-training ``(cost, time_s)`` for ``batch`` troops at ``tier``.

    ``({}, 0)`` when the tier has no data (stub) — the planner then behaves as before.
    """
    tbl = table if table is not None else load_training_costs()
    tt = tbl.get(int(tier))
    if tt is None:
        return {}, 0
    n = max(0, int(batch))
    return _scale(tt.cost, n), tt.time_s * n


def promote_cost_time(
    tier: int, *, batch: int = 1, table: Mapping[int, TrainTier] | None = None
) -> tuple[dict[str, int], int]:
    """Promotion ``(cost, time_s)`` to raise ``batch`` troops from ``tier-1`` to
    ``tier`` — the per-tier *difference* (the cheaper path vs fresh training).

    Falls back to the full ``tier`` cost when ``tier-1`` has no data; ``({}, 0)`` when
    ``tier`` itself is missing.
    """
    tbl = table if table is not None else load_training_costs()
    cur = tbl.get(int(tier))
    if cur is None:
        return {}, 0
    prev = tbl.get(int(tier) - 1)
    pcost = prev.cost if prev else {}
    ptime = prev.time_s if prev else 0
    n = max(0, int(batch))
    diff = {k: max(0, v - pcost.get(k, 0)) * n for k, v in cur.cost.items()}
    return diff, max(0, cur.time_s - ptime) * n


def train_eta(
    tier: int,
    count: int,
    *,
    speed_pct: float = 0.0,
    table: Mapping[int, TrainTier] | None = None,
) -> tuple[int, dict[str, int]]:
    """``(total_time_s, total_cost)`` to train ``count`` troops at ``tier``.

    Cost scales linearly with ``count``; the total time is shortened by a
    Training-Speed buff (``speed_pct`` — e.g. a Ling-Xue hero) via the shared
    :func:`apply_speed`. ``(0, {})`` when the tier is missing or ``count<=0``.
    """
    tbl = table if table is not None else load_training_costs()
    tt = tbl.get(int(tier))
    if tt is None or count <= 0:
        return 0, {}
    n = int(count)
    return apply_speed(tt.time_s * n, speed_pct), _scale(tt.cost, n)
