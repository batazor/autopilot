"""Research / alliance tech tree payloads for the Next.js /trees page.

Single source of truth is ``games/<game>/db/research.yaml`` and
``games/<game>/db/alliance_tech.yaml`` (loaded by ``config.research``). The
page renders straight from these — no data is duplicated in the frontend.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from config.research import get_alliance_tech_registry, get_research_registry

if TYPE_CHECKING:
    from config.research import ResearchGame


def serialize_games(registry: tuple[ResearchGame, ...]) -> dict[str, Any]:
    games = [
        {
            "id": g.id,
            "label": g.label,
            "source_url": g.source_url,
            "source_label": g.source_label,
            "branches": [
                {
                    "id": b.id,
                    "label": b.label,
                    "blurb": b.blurb,
                    "nodes": [
                        {
                            "id": n.id,
                            "name": n.name,
                            "line": n.line,
                            "tier": n.tier,
                            "bonus": n.bonus,
                            "requires": list(n.requires),
                            "levels": [
                                {
                                    "level": lv.level,
                                    "effect": lv.effect,
                                    "rc": lv.rc,
                                    "time": lv.time,
                                    "power": lv.power,
                                    "cost": lv.cost,
                                    "gate": lv.gate,
                                }
                                for lv in n.levels
                            ],
                        }
                        for n in b.nodes
                    ],
                }
                for b in g.branches
            ],
        }
        for g in registry
    ]
    return {"games": games}


def get_research_payload() -> dict[str, Any]:
    return serialize_games(get_research_registry())


def get_alliance_tech_payload() -> dict[str, Any]:
    return serialize_games(get_alliance_tech_registry())
