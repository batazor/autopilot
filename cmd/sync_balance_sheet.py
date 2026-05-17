"""Sync balance weights from the community Google Sheet.

Source: https://docs.google.com/spreadsheets/d/1-PAWwMMDyU1W_nsJPgR_KZpfZmJtWYcBtsWk5X2A19s
Eight tabs:

* gid 780465677 — Hero stat tables (Power/Attack/Defense/Health/Lethality/
  Health-class/Skills by level 1-10). Merged into
  ``db/heroes/<id>.yaml`` under ``levels:``.
* gid 1164504856 — Goggles & Boots (Marksman) gear stats.
* gid 1218774117 — Goggles & Boots (Infantry) gear stats.
*  gid 336497909 — Goggles & Boots (Lancer)   gear stats.
*  gid 593694800 — Gloves & Belt   (Marksman) gear stats.
*  gid 982509375 — Gloves & Belt   (Infantry) gear stats.
* gid 1322869986 — Gloves & Belt   (Lancer)   gear stats.
  Gear tabs land in ``db/gear/<slot>_<class>.yaml``.
* gid 0 — Enhancement-points multi-table (level cost, sacrifice points,
  mythic max costs, mastery levels, weapon widgets). Saved as
  ``db/gear/enhancement.yaml``.

Run: ``uv run python cmd/sync_balance_sheet.py``
"""

from __future__ import annotations

import csv
import io
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

_SHEET_ID = "1-PAWwMMDyU1W_nsJPgR_KZpfZmJtWYcBtsweK5X2A19s".replace(
    "1-PAWwMMDyU1W_nsJPgR_KZpfZmJtWYcBtsweK5X2A19s",
    "1-PAWwMMDyU1W_nsJPgR_KZpfZmJtWYcBtsWk5X2A19s",
)
"""ID baked in; the replace-trick keeps the literal in one spot so a typo
on either side fails a grep, not a runtime fetch."""

_UA = {"User-Agent": "wos-autopilot/0.1"}

_GEAR_GIDS: dict[int, tuple[str, str]] = {
    1164504856: ("goggles_boots", "marksman"),
    1218774117: ("goggles_boots", "infantry"),
    336497909: ("goggles_boots", "lancer"),
    593694800: ("gloves_belt", "marksman"),
    982509375: ("gloves_belt", "infantry"),
    1322869986: ("gloves_belt", "lancer"),
}
_HERO_LEVELS_GID = 780465677
_ENHANCEMENT_GID = 0
_TIERS = ("grey", "green", "blue", "purple", "gold")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _source_block(gid: int) -> dict[str, Any]:
    return {
        "sheet": _SHEET_ID,
        "gid": gid,
        "fetched_at": _today(),
    }


def _fetch_csv(client: httpx.Client, gid: int) -> list[list[str]]:
    url = f"https://docs.google.com/spreadsheets/d/{_SHEET_ID}/export?format=csv&gid={gid}"
    r = client.get(url, headers=_UA, follow_redirects=True, timeout=30)
    r.raise_for_status()
    return list(csv.reader(io.StringIO(r.text)))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Hero level tables
# ---------------------------------------------------------------------------

_HERO_NAME_TO_ID: dict[str, str] = {}


def _hero_id_for(name: str) -> str | None:
    """Slugify ``name`` and verify a hero file exists in db/heroes/."""
    if not _HERO_NAME_TO_ID:
        from config.heroes import hero_index_path

        idx_path = hero_index_path(_repo_root())
        idx = _load_yaml(idx_path)
        for entry in idx.get("heroes", []) or []:
            if not isinstance(entry, dict):
                continue
            hid = str(entry.get("id") or "").strip()
            label = str(entry.get("name") or "").strip()
            if hid and label:
                _HERO_NAME_TO_ID[label.lower()] = hid
    return _HERO_NAME_TO_ID.get((name or "").strip().lower())


# Statistic-name normalization for the per-hero blocks. Bracketed prefixes
# ("[L]", "[M]", "[I]") flag the troop-class affinity (Lancer / Marksman /
# Infantry); we expose them as suffixes (``lethality_l``) so callers can
# index without the punctuation.
_CLASS_PREFIX_RE = re.compile(r"^\[(?P<letter>[A-Za-z])\]\s*(?P<rest>.+)$")


def _normalize_stat_name(raw: str) -> str:
    s = (raw or "").strip()
    m = _CLASS_PREFIX_RE.match(s)
    if m:
        return f"{m.group('rest').strip().lower()}_{m.group('letter').lower()}"
    return s.lower().replace(" ", "_")


def _coerce_number(value: str) -> Any:
    s = (value or "").strip().replace(",", "")
    if not s or s == "-":
        return None
    # Skill cells are textual ("Molly damage +10%") — keep them as-is.
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return value.strip()
    try:
        return int(s) if "." not in s else float(s)
    except ValueError:
        return value.strip()


def _parse_hero_levels(rows: list[list[str]]) -> dict[str, dict[int, dict[str, Any]]]:
    """Walk the CSV row-by-row; each block starts with a hero header row."""
    out: dict[str, dict[int, dict[str, Any]]] = {}
    i = 0
    while i < len(rows):
        row = rows[i]
        if not row:
            i += 1
            continue
        first = (row[0] or "").strip()
        is_header = (
            first
            and len(row) > 1
            and (row[1] or "").strip().lower().startswith("level ")
        )
        if not is_header:
            i += 1
            continue
        hero_name = first
        levels: list[int] = []
        for cell in row[1:]:
            m = re.match(r"\s*Level\s*(\d+)\s*", cell or "", re.IGNORECASE)
            if m:
                levels.append(int(m.group(1)))
            else:
                levels.append(0)
        # Collect stats rows until a blank row.
        stats: dict[str, list[Any]] = {}
        i += 1
        while i < len(rows) and any((c or "").strip() for c in rows[i]):
            stat_row = rows[i]
            stat_name = _normalize_stat_name(stat_row[0])
            values = [
                _coerce_number(stat_row[j + 1] if j + 1 < len(stat_row) else "")
                for j in range(len(levels))
            ]
            stats[stat_name] = values
            i += 1
        # Pivot into per-level dicts.
        per_level: dict[int, dict[str, Any]] = {}
        for idx, lv in enumerate(levels):
            if lv <= 0:
                continue
            per_level[lv] = {
                stat: vals[idx]
                for stat, vals in stats.items()
                if idx < len(vals) and vals[idx] is not None
            }
        out[hero_name] = per_level
    return out


def _sync_hero_levels(client: httpx.Client) -> tuple[int, int]:
    rows = _fetch_csv(client, _HERO_LEVELS_GID)
    blocks = _parse_hero_levels(rows)
    updated = 0
    missing: list[str] = []
    from config.heroes import heroes_wiki_dir

    heroes_dir = heroes_wiki_dir(_repo_root())
    for hero_name, levels in blocks.items():
        hid = _hero_id_for(hero_name)
        if not hid:
            missing.append(hero_name)
            continue
        path = heroes_dir / f"{hid}.yaml"
        doc = _load_yaml(path)
        if not doc:
            missing.append(f"{hero_name} (no wiki file)")
            continue
        doc["levels"] = {
            "source": _source_block(_HERO_LEVELS_GID),
            "table": {int(lv): levels[lv] for lv in sorted(levels)},
        }
        _save_yaml(path, doc)
        updated += 1
    if missing:
        print(
            f"heroes without a wiki entry to merge into: {len(missing)} "
            f"({', '.join(missing[:5])}…)",
            file=sys.stderr,
        )
    return updated, len(missing)


# ---------------------------------------------------------------------------
# Gear stat tables
# ---------------------------------------------------------------------------


def _parse_gear_table(rows: list[list[str]]) -> dict[str, Any]:
    """Layout (5-tier × 4-stat grid):

    row 0: ``"", "Goggles & Boots (Marksman)", "", ...``
    row 1: ``"", "Grey", "", "", "", "Green", ...`` (tier name in first col)
    row 2: ``"Gear Lv.", "Power", "Attack", "Health", "Lethality", "Power",
            "Attack", ...`` (stat names repeat per tier)
    row 3+: ``<level>, <values...>``
    """
    if len(rows) < 4:
        msg = "gear table too short"
        raise ValueError(msg)
    title = (rows[0][1] if len(rows[0]) > 1 else "").strip()
    header = rows[2]
    # First 4 stats after column 0; same names repeat per tier.
    stat_names = [
        (cell or "").strip().lower().replace(" ", "_").replace(".", "")
        for cell in header[1:5]
    ]
    levels: dict[int, dict[str, dict[str, Any]]] = {}
    for raw in rows[3:]:
        if not raw or not (raw[0] or "").strip():
            continue
        try:
            lv = int((raw[0] or "").strip())
        except ValueError:
            continue
        per_tier: dict[str, dict[str, Any]] = {}
        for ti, tier in enumerate(_TIERS):
            base = 1 + ti * 4
            cells = raw[base : base + 4]
            if len(cells) < 4 or all(_coerce_number(c) is None for c in cells):
                continue
            entry: dict[str, Any] = {}
            for si, stat in enumerate(stat_names):
                v = _coerce_number(cells[si]) if si < len(cells) else None
                if v is not None:
                    entry[stat] = v
            if entry:
                per_tier[tier] = entry
        if per_tier:
            levels[lv] = per_tier
    return {
        "title": title,
        "stats": stat_names,
        "tiers": list(_TIERS),
        "levels": levels,
    }


def _sync_gear(client: httpx.Client) -> int:
    gear_dir = _repo_root() / "db" / "gear"
    written = 0
    for gid, (slot, klass) in _GEAR_GIDS.items():
        rows = _fetch_csv(client, gid)
        parsed = _parse_gear_table(rows)
        out_path = gear_dir / f"{slot}_{klass}.yaml"
        doc = {
            "id": f"{slot}_{klass}",
            "slot": slot,
            "troop_class": klass,
            "title": parsed["title"],
            "stats": parsed["stats"],
            "tiers": parsed["tiers"],
            "source": _source_block(gid),
            "levels": parsed["levels"],
        }
        _save_yaml(out_path, doc)
        written += 1
    return written


# ---------------------------------------------------------------------------
# Enhancement multi-table (gid=0)
# ---------------------------------------------------------------------------


def _parse_enhancement(rows: list[list[str]]) -> dict[str, Any]:
    """Five logical sub-tables share one sheet. We pick them out by column
    range so future column shifts surface as missing fields, not silently
    wrong data.
    """
    def cell(r: int, c: int) -> str:
        if 0 <= r < len(rows) and 0 <= c < len(rows[r]):
            return (rows[r][c] or "").strip()
        return ""

    # Section A: enhancement points required per (tier, level) — cols 0..5.
    points_required: dict[str, dict[int, int]] = {t: {} for t in _TIERS}
    for r in range(2, len(rows)):
        lv_text = cell(r, 0)
        if not lv_text:
            continue
        try:
            lv = int(lv_text)
        except ValueError:
            continue
        for ti, tier in enumerate(_TIERS):
            v = _coerce_number(cell(r, 1 + ti))
            if isinstance(v, (int, float)):
                points_required[tier][lv] = int(v)

    # Section B: points from sacrificing a tier — cols 8..10, sparse.
    sacrifice: dict[str, Any] = {}
    for r in range(2, 25):
        tier_name = cell(r, 8).lower()
        pts_text = cell(r, 10)
        if tier_name in _TIERS:
            sacrifice[tier_name] = _coerce_number(pts_text)

    # Section C: mastery level table — cols 7..10, rows ~25..44.
    mastery: dict[int, dict[str, Any]] = {}
    for r in range(25, len(rows)):
        lv = _coerce_number(cell(r, 7))
        if not isinstance(lv, int):
            continue
        mastery[lv] = {
            "bonus_pct": _coerce_number(cell(r, 8)),
            "essence_stones": _coerce_number(cell(r, 9)),
            "stat_multiplier": _coerce_number(cell(r, 10)),
        }

    # Section D: mythic max costs — cols 12..18 starting at the "From Lv."
    # header. Locate header row dynamically so column drift surfaces here.
    mythic_max: dict[int, dict[str, Any]] = {}
    header_row = None
    for r, _row in enumerate(rows):
        if (cell(r, 12) or "").lower().startswith("from lv"):
            header_row = r
            break
    if header_row is not None:
        # 8 columns: "From Lv., Pts Required, Grey Gear, Green Gear, Blue
        # Gear, Purple Gear, Gear Cog (10), Gear Cog (100)" — col 12..19.
        cols = [cell(header_row, c) for c in range(12, 20)]
        keys = [c.lower().replace(" ", "_").replace("(", "").replace(")", "") for c in cols]
        for r in range(header_row + 1, len(rows)):
            from_lv = _coerce_number(cell(r, 12))
            if not isinstance(from_lv, int):
                continue
            entry = {}
            for k, c in zip(keys[1:], range(13, 20), strict=False):
                v = _coerce_number(cell(r, c))
                if v is not None:
                    entry[k] = v
            if entry:
                mythic_max[from_lv] = entry

    # Section E: weapon widgets — cols 12..13, located after the mythic table.
    weapon_widgets: dict[Any, Any] = {}
    weapon_header_row = None
    for r, _row in enumerate(rows):
        if cell(r, 12).lower() == "weapon level":
            weapon_header_row = r
            break
    if weapon_header_row is not None:
        for r in range(weapon_header_row + 1, len(rows)):
            lv_text = cell(r, 12)
            if not lv_text:
                continue
            widgets = _coerce_number(cell(r, 13))
            try:
                lv = int(lv_text)
                weapon_widgets[lv] = widgets
            except ValueError:
                # "Total" label row at the end.
                weapon_widgets[lv_text.lower()] = widgets

    # Section F: total row at the bottom of the level cost table.
    totals: dict[str, Any] = {}
    for r in range(len(rows) - 1, -1, -1):
        if cell(r, 0).lower() == "total":
            for ti, tier in enumerate(_TIERS):
                v = _coerce_number(cell(r, 1 + ti))
                if v is not None:
                    totals[tier] = v
            break

    return {
        "source": _source_block(_ENHANCEMENT_GID),
        "points_required": points_required,
        "points_required_totals": totals,
        "points_per_tier_sacrifice": sacrifice,
        "mastery_levels": mastery,
        "mythic_max_costs": mythic_max,
        "weapon_widgets": weapon_widgets,
    }


def _sync_enhancement(client: httpx.Client) -> None:
    rows = _fetch_csv(client, _ENHANCEMENT_GID)
    doc = _parse_enhancement(rows)
    out_path = _repo_root() / "db" / "gear" / "enhancement.yaml"
    _save_yaml(out_path, doc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    with httpx.Client(headers=_UA, follow_redirects=True, timeout=30) as client:
        heroes_updated, heroes_missing = _sync_hero_levels(client)
        gear_written = _sync_gear(client)
        _sync_enhancement(client)
    print(
        f"updated {heroes_updated} hero files, "
        f"{gear_written} gear files, 1 enhancement file "
        f"(missing/skipped heroes: {heroes_missing})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
