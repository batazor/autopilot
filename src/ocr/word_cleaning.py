from __future__ import annotations

import re

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_ACRONYM_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_NON_LETTER_RE = re.compile(r"[^A-Za-z]+")


def clean_word_text(raw: object) -> str:
    """Keep item-word OCR to English letters/spaces and split CamelCase joins."""
    text = str(raw or "").replace("\n", " ")
    text = _ACRONYM_BOUNDARY_RE.sub(" ", text)
    text = _CAMEL_BOUNDARY_RE.sub(" ", text)
    text = _NON_LETTER_RE.sub(" ", text)
    return " ".join(text.split())


def normalize_word_text(raw: object) -> str:
    return clean_word_text(raw).casefold()


_VOWELS = frozenset("aeiou")


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
    repetition. OCR run on an unsettled/animating frame produces garbage like
    ``ooceeeeenne EEEEEEEEEREET`` — 4+ same-letter runs and very few distinct
    characters. Reject such reads so they never trigger helper taps or get
    persisted into the scene DB. Conservative by design: it should only fire on
    obvious noise, never on a real (even OCR-garbled) item word.
    """
    cleaned = clean_word_text(raw).casefold()
    letters = [ch for ch in cleaned if ch.isalpha()]
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
