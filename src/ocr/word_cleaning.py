from __future__ import annotations

import re

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_ACRONYM_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
# Latin + Cyrillic: the «Белая мгла» client (catalog ``wos_ru``, OCR lang
# ``rus``) prints item words in Russian, and an ASCII-only class stripped them
# to "" — every RU word then read as empty and no item was ever mapped. For
# ASCII text the two classes are equivalent, so the English path is unchanged.
_NON_LETTER_RE = re.compile(r"[^A-Za-zА-Яа-яЁё0-9]+")


def clean_word_text(raw: object) -> str:
    """Keep item-word OCR to letters/spaces and split CamelCase joins."""
    text = str(raw or "").replace("\n", " ")
    text = _ACRONYM_BOUNDARY_RE.sub(" ", text)
    text = _CAMEL_BOUNDARY_RE.sub(" ", text)
    text = _NON_LETTER_RE.sub(" ", text)
    # "ё" is printed inconsistently (game text, community lists, OCR alike), so
    # fold it onto "е" — both spellings then hash to one key.
    text = text.replace("ё", "е").replace("Ё", "Е")
    return " ".join(text.split())


def normalize_word_text(raw: object) -> str:
    return clean_word_text(raw).casefold()


_VOWELS = frozenset("aeiou" + "аеиоуыэюя")


def _max_char_run(token: str) -> int:
    """Length of the longest run of one repeated character in ``token``."""
    best = run = 0
    prev = ""
    for ch in token:
        run = run + 1 if ch == prev else 1
        prev = ch
        if run > best:
            best = run
    return best


def is_plausible_word_text(raw: object, *, min_letters: int = 3) -> bool:
    """Heuristic gate rejecting OCR noise before costly helper/learn actions.

    Real item words mix vowels and consonants with limited character
    repetition (Russian words too — ``_VOWELS`` covers both alphabets). OCR
    run on an unsettled/animating frame produces garbage like
    ``ooceeeeenne EEEEEEEEEREET`` — 4+ same-letter runs and very few distinct
    characters. Reject such reads so they never trigger helper taps or get
    persisted into the scene DB. Conservative by design: it should only fire on
    obvious noise, never on a real (even OCR-garbled) item word.
    """
    cleaned = clean_word_text(raw).casefold()
    letters = [ch for ch in cleaned if ch.isalpha()]
    if not letters:
        # Digit-only item words exist (the «8» on the city-hall wall). A short
        # digit run is a real word for lenient callers (the read gate,
        # min_letters<=2); strict callers (helper/learn, min_letters>=3) still
        # reject digits so timer bleed can never spend a hint.
        compact = cleaned.replace(" ", "")
        return bool(compact) and compact.isdigit() and min_letters <= 2 and len(compact) <= 3
    if len(letters) < min_letters:
        return False
    if not any(ch in _VOWELS for ch in letters):
        return False
    if not any(ch not in _VOWELS for ch in letters):
        return False
    for token in cleaned.split():
        if _max_char_run(token) >= 4:
            return False
        if len(token) >= 8 and len(set(token)) / len(token) < 0.4:
            return False
    return True
