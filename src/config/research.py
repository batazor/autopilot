"""Research tree registry loader.

Source of truth: ``games/<game>/db/research.yaml`` (one file per game). Mirrors
the ``config.buildings`` style — a cached registry the API serializes for the
Next.js /research-tree page. No research data is duplicated in the frontend.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from config.games import iter_games, modules_root_for

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ResearchNode:
    id: str
    name: str
    tier: int
    levels: int
    bonus: str
    requires: tuple[str, ...]


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
        levels = int(raw.get("levels") or 0)
    except (TypeError, ValueError):
        tier, levels = 1, 0
    return ResearchNode(
        id=rid,
        name=name,
        tier=tier,
        levels=levels,
        bonus=str(raw.get("bonus") or "").strip(),
        requires=requires,
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


def load_research_game(game: str, repo_root: Path | None = None) -> ResearchGame | None:
    path = research_yaml_path(game, repo_root=repo_root)
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


def load_research(repo_root: Path | None = None) -> tuple[ResearchGame, ...]:
    games: list[ResearchGame] = []
    for game in iter_games(repo_root=repo_root):
        loaded = load_research_game(game, repo_root=repo_root)
        if loaded is not None and loaded.branches:
            games.append(loaded)
    return tuple(games)


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
