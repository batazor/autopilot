"""Research tree registry loader.

Source of truth: ``games/<game>/db/research.yaml`` (one file per game). Mirrors
the ``config.buildings`` style — a cached registry the API serializes for the
Next.js /research-tree page. No research data is duplicated in the frontend.

``games/<game>/db/alliance_tech.yaml`` shares the exact same schema (branches →
nodes → levels), so the alliance tech tree reuses these dataclasses and parsers
with its own registry below.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from config.games import iter_games, modules_root_for

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass(frozen=True)
class ResearchLevel:
    level: int
    effect: str
    rc: int | None
    time: str
    power: int | None
    # resource name -> amount. Core: meat/wood/coal/iron/steel.
    # Fire Crystal era also: fire_crystal/refined_fc/fc_shards.
    cost: dict[str, int]
    # Fire Crystal era gate, e.g. "FC10" (War Academy level); "" for core techs.
    gate: str = ""


@dataclass(frozen=True)
class ResearchNode:
    id: str
    name: str
    line: str
    tier: int
    bonus: str
    requires: tuple[str, ...]
    levels: tuple[ResearchLevel, ...]


@dataclass(frozen=True)
class ResearchBranch:
    id: str
    label: str
    blurb: str
    nodes: tuple[ResearchNode, ...]


@dataclass(frozen=True)
class ResearchGame:
    id: str
    label: str
    source_url: str
    source_label: str
    branches: tuple[ResearchBranch, ...]


def research_yaml_path(game: str, repo_root: Path | None = None) -> Path:
    return modules_root_for(game, repo_root=repo_root) / "db" / "research.yaml"


def alliance_tech_yaml_path(game: str, repo_root: Path | None = None) -> Path:
    return modules_root_for(game, repo_root=repo_root) / "db" / "alliance_tech.yaml"


def _parse_node(raw: dict[str, object]) -> ResearchNode | None:
    rid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not rid or not name:
        return None
    requires_raw = raw.get("requires") or []
    requires = tuple(
        str(r).strip()
        for r in (requires_raw if isinstance(requires_raw, list) else [])
        if str(r).strip()
    )
    try:
        tier = int(raw.get("tier") or 1)
    except (TypeError, ValueError):
        tier = 1
    levels_raw = raw.get("levels") or []
    levels = tuple(
        lvl
        for lvl in (
            _parse_level(item)
            for item in (levels_raw if isinstance(levels_raw, list) else [])
            if isinstance(item, dict)
        )
        if lvl is not None
    )
    return ResearchNode(
        id=rid,
        name=name,
        line=str(raw.get("line") or "").strip(),
        tier=tier,
        bonus=str(raw.get("bonus") or "").strip(),
        requires=requires,
        levels=levels,
    )


def _parse_level(raw: dict[str, object]) -> ResearchLevel | None:
    try:
        level = int(raw.get("level") or 0)
    except (TypeError, ValueError):
        return None
    if level <= 0:
        return None

    def _int_or_none(key: str) -> int | None:
        val = raw.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    cost_raw = raw.get("cost") or {}
    cost = {
        str(k): int(v)
        for k, v in (cost_raw.items() if isinstance(cost_raw, dict) else [])
        if isinstance(v, (int, float))
    }
    return ResearchLevel(
        level=level,
        effect=str(raw.get("effect") or "").strip(),
        rc=_int_or_none("rc"),
        time=str(raw.get("time") or "").strip(),
        power=_int_or_none("power"),
        cost=cost,
        gate=str(raw.get("gate") or "").strip(),
    )


def _parse_branch(raw: dict[str, object]) -> ResearchBranch | None:
    bid = str(raw.get("id") or "").strip()
    if not bid:
        return None
    nodes_raw = raw.get("nodes") or []
    nodes = tuple(
        n
        for n in (
            _parse_node(item)
            for item in (nodes_raw if isinstance(nodes_raw, list) else [])
            if isinstance(item, dict)
        )
        if n is not None
    )
    return ResearchBranch(
        id=bid,
        label=str(raw.get("label") or bid).strip(),
        blurb=str(raw.get("blurb") or "").strip(),
        nodes=nodes,
    )


def _load_game_yaml(path: Path, game: str) -> ResearchGame | None:
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return None
    branches_raw = raw.get("branches") or []
    branches = tuple(
        b
        for b in (
            _parse_branch(item)
            for item in (branches_raw if isinstance(branches_raw, list) else [])
            if isinstance(item, dict)
        )
        if b is not None
    )
    return ResearchGame(
        id=str(raw.get("game") or game).strip(),
        label=str(raw.get("label") or game).strip(),
        source_url=str(raw.get("source_url") or "").strip(),
        source_label=str(raw.get("source_label") or "").strip(),
        branches=branches,
    )


def load_research_game(game: str, repo_root: Path | None = None) -> ResearchGame | None:
    return _load_game_yaml(research_yaml_path(game, repo_root=repo_root), game)


def load_alliance_tech_game(game: str, repo_root: Path | None = None) -> ResearchGame | None:
    return _load_game_yaml(alliance_tech_yaml_path(game, repo_root=repo_root), game)


def _load_all(
    loader: Callable[..., ResearchGame | None], repo_root: Path | None = None
) -> tuple[ResearchGame, ...]:
    games: list[ResearchGame] = []
    for game in iter_games(repo_root=repo_root):
        loaded = loader(game, repo_root=repo_root)
        if loaded is not None and loaded.branches:
            games.append(loaded)
    return tuple(games)


def load_research(repo_root: Path | None = None) -> tuple[ResearchGame, ...]:
    return _load_all(load_research_game, repo_root=repo_root)


def load_alliance_tech(repo_root: Path | None = None) -> tuple[ResearchGame, ...]:
    return _load_all(load_alliance_tech_game, repo_root=repo_root)


# ---------------------------------------------------------------------------
# Global registry cache (mirrors ``config.buildings`` style)
# ---------------------------------------------------------------------------

_registry: tuple[ResearchGame, ...] | None = None
_registry_lock = threading.Lock()


def invalidate_research_registry() -> None:
    global _registry
    with _registry_lock:
        _registry = None


def get_research_registry() -> tuple[ResearchGame, ...]:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = load_research()
    return _registry


_alliance_registry: tuple[ResearchGame, ...] | None = None


def invalidate_alliance_tech_registry() -> None:
    global _alliance_registry
    with _registry_lock:
        _alliance_registry = None


def get_alliance_tech_registry() -> tuple[ResearchGame, ...]:
    global _alliance_registry
    if _alliance_registry is None:
        with _registry_lock:
            if _alliance_registry is None:
                _alliance_registry = load_alliance_tech()
    return _alliance_registry
