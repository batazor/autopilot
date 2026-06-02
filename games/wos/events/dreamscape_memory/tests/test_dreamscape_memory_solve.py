"""Unit tests for the Dreamscape Memory solver's pure logic.

The handler's IO (Redis reads, taps) is thin; the logic worth protecting is
map parsing, word normalization, and percent->pixel tap resolution.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "dreamscape_memory_exec", MODULE_DIR / "exec.py"
)
assert _spec and _spec.loader
solve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(solve)


def _write_map(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "map.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_normalize_word_collapses_case_and_whitespace() -> None:
    assert solve._normalize_word("  Book ") == "book"
    assert solve._normalize_word("Camp\tFire") == "camp fire"
    assert solve._normalize_word(None) == ""


def test_load_targets_parses_and_skips_malformed(tmp_path: Path) -> None:
    path = _write_map(
        tmp_path,
        """
targets:
  Book:  { x: 48.5, y: 41.0 }
  WOLF:  { x: 44, y: 55.5 }
  broken: { x: 10 }
  alsobad: "nope"
""",
    )
    targets = solve._load_targets(path)
    assert targets == {"book": (48.5, 41.0), "wolf": (44.0, 55.5)}


def test_load_targets_empty_and_missing(tmp_path: Path) -> None:
    assert solve._load_targets(_write_map(tmp_path, "targets: {}\n")) == {}
    assert solve._load_targets(tmp_path / "absent.yaml") == {}


def test_resolve_taps_maps_words_to_pixels_and_reports_misses() -> None:
    targets = {"book": (50.0, 40.0), "smoke": (52.0, 30.0)}
    hits, misses = solve._resolve_taps(
        ["Book", "Wolf", "  smoke", ""], targets, 720, 1280
    )
    assert [(w, (p.x, p.y)) for w, p in hits] == [
        ("Book", (360, 512)),
        ("  smoke", (374, 384)),
    ]
    assert misses == ["Wolf"]
