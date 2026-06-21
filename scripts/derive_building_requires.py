"""Derive an explicit dependency graph from free-text building prerequisites.

The per-level ``prerequisites`` in ``games/<game>/db/buildings/*.yaml`` are
free text (e.g. "Embassy Lv. 8 Infirmary Lv. 1"). This script resolves them
into an explicit top-level ``requires:`` list of ``{building, level}`` unlock
gates and writes it back into each YAML — so the dependency graph is part of the
data (reviewable, hand-editable) instead of being re-parsed at request time.

Idempotent: re-running replaces an existing ``requires:`` block. Run once after
the wiki data changes::

    uv run python scripts/derive_building_requires.py

Insertion is line-based (after the ``category:`` line) to keep the diff to just
the new block — the rest of each generated file is left byte-for-byte intact.
"""
from __future__ import annotations

from config.building_deps import name_index, refs_in_text
from config.buildings import buildings_db_dir, get_building_registry


def _unlock_requires(
    building_id: str, req_by_level: dict, names: list[tuple[str, str]]
) -> list[tuple[str, int]]:
    gates: dict[str, int] = {}
    for level in sorted(req_by_level):
        text = str(req_by_level[level].get("prerequisites") or "")
        if not text:
            continue
        for bid, lvl in refs_in_text(text, names).items():
            if bid != building_id and bid not in gates:
                gates[bid] = lvl
    return list(gates.items())


def _render_block(requires: list[tuple[str, int]]) -> list[str]:
    if not requires:
        return ["requires: []\n"]
    lines = ["requires:\n"]
    for bid, level in requires:
        lines.append(f"- building: {bid}\n")
        lines.append(f"  level: {level}\n")
    return lines


def _strip_existing(lines: list[str]) -> list[str]:
    """Remove a top-level ``requires:`` block (and its indented body) if present."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("requires:"):
            i += 1
            while i < len(lines) and (
                lines[i].startswith((" ", "\t", "-")) or not lines[i].strip()
            ):
                # stop at the next top-level key
                if lines[i].strip() and not lines[i].startswith((" ", "\t", "-")):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


def main() -> None:
    registry = get_building_registry()
    names = name_index(registry.buildings)
    by_id = {b.id: b for b in registry.buildings}
    db_dir = buildings_db_dir()

    changed = 0
    for b in registry.buildings:
        path = db_dir / f"{b.id}.yaml"
        if not path.exists():
            continue
        requires = _unlock_requires(b.id, by_id[b.id].requirements_by_level, names)
        lines = _strip_existing(path.read_text(encoding="utf-8").splitlines(keepends=True))

        # Insert the block right after the top-level ``category:`` line.
        cat_idx = next(
            (j for j, ln in enumerate(lines) if ln.startswith("category:")), None
        )
        if cat_idx is None:
            print(f"  ! {b.id}: no category line, skipped")
            continue
        new_lines = lines[: cat_idx + 1] + _render_block(requires) + lines[cat_idx + 1 :]
        path.write_text("".join(new_lines), encoding="utf-8")
        changed += 1
        names_str = ", ".join(f"{bid}@{lvl}" for bid, lvl in requires) or "(none)"
        print(f"  {b.id}: {names_str}")

    print(f"\nWrote requires to {changed} building file(s).")


if __name__ == "__main__":
    main()
