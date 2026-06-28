"""Pure parsers: OCR'd ``<domain>.read.*`` hash fields → planner ``owned`` dicts.

The reader cron OCRs each labeled cell into a Redis field
``<domain>.read.<entity>[.<stat>]`` (the ``store:`` target of an ``ocr:`` step).
All five screens are **fixed-position** layouts (the slots / pieces / stats are
known up front), so the labeler assigns a stable ``<entity>`` per region and these
parsers just collect them — no dynamic name matching. Pure + side-effect free, so
OCR calibration is validated by fixture tests without a device.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def _coerce_int(value: object) -> int | None:
    """Best-effort non-negative int from an OCR string (drops noise/blanks)."""
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    # Keep digits only (OCR often appends stray glyphs around the number).
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        n = int(digits)
    except ValueError:
        return None
    return n if n >= 0 else None


def parse_owned_flat(read_fields: Mapping[str, object], *, domain: str) -> dict[str, int]:
    """``<domain>.read.<entity>`` → ``{entity: level}`` (charms, gear ordinals)."""
    prefix = f"{domain}.read."
    out: dict[str, int] = {}
    for key, val in read_fields.items():
        if not key.startswith(prefix):
            continue
        entity = key[len(prefix):]
        if not entity or "." in entity:  # nested → wrong shape for this parser
            continue
        n = _coerce_int(val)
        if n is not None:
            out[entity] = n
    return out


def parse_owned_nested(
    read_fields: Mapping[str, object], *, domain: str, require: str | None = None
) -> dict[str, dict[str, int]]:
    """``<domain>.read.<entity>.<stat>`` → ``{entity: {stat: level}}`` (pets, hero_gear).

    ``require`` (e.g. ``"level"``) drops entities lacking that stat — used to filter
    out locked/empty roster slots that OCR'd as zero-stat cells.
    """
    prefix = f"{domain}.read."
    out: dict[str, dict[str, int]] = {}
    for key, val in read_fields.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        parts = rest.split(".")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            continue
        entity, stat = parts
        n = _coerce_int(val)
        if n is not None:
            out.setdefault(entity, {})[stat] = n
    if require is not None:
        out = {e: stats for e, stats in out.items() if require in stats}
    return out
