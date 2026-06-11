"""Research tree payload for the Next.js /research-tree page.

Single source of truth is ``games/<game>/db/research.yaml`` (loaded by
``config.research``). The page renders straight from this — no data is
duplicated in the frontend.
"""
from __future__ import annotations

from typing import Any

from config.research import get_research_registry


def get_research_payload() -> dict[str, Any]:
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
                            "tier": n.tier,
                            "levels": n.levels,
                            "bonus": n.bonus,
                            "requires": list(n.requires),
                        }
                        for n in b.nodes
                    ],
                }
                for b in g.branches
            ],
        }
        for g in get_research_registry()
    ]
    return {"games": games}
