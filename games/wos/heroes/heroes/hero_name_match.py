"""Map an OCR'd hero display name to its canonical registry id.

The new-hero unlock page (``heroes.sr.new``) only shows the hero's *name* — there
is no portrait template to match against, so identity comes from OCR. OCR of the
white-on-gold title is high-confidence but not perfect, so the match is:

1. **Exact** on the normalised slug (``"Renee"`` → ``"renee"``). The normaliser
   strips spaces/punctuation, so multi-word ids match too (``"Ling Xue"`` →
   ``"lingxue"`` → id ``ling_xue``).
2. **Fuzzy** (``difflib``) against every hero's normalised name *and* id, to
   survive a one-character OCR slip (``"Renae"`` → ``renee``).

Pure + dependency-light so it is unit-testable without a device or Redis.
"""
from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from config.building_name_parser import normalise_building_lookup_text as _norm

if TYPE_CHECKING:
    from config.heroes import HeroRegistry

# Fuzzy floor. OCR of a clean title is ~0.95+; a single dropped/added letter on a
# 5-7 char name still scores well above 0.6, while unrelated noise stays below it.
_FUZZY_CUTOFF = 0.6


def _normalised_index(registry: HeroRegistry) -> dict[str, str]:
    """``{normalised name | normalised id: hero_id}`` for the whole catalog.

    Name keys win over id keys on collision (display name is what OCR reads), but
    both are indexed so an id-shaped OCR ("ling_xue") still resolves.
    """
    index: dict[str, str] = {}
    for hero in registry.heroes:
        id_key = _norm(hero.id)
        if id_key:
            index.setdefault(id_key, hero.id)
    # Second pass so name keys overwrite any colliding id key.
    for hero in registry.heroes:
        name_key = _norm(hero.name)
        if name_key:
            index[name_key] = hero.id
    return index


def match_hero_id(
    raw_name: str,
    registry: HeroRegistry,
    *,
    cutoff: float = _FUZZY_CUTOFF,
) -> tuple[str | None, float]:
    """Resolve an OCR'd name to ``(hero_id, score)``.

    ``score`` is ``1.0`` for an exact slug hit, the ``difflib`` ratio for a fuzzy
    hit, or ``0.0`` when nothing clears ``cutoff`` (``hero_id`` is then ``None``).
    """
    key = _norm(raw_name)
    if not key:
        return None, 0.0

    index = _normalised_index(registry)
    if key in index:
        return index[key], 1.0

    choices = list(index.keys())
    hit = difflib.get_close_matches(key, choices, n=1, cutoff=cutoff)
    if not hit:
        return None, 0.0
    score = difflib.SequenceMatcher(None, key, hit[0]).ratio()
    return index[hit[0]], round(score, 3)
