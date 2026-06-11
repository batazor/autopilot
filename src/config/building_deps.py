"""Resolve free-text building prerequisites into structured dependency refs.

Shared by the API serializer (``api.services.buildings_api``) and the derive
script (``scripts/derive_building_requires.py``). The per-level
``prerequisites`` strings (e.g. "Embassy Lv. 8 Infirmary Lv. 1") are matched
against known building names to produce ``{building_id: level}`` refs.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.buildings import BuildingDef

# British/American + punctuation variants in the wiki text that don't match the
# canonical building names verbatim.
_ALIASES = {"command centre": "command_center"}
_NUM_RE = re.compile(r"\d+")


def normalize(text: str) -> str:
    return text.replace("’", "'").replace("`", "'").lower().strip()


def name_index(buildings: tuple[BuildingDef, ...]) -> list[tuple[str, str]]:
    """(normalized name, id) pairs, longest name first so multi-word names win."""
    pairs = {normalize(b.name): b.id for b in buildings}
    pairs.update(_ALIASES)
    return sorted(pairs.items(), key=lambda kv: -len(kv[0]))


def refs_in_text(text: str, names: list[tuple[str, str]]) -> dict[str, int]:
    """Resolve a prerequisites string to {building_id: required_level}."""
    norm = normalize(text)
    refs: dict[str, int] = {}
    for name, bid in names:
        start = 0
        while (i := norm.find(name, start)) != -1:
            tail = norm[i + len(name) : i + len(name) + 14]
            m = _NUM_RE.search(tail)
            level = int(m.group()) if m else 1
            refs[bid] = max(refs.get(bid, 0), level)
            # Blank the match so a shorter name can't re-match inside it
            # (e.g. "Marksman Camp" inside "Fire Crystal Marksman Camp").
            norm = norm[:i] + (" " * len(name)) + norm[i + len(name) :]
            start = i + len(name)
    return refs
