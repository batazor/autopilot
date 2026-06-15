"""Static research tech tree parsed from ``games/wos/db/research.yaml``.

No Redis, no ADB, no game IO — pure parsing + lookups, unit testable. Feeds
:mod:`planner`, which decides *which technology to research next* (the bot has
no such logic today).

``research.yaml`` has 9 ``branches`` (Growth / Economy / Battle / T11-T12 troop
lines); each branch has ``nodes`` with ``id``, ``line`` (family, e.g.
``tool_enhancement``), ``tier``, ``bonus``, ``requires`` (prerequisite node ids,
which must be MAXED to unlock this node) and ``levels``. Each level carries the
required Research Center building level (``rc``), ``time``, ``power`` and a flat
resource ``cost`` (already numeric — no suffix parsing needed).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# games/wos/core/research/planner/ → parents[3] = games/wos
DEFAULT_RESEARCH_PATH = Path(__file__).resolve().parents[3] / "db" / "research.yaml"

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
class ResearchLevel:
    """One level of a tech node: its RC gate, time, power and resource cost."""

    level: int
    rc: int                              # Research Center level required
    time_s: int
    power: int | None
    cost: Mapping[str, int]

    @property
    def total_cost(self) -> int:
        return sum(self.cost.values())


@dataclass(frozen=True, slots=True)
class ResearchNode:
    """One tech node: its line/tier, prerequisites and level ladder."""

    id: str
    branch: str
    name: str
    line: str
    tier: int
    bonus: str
    requires: tuple[str, ...]
    levels: tuple[ResearchLevel, ...]

    @property
    def max_level(self) -> int:
        return self.levels[-1].level if self.levels else 0

    def level_at(self, level: int) -> ResearchLevel | None:
        for lv in self.levels:
            if lv.level == level:
                return lv
        return None

    def next_after(self, current: int) -> ResearchLevel | None:
        """The next level above ``current`` (the one we'd research now)."""
        for lv in self.levels:
            if lv.level > current:
                return lv
        return None


@dataclass(frozen=True, slots=True)
class ResearchGraph:
    """Parsed tech tree + branch metadata + a reverse (unlocks) index."""

    nodes: Mapping[str, ResearchNode]
    branch_order: tuple[str, ...]                # branch ids in file order
    branch_labels: Mapping[str, str]
    _children: Mapping[str, tuple[str, ...]]     # node id → ids that require it

    def spec(self, node_id: str) -> ResearchNode | None:
        return self.nodes.get(node_id)

    def children(self, node_id: str) -> tuple[str, ...]:
        """Nodes whose ``requires`` lists ``node_id`` (what it unlocks)."""
        return self._children.get(node_id, ())


def _rc_value(raw: dict[str, Any]) -> int:
    """Required Research Center level. Numeric ``rc`` as-is; Fire-Crystal gates
    (``gate: "FC4"``, used by the T11/T12 branches) map after 30 → 34."""
    rc = raw.get("rc")
    if isinstance(rc, (int, float)):
        return int(rc)
    token = str(rc or raw.get("gate") or "").strip()
    if token.isdigit():
        return int(token)
    m = re.search(r"FC-?(\d+)", token, re.IGNORECASE)
    return 30 + int(m.group(1)) if m else 0


def _build_level(raw: dict[str, Any]) -> ResearchLevel:
    power = raw.get("power")
    return ResearchLevel(
        level=int(raw.get("level", 0)),
        rc=_rc_value(raw),
        time_s=parse_duration(raw.get("time")),
        power=int(power) if isinstance(power, (int, float)) else None,
        cost={str(k): int(v) for k, v in (raw.get("cost") or {}).items()},
    )


def load_research_graph(path: str | Path | None = None) -> ResearchGraph:
    """Parse ``research.yaml`` into a :class:`ResearchGraph`."""
    p = Path(path) if path else DEFAULT_RESEARCH_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    nodes: dict[str, ResearchNode] = {}
    branch_order: list[str] = []
    branch_labels: dict[str, str] = {}
    for branch in doc.get("branches") or []:
        bid = str(branch.get("id"))
        branch_order.append(bid)
        branch_labels[bid] = str(branch.get("label") or bid)
        for n in branch.get("nodes") or []:
            nid = str(n["id"])
            nodes[nid] = ResearchNode(
                id=nid,
                branch=bid,
                name=str(n.get("name") or nid),
                line=str(n.get("line") or nid),
                tier=int(n.get("tier", 0)),
                bonus=str(n.get("bonus") or ""),
                requires=tuple(str(r) for r in (n.get("requires") or [])),
                levels=tuple(_build_level(lv) for lv in (n.get("levels") or [])),
            )

    children: dict[str, list[str]] = {}
    for node in nodes.values():
        for req in node.requires:
            children.setdefault(req, []).append(node.id)

    return ResearchGraph(
        nodes=nodes,
        branch_order=tuple(branch_order),
        branch_labels=branch_labels,
        _children={k: tuple(v) for k, v in children.items()},
    )
