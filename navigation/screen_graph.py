"""Screen routing: BFS over FSM topology + tap action registry per directed edge.

Usage pattern
-------------
1. Detect current screen via ``navigation.detector.ScreenDetector``.
2. Call ``route_taps(current, target)`` to get the ordered list of tap sequences.
3. Execute each sequence with a short delay (Navigator uses 0.8 s per tap by default).

Adding a new screen
-------------------
- Add an edge to ``navigation.fsm_screen_map`` (topology only).
- Add ``src → dst: [region, ...]`` entries to ``navigation/edge_taps.yaml`` (regions must exist in area.json).
- Add detection landmarks to ``navigation.detector._SCREEN_LANDMARKS``.
- Add coordinate constants to ``layout.screens``.
"""

from __future__ import annotations

from collections import deque
from functools import lru_cache
from pathlib import Path

import yaml

# Tap steps are region names from `area.json` (no hardcoded coordinates).
Tap = str
VerifyRule = dict[str, object]
VerifyConfig = dict[str, object]
ScreenVerifyEntry = dict[str, object]
TextSwitchRule = dict[str, object]

# ---------------------------------------------------------------------------
# Tap registry — loaded from navigation/edge_taps.yaml
# ---------------------------------------------------------------------------


def _load_edge_taps() -> dict[tuple[str, str], list[Tap]]:
    path = Path(__file__).resolve().with_name("edge_taps.yaml")
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    edges = raw.get("edges", {})
    result: dict[tuple[str, str], list[Tap]] = {}
    if not isinstance(edges, dict):
        return result
    for src, dsts in edges.items():
        if not isinstance(dsts, dict):
            continue
        for dst, taps in dsts.items():
            if isinstance(taps, list):
                result[(str(src), str(dst))] = [str(t) for t in taps]
            elif isinstance(taps, str):
                result[(str(src), str(dst))] = [taps]
    return result


EDGE_TAPS: dict[tuple[str, str], list[Tap]] = _load_edge_taps()

# ---------------------------------------------------------------------------
# Adjacency graph derived from EDGE_TAPS
# ---------------------------------------------------------------------------
_TAPS_GRAPH: dict[str, set[str]] = {}
for _src, _dst in EDGE_TAPS:
    _TAPS_GRAPH.setdefault(_src, set()).add(_dst)


# ---------------------------------------------------------------------------
# Destination verification config
# ---------------------------------------------------------------------------

def _screen_verify_yaml_path() -> Path:
    return Path(__file__).resolve().with_name("screen_verify.yaml")


def _normalize_verify_rule(raw: object) -> VerifyRule | None:
    if not isinstance(raw, dict):
        return None
    rule: VerifyRule = {}
    for key in ("match", "ocr"):
        value = raw.get(key)
        if value is not None and str(value).strip():
            rule[key] = str(value).strip()
    if not rule:
        return None
    if "contains" in raw:
        contains = raw.get("contains")
        if isinstance(contains, list):
            rule["contains"] = [str(x).strip() for x in contains if str(x).strip()]
        elif contains is not None and str(contains).strip():
            rule["contains"] = str(contains).strip()
    for key in ("threshold", "confidence", "min_match_saturation"):
        if key in raw:
            rule[key] = raw[key]
    return rule


def _normalize_text_switch_rule(raw: object) -> TextSwitchRule | None:
    if not isinstance(raw, dict):
        return None
    region = str(raw.get("ocr") or "").strip()
    cases_raw = raw.get("cases")
    if not region or not isinstance(cases_raw, dict):
        return None
    cases: dict[str, list[str]] = {}
    for screen, candidates_raw in cases_raw.items():
        screen_s = str(screen).strip()
        if not screen_s:
            continue
        if isinstance(candidates_raw, str):
            candidates = [candidates_raw]
        elif isinstance(candidates_raw, list):
            candidates = [str(x).strip() for x in candidates_raw if str(x).strip()]
        else:
            candidates = []
        if candidates:
            cases[screen_s] = candidates
    if not cases:
        return None
    rule: TextSwitchRule = {"ocr": region, "cases": cases}
    if "threshold" in raw:
        rule["threshold"] = raw["threshold"]
    if "confidence" in raw:
        rule["confidence"] = raw["confidence"]
    return rule


def _file_fingerprint(path: Path) -> tuple[str, int, int]:
    try:
        st = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


@lru_cache(maxsize=8)
def load_screen_verify_config(fp: tuple[str, int, int] | None = None) -> VerifyConfig:
    """Load route destination verification rules from ``navigation/screen_verify.yaml``.

    Cache key includes file mtime/size so edits are picked up automatically.
    """
    path = _screen_verify_yaml_path()
    if not path.is_file():
        return {"retry": {}, "screens": {}}
    if fp is None:
        return load_screen_verify_config(_file_fingerprint(path))
    path = Path(fp[0])

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {"retry": {}, "screens": {}}

    retry = raw.get("retry")
    switch_raw = raw.get("text_switch")
    text_switch = []
    if isinstance(switch_raw, list):
        text_switch = [
            rule
            for rule in (_normalize_text_switch_rule(item) for item in switch_raw)
            if rule is not None
        ]
    screens = raw.get("screens")
    out_screens: dict[str, ScreenVerifyEntry] = {}
    if isinstance(screens, dict):
        for screen, entry_raw in screens.items():
            if isinstance(entry_raw, list):
                rules_raw = entry_raw
                retry_raw = {}
                landmarks_raw = []
            elif isinstance(entry_raw, dict):
                rules_raw = entry_raw.get("rules")
                retry_raw = entry_raw.get("retry")
                landmarks_raw = entry_raw.get("landmarks")
            else:
                continue
            if not isinstance(rules_raw, list):
                rules_raw = []
            if not isinstance(landmarks_raw, list):
                landmarks_raw = []
            rules = [
                rule
                for rule in (_normalize_verify_rule(item) for item in rules_raw)
                if rule is not None
            ]
            landmarks = [
                rule
                for rule in (_normalize_verify_rule(item) for item in landmarks_raw)
                if rule is not None
            ]
            entry: ScreenVerifyEntry = {"rules": rules, "landmarks": landmarks}
            if isinstance(retry_raw, dict):
                entry["retry"] = retry_raw
            priority_raw = entry_raw.get("priority") if isinstance(entry_raw, dict) else None
            try:
                entry["priority"] = int(priority_raw) if priority_raw is not None else 100
            except (TypeError, ValueError):
                entry["priority"] = 100
            if rules or landmarks or "retry" in entry:
                out_screens[str(screen).strip()] = entry

    return {
        "retry": retry if isinstance(retry, dict) else {},
        "text_switch": text_switch,
        "screens": out_screens,
    }


def screen_verify_rules(screen: str) -> list[VerifyRule]:
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return []
    entry = screens.get(screen)
    if isinstance(entry, list):
        return list(entry)
    if not isinstance(entry, dict):
        return []
    rules = entry.get("rules")
    return list(rules) if isinstance(rules, list) else []


def screen_landmark_rules(screen: str) -> list[VerifyRule]:
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return []
    entry = screens.get(screen)
    if isinstance(entry, list):
        return list(entry)
    if not isinstance(entry, dict):
        return []
    rules = entry.get("landmarks")
    return list(rules) if isinstance(rules, list) else []


def screen_text_switch_rules() -> list[TextSwitchRule]:
    rules = load_screen_verify_config().get("text_switch")
    return list(rules) if isinstance(rules, list) else []


def screen_verify_screen_names() -> list[str]:
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return []
    names = [str(screen).strip() for screen in screens if str(screen).strip()]
    return sorted(names, key=lambda s: int((screens.get(s) or {}).get("priority") or 100))


def _parse_retry(
    raw: object,
    *,
    default_attempts: int,
    default_interval: float,
) -> tuple[int, float]:
    if not isinstance(raw, dict):
        return default_attempts, default_interval
    try:
        attempts = int(raw.get("attempts", default_attempts))
    except (TypeError, ValueError):
        attempts = default_attempts
    try:
        interval = float(raw.get("interval_seconds", default_interval))
    except (TypeError, ValueError):
        interval = default_interval
    return max(1, attempts), max(0.0, interval)


def screen_verify_retry(screen: str | None = None) -> tuple[int, float]:
    cfg = load_screen_verify_config()
    attempts, interval = _parse_retry(
        cfg.get("retry"),
        default_attempts=6,
        default_interval=0.8,
    )
    if not screen:
        return attempts, interval
    screens = cfg.get("screens")
    if not isinstance(screens, dict):
        return attempts, interval
    entry = screens.get(screen)
    if not isinstance(entry, dict):
        return attempts, interval
    return _parse_retry(
        entry.get("retry"),
        default_attempts=attempts,
        default_interval=interval,
    )


# ---------------------------------------------------------------------------
# BFS path finder
# ---------------------------------------------------------------------------

def bfs_route(src: str, dst: str) -> list[str] | None:
    """Shortest path [src, …, dst] over the tap-action graph; None if unreachable.

    Uses sorted neighbor iteration for deterministic results when multiple
    shortest paths of equal length exist.
    """
    if src == dst:
        return [src]
    visited: set[str] = {src}
    queue: deque[list[str]] = deque([[src]])
    while queue:
        path = queue.popleft()
        for nb in sorted(_TAPS_GRAPH.get(path[-1], set())):
            if nb in visited:
                continue
            new_path = path + [nb]
            if nb == dst:
                return new_path
            visited.add(nb)
            queue.append(new_path)
    return None


def route_taps(src: str, dst: str) -> list[list[Tap]] | None:
    """BFS path from *src* to *dst* resolved to per-hop tap sequences.

    Returns ``None`` when no path exists in the tap-action graph
    (either the edge is unknown or tap coordinates are not yet registered).
    The caller (Navigator) falls back to routing via ``main_city`` in that case.
    """
    path = bfs_route(src, dst)
    if path is None:
        return None
    result: list[list[Tap]] = []
    for a, b in zip(path, path[1:], strict=False):
        taps = EDGE_TAPS.get((a, b))
        if taps is None:
            return None
        result.append(list(taps))
    return result


def route_hops(src: str, dst: str) -> list[tuple[str, list[Tap]]] | None:
    """BFS path resolved to ``(destination_screen, tap_sequence)`` per hop."""
    path = bfs_route(src, dst)
    if path is None:
        return None
    result: list[tuple[str, list[Tap]]] = []
    for a, b in zip(path, path[1:], strict=False):
        taps = EDGE_TAPS.get((a, b))
        if taps is None:
            return None
        result.append((b, list(taps)))
    return result


def reachable_screens(src: str) -> set[str]:
    """All screens reachable from *src* via the tap-action graph (excluding *src*)."""
    visited: set[str] = {src}
    queue: deque[str] = deque([src])
    while queue:
        node = queue.popleft()
        for nb in _TAPS_GRAPH.get(node, set()):
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)
    visited.discard(src)
    return visited
