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

# RU → Latin transliteration for the fallback match: an RU-build card whose hero
# has no ``aliases:`` entry yet still resolves when its transliterated name is
# close to the EN one («Патрик» → "patrik" ≈ "patrick"). Standard GOST-ish table;
# multi-letter targets are fine — the comparison is fuzzy, not exact.
_RU_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def transliterate_ru(text: str) -> str:
    """Lower-cased RU→Latin transliteration (non-Cyrillic chars pass through)."""
    return (text or "").lower().translate(_RU_TRANSLIT)


def _normalised_index(registry: HeroRegistry) -> dict[str, str]:
    """``{normalised name | id | alias: hero_id}`` for the whole catalog.

    Name keys win over id keys on collision (display name is what OCR reads), but
    both are indexed so an id-shaped OCR ("ling_xue") still resolves. Localized
    ``aliases`` (e.g. RU «Патрик») are indexed too — fuzzy matching against the
    Cyrillic alias absorbs OCR noise the transliteration fallback can't
    («Лжина» ≈ «джина»).
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
        for alias in getattr(hero, "aliases", ()) or ():
            alias_key = _norm(alias)
            if alias_key:
                index[alias_key] = hero.id
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
        # RU fallback: transliterate a Cyrillic read and retry against the
        # EN name/id keys — covers heroes without an ``aliases:`` entry.
        translit = _norm(transliterate_ru(key))
        if translit and translit != key:
            hit = difflib.get_close_matches(translit, choices, n=1, cutoff=cutoff)
            if hit:
                score = difflib.SequenceMatcher(None, translit, hit[0]).ratio()
                return index[hit[0]], round(score, 3)
        return None, 0.0
    score = difflib.SequenceMatcher(None, key, hit[0]).ratio()
    return index[hit[0]], round(score, 3)
