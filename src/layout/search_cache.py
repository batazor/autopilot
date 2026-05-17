"""Persistent top-left cache for full-frame template searches."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from config.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

MAX_POSITIONS = 20


def default_search_cache_dir() -> Path:
    return repo_root() / ".cache" / "wos" / "search_positions"


def _cache() -> Any:
    from diskcache import Cache

    root = default_search_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    return Cache(str(root))


def read_positions(key: str) -> list[dict[str, Any]]:
    if not key:
        return []
    try:
        with _cache() as c:
            raw = c.get(key, default=[])
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            x = int(item.get("x"))
            y = int(item.get("y"))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "x": x,
                "y": y,
                "score": float(item.get("score") or 0.0),
                "last_seen": float(item.get("last_seen") or 0.0),
                "hits": int(item.get("hits") or 0),
            }
        )
    out.sort(key=lambda it: (float(it["score"]), float(it["last_seen"]), int(it["hits"])), reverse=True)
    return out[:MAX_POSITIONS]


def record_position(key: str, *, x: int, y: int, score: float) -> None:
    if not key:
        return
    now = time.time()
    rows = read_positions(key)
    merged: list[dict[str, Any]] = []
    updated = False
    for row in rows:
        if abs(int(row["x"]) - int(x)) <= 2 and abs(int(row["y"]) - int(y)) <= 2:
            merged.append(
                {
                    "x": int(x),
                    "y": int(y),
                    "score": max(float(row.get("score") or 0.0), float(score)),
                    "last_seen": now,
                    "hits": int(row.get("hits") or 0) + 1,
                }
            )
            updated = True
        else:
            merged.append(row)
    if not updated:
        merged.append({"x": int(x), "y": int(y), "score": float(score), "last_seen": now, "hits": 1})
    merged.sort(key=lambda it: (float(it["score"]), float(it["last_seen"]), int(it["hits"])), reverse=True)
    try:
        with _cache() as c:
            c.set(key, merged[:MAX_POSITIONS])
    except Exception:
        return

