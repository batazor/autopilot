"""Event registry loader.

Reads ``config/events.yaml`` once (cached) and exposes a small lookup API used
by ``tasks.dsl_exec._exec_scan_event_blocks`` to resolve an OCR string into a
canonical screen node name.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ocr.fuzzy import match as fuzzy_match


@dataclass(frozen=True)
class EventDef:
    name: str
    """Canonical screen-node id, e.g. ``event.trials``."""

    ocr_aliases: tuple[str, ...]
    """Lower-case OCR variants the scanner should accept."""


def _events_yaml_path() -> Path:
    return Path(__file__).resolve().with_name("events.yaml")


@lru_cache(maxsize=1)
def load_events() -> tuple[EventDef, ...]:
    path = _events_yaml_path()
    if not path.is_file():
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return ()
    out: list[EventDef] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        aliases = item.get("ocr_aliases") or []
        if not name or not isinstance(aliases, list):
            continue
        ocr_aliases = tuple(
            str(a).strip().lower() for a in aliases if str(a).strip()
        )
        if not ocr_aliases:
            continue
        out.append(EventDef(name=name, ocr_aliases=ocr_aliases))
    return tuple(out)


def match_event_by_ocr(text: str, *, threshold: float = 0.80) -> EventDef | None:
    """Resolve an OCR string to an :class:`EventDef`.

    Iterates each event's aliases through :func:`ocr.fuzzy.match`; the first
    alias clearing ``threshold`` wins. Numeric labels and timers (e.g.
    ``21:59:38`` for a chest cooldown) won't match any event and return None.
    """
    if not text or not text.strip():
        return None
    best: tuple[float, EventDef] | None = None
    for event in load_events():
        m = fuzzy_match(text, list(event.ocr_aliases), threshold=threshold)
        if m is None:
            continue
        if best is None or m.score > best[0]:
            best = (m.score, event)
    return best[1] if best else None
