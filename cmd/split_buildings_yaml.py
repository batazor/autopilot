from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main(argv: list[str]) -> int:
    repo = _repo_root()
    src = repo / "db" / "buildings.yaml"
    out_dir = repo / "db" / "buildings"
    out_dir.mkdir(parents=True, exist_ok=True)

    db = _load_yaml(src)
    buildings = db.get("buildings")
    if not isinstance(buildings, list):
        print("db/buildings.yaml missing buildings list", file=sys.stderr)
        return 2

    index: dict[str, Any] = {
        "source": db.get("source") or {},
        "buildings": [],
    }

    written = 0
    for b in buildings:
        if not isinstance(b, dict):
            continue
        bid = str(b.get("id") or "").strip()
        name = str(b.get("name") or "").strip()
        if not bid or not name:
            continue

        entry = dict(b)
        path = out_dir / f"{bid}.yaml"
        _dump_yaml(path, entry)
        written += 1

        index["buildings"].append(
            {
                "id": bid,
                "name": name,
                "category": str(b.get("category") or "unknown"),
                "wiki_url": str(b.get("wiki_url") or ""),
                "file": f"{bid}.yaml",
            }
        )

    _dump_yaml(out_dir / "index.yaml", index)
    print(f"written {written} building files to db/buildings/ and index.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

