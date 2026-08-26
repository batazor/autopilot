from __future__ import annotations

import pytest

from ocr.word_cleaning import (
    clean_word_text,
    is_plausible_word_text,
    normalize_word_text,
)


def test_clean_word_text_splits_camel_and_strips_noise() -> None:
    assert clean_word_text("PocketWatch") == "Pocket Watch"
    assert clean_word_text("Clay Jug · 23%") == "Clay Jug"


def test_normalize_word_text_casefolds() -> None:
    assert normalize_word_text("Grilled Fish") == "grilled fish"


@pytest.mark.parametrize(
    "word",
    [
        "Snowman",
        "Grilled Fish",
        "Clay Jug",
        "Aurora",
        "Lightning",
        "Snowmann",  # plausibly OCR-garbled but still a real word
        "Axe",
    ],
)
def test_is_plausible_word_text_accepts_real_words(word: str) -> None:
    assert is_plausible_word_text(word) is True


@pytest.mark.parametrize(
    "garbage",
    [
        "ooceeeeenne EEEEEEEEEREET",  # the bug: OCR of an animating slot
        "eeeeeeee",  # 4+ same-char run
        "xkqrtwn",  # no vowels
        "aeiou aeio",  # no consonants
        "ababababab",  # 2 distinct chars over a long token
        "ab",  # too short
        "",
    ],
)
def test_is_plausible_word_text_rejects_garbage(garbage: str) -> None:
    assert is_plausible_word_text(garbage) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The «Белая мгла» client prints item words in Russian; an ASCII-only
        # filter used to strip them to "", so no RU word ever mapped to a point.
        ("Резиновая уточка", "резиновая уточка"),
        ("Шарф", "шарф"),
        # "ё" and "е" spellings must hash to one key.
        ("Ёлка", "елка"),
        ("ЖЁЛУДЬ", "желудь"),
        # Mixed reads keep both alphabets, punctuation still goes.
        ("Кот/Cat", "кот cat"),
    ],
)
def test_normalize_word_text_keeps_cyrillic(raw: str, expected: str) -> None:
    assert normalize_word_text(raw) == expected


@pytest.mark.parametrize("word", ["Шарф", "Паутина", "Морская звезда", "Кот"])
def test_is_plausible_word_text_accepts_russian_words(word: str) -> None:
    assert is_plausible_word_text(word) is True


@pytest.mark.parametrize("garbage", ["ыыыыыы", "кпрст", "ааа ооо"])
def test_is_plausible_word_text_rejects_russian_garbage(garbage: str) -> None:
    assert is_plausible_word_text(garbage) is False
