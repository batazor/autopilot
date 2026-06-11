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


# "FC 1" / "FC-1" / "FC Lv. 10" right after a building name — the Fire Crystal
# ladder of that building (a separate fire_crystal_* entry in the registry).
_FC_RE = re.compile(r"^[\s\-]*fc[\s\-]*(?:lv\.?\s*)?(\d+)")


def _level_rank(level: int | str) -> tuple[int, int]:
    """Orderable rank for mixed levels: numeric < FC; higher number wins."""
    if isinstance(level, int):
        return (0, level)
    m = _NUM_RE.search(str(level))
    return (1, int(m.group()) if m else 0)


def refs_in_text(
    text: str,
    names: list[tuple[str, str]],
    fc_twins: dict[str, str] | None = None,
) -> dict[str, int | str]:
    """Resolve a prerequisites string to {building_id: required_level}.

    Levels are ints for the core ladder; "FC <n>" strings when the text
    references a building's Fire Crystal ladder ("Furnace FC1") — those refs
    point at the fire_crystal_* twin from ``fc_twins`` (base id -> twin id).
    """
    norm = normalize(text)
    refs: dict[str, int | str] = {}
    for name, bid in names:
        start = 0
        while (i := norm.find(name, start)) != -1:
            tail = norm[i + len(name) : i + len(name) + 14]
            fc = _FC_RE.match(tail)
            if fc and fc_twins and bid in fc_twins:
                target, level = fc_twins[bid], f"FC {int(fc.group(1))}"
            else:
                m = _NUM_RE.search(tail)
                target, level = bid, (int(m.group()) if m else 1)
            if _level_rank(level) > _level_rank(refs.get(target, 0)):
                refs[target] = level
            # Blank the match so a shorter name can't re-match inside it
            # (e.g. "Marksman Camp" inside "Fire Crystal Marksman Camp").
            norm = norm[:i] + (" " * len(name)) + norm[i + len(name) :]
            start = i + len(name)
    return refs
