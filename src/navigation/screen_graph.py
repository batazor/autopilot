"""Screen routing: BFS over FSM topology + tap action registry per directed edge.

Usage pattern
-------------
1. Detect current screen via ``navigation.detector.ScreenDetector``.
2. Call ``route_taps(current, target)`` to get the ordered list of tap sequences.
3. Execute each sequence with a short delay (Navigator uses 0.8 s per tap by default).

**Redis ``nav_error``:** the part before ``→`` is the navigator's last known source
screen (from detection), not the DSL scenario id. Directed taps are defined in
``navigation/edge_taps.yaml`` (this module only loads them).

Adding a new screen
--------------------
- Add ``src → dst: [region, ...]`` to ``navigation/edge_taps.yaml`` (regions must exist in area.json).
- Add detection landmarks / verification rules to ``modules/<id>/screen_verify.yaml``.
- Add coordinate constants to ``layout.screens``.
"""
from __future__ import annotations

import itertools
from collections import deque
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml

# Tap steps are usually region names from `area.json`. Dynamic resolvers may
# return structured tap specs when the final coordinate must be resolved from
# the current framebuffer (for example, template-matched event icons).
Tap = str | dict[str, Any]
VerifyRule = dict[str, Any]
VerifyConfig = dict[str, Any]
ScreenVerifyEntry = dict[str, Any]
DynamicEdgeSpec = dict[str, Any]
"""Per-edge spec for runtime-resolved taps.

YAML shape: ``{ resolver: <name>, target: <str> }``. ``resolver`` selects an
entry from :data:`EDGE_RESOLVERS`; ``target`` (and any other keys) are passed
through unchanged for the resolver to interpret. Used when the tap region
depends on per-instance state (e.g. which main_city event slot currently
hosts a given event)."""

EdgeResolver = Callable[
    [DynamicEdgeSpec, str, Any], Awaitable["list[Tap] | None"]
]
"""``async (spec, instance_id, redis_client) -> [Tap] | None``.

Returns the tap-region sequence resolved for the current instance, or ``None``
when the edge is currently unavailable (state stale / target not present).
A ``None`` return makes :func:`route_taps_async` fail the whole route — the
caller (Navigator) treats it as a routing failure and retries later."""

# ---------------------------------------------------------------------------
# Tap registry — loaded from navigation/edge_taps.yaml
# ---------------------------------------------------------------------------



def _hero_ids() -> list[str]:
    """Hero IDs from the heroes wiki index, lowercase + sorted.

    Same source the scenario template resolver uses, so per-hero edges /
    verify rules and per-hero scenario keys stay in lockstep. Best-effort:
    on parse failure / missing file we return ``[]`` and the navigation
    layer just won't get per-hero wiki edges (they're additive, not core).
    """
    from config.heroes import get_hero_registry

    return sorted({h.id for h in get_hero_registry().heroes})


def _load_edge_taps() -> tuple[
    dict[tuple[str, str], list[Tap]],
    dict[tuple[str, str], DynamicEdgeSpec],
]:
    """Parse root + module edge_taps.yaml into static + dynamic registries.

    Edge value forms:
    * ``str`` — single static tap region (legacy shorthand).
    * ``list[str]`` — static tap sequence.
    * ``dict`` — dynamic edge resolved at runtime via an :data:`EDGE_RESOLVERS`
      entry; the dict is opaque to the loader and passed through to the
      resolver as-is.
    """
    static: dict[tuple[str, str], list[Tap]] = {}
    dynamic: dict[tuple[str, str], DynamicEdgeSpec] = {}
    for path in _edge_taps_yaml_paths():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        edges_raw = raw.get("edges", {})
        if not isinstance(edges_raw, dict):
            continue
        edges = cast("dict[str, Any]", edges_raw)
        for src, dsts_raw in edges.items():
            if not isinstance(dsts_raw, dict):
                continue
            dsts = cast("dict[str, Any]", dsts_raw)
            for dst, taps in dsts.items():
                key = (str(src), str(dst))
                if isinstance(taps, list):
                    static[key] = [str(t) for t in taps]
                    dynamic.pop(key, None)
                elif isinstance(taps, str):
                    static[key] = [taps]
                    dynamic.pop(key, None)
                elif isinstance(taps, dict):
                    dynamic[key] = dict(taps)
                    static.pop(key, None)
    # Generated per-hero wiki edges. One pair per hero: tap the wiki icon
    # from the hero card to open the popup, tap back to return. YAML-listing
    # 62 × 2 = 124 entries by hand drifts from the heroes wiki index — keep
    # the source of truth there and synthesize the edges at load time.
    for hid in _hero_ids():
        src_card = f"page.heroes.{hid}"
        dst_wiki = f"heroes.{hid}.wiki"
        static.setdefault((src_card, dst_wiki), ["page.heroes.unit.wiki"])
        static.setdefault((dst_wiki, src_card), ["icon.page.back"])
    return static, dynamic


def _edge_taps_yaml_paths() -> list[Path]:
    root_path = Path(__file__).resolve().with_name("edge_taps.yaml")
    paths = [root_path] if root_path.is_file() else []

    from config.module_discovery import iter_module_dirs
    from config.paths import repo_root

    root = repo_root()
    for module_dir in iter_module_dirs(root):
        for rel in ("edge_taps.yaml", "routes/edge_taps.yaml"):
            path = module_dir / rel
            if path.is_file():
                paths.append(path)
    return paths


EDGE_TAPS, EDGE_DYNAMIC = _load_edge_taps()

# ---------------------------------------------------------------------------
# Adjacency graph derived from BOTH static and dynamic edges.
# BFS only needs topology; per-edge resolution happens at route-walk time.
# ---------------------------------------------------------------------------
_TAPS_GRAPH: dict[str, set[str]] = {}
for _src, _dst in EDGE_TAPS:
    _TAPS_GRAPH.setdefault(_src, set()).add(_dst)
for _src, _dst in EDGE_DYNAMIC:
    _TAPS_GRAPH.setdefault(_src, set()).add(_dst)


# ---------------------------------------------------------------------------
# Resolver registry — populated by call sites that import this module.
# Decoupled from the resolver implementations themselves so screen_graph stays
# a pure topology / routing module (no Redis import at module load).
# ---------------------------------------------------------------------------
EDGE_RESOLVERS: dict[str, EdgeResolver] = {}


def register_edge_resolver(name: str, fn: EdgeResolver) -> None:
    """Idempotent registration. Late binding lets the resolver live anywhere."""
    EDGE_RESOLVERS[str(name).strip()] = fn


async def _resolve_dynamic_edge(
    src: str,
    dst: str,
    *,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    spec = EDGE_DYNAMIC.get((src, dst))
    if spec is None:
        return None
    name = str(spec.get("resolver") or "").strip()
    fn = EDGE_RESOLVERS.get(name)
    if fn is None:
        return None
    return await fn(spec, instance_id, redis_client)


# ---------------------------------------------------------------------------
# Destination verification config
# ---------------------------------------------------------------------------

def _screen_verify_yaml_path() -> Path:
    return Path(__file__).resolve().with_name("screen_verify.yaml")


def _module_screen_verify_yaml_paths() -> list[Path]:
    from config.module_discovery import iter_module_dirs
    from config.paths import repo_root

    root = repo_root()
    paths: list[Path] = []
    for module_dir in iter_module_dirs(root):
        for rel in ("screen_verify.yaml", "routes/screen_verify.yaml"):
            path = module_dir / rel
            if path.is_file():
                paths.append(path)
    return paths


def _screen_verify_yaml_paths() -> list[Path]:
    path = _screen_verify_yaml_path()
    paths = [path]
    # Unit tests monkeypatch the root YAML path to a temp file; in that mode,
    # keep the fixture isolated and do not merge production module manifests.
    if path.resolve() == Path(__file__).resolve().with_name("screen_verify.yaml"):
        paths.extend(_module_screen_verify_yaml_paths())
    return paths


def _area_json_path() -> Path:
    from config.paths import repo_root

    return repo_root() / "area.json"


def _area_yaml_paths() -> list[Path]:
    from config.module_discovery import iter_module_area_manifests
    from config.paths import repo_root

    root = repo_root()
    area_path = _area_json_path()
    paths = [area_path]
    if (
        area_path.is_file()
        and area_path.resolve() == (root / "area.json").resolve()
    ):
        paths.extend(iter_module_area_manifests(root))
    return paths


def _normalize_verify_rule(raw: object) -> VerifyRule | None:
    if not isinstance(raw, dict):
        return None
    raw_d = cast("dict[str, Any]", raw)
    rule: VerifyRule = {}
    for key in ("match", "ocr", "tab_active"):
        value = raw_d.get(key)
        if value is not None and str(value).strip():
            rule[key] = str(value).strip()
    # ``from_screen`` is an image-less verify: passes when the previous entry in
    # the Navigator's rolling screen_history matches the given screen name. Lets
    # destinations without their own OCR/match landmark (e.g. the per-hero wiki
    # popup) be verified by the hop we took to reach them. List form accepts
    # multiple acceptable predecessors.
    fs_raw = raw_d.get("from_screen")
    fs_values: list[str] = []
    if isinstance(fs_raw, list):
        fs_values = [str(x).strip() for x in fs_raw if str(x).strip()]
    elif fs_raw is not None and str(fs_raw).strip():
        fs_values = [str(fs_raw).strip()]
    if fs_values:
        rule["from_screen"] = fs_values
    if not rule:
        return None
    if "contains" in raw_d:
        contains = raw_d.get("contains")
        if isinstance(contains, list):
            rule["contains"] = [str(x).strip() for x in contains if str(x).strip()]
        elif contains is not None and str(contains).strip():
            rule["contains"] = str(contains).strip()
    for key in ("threshold", "confidence", "min_match_saturation"):
        if key in raw_d:
            rule[key] = raw_d[key]
    return rule


def _file_fingerprint(path: Path) -> tuple[str, int, int]:
    try:
        st = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


def _combined_config_fingerprint() -> tuple[
    tuple[tuple[str, int, int], ...],
    tuple[tuple[str, int, int], ...],
]:
    return (
        tuple(_file_fingerprint(path) for path in _screen_verify_yaml_paths()),
        tuple(_file_fingerprint(path) for path in _area_yaml_paths()),
    )


def _area_screen_region_landmarks(root: Path) -> dict[str, list[VerifyRule]]:
    """Build screen landmark rules from ``area.json`` screen entries.

    ``screen_region`` is an optional entry-level pointer to the region that
    proves the current reference image represents ``screen_id``. It keeps common
    one-region screen detection close to the labeled regions instead of forcing
    every screen into ``screen_verify.yaml``.
    """
    from layout.area_manifest import load_area_doc

    raw = load_area_doc(root)
    out: dict[str, list[VerifyRule]] = {}
    for entry in raw.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        screen_id = str(entry.get("screen_id") or "").strip()
        region_name = str(entry.get("screen_region") or "").strip()
        if not screen_id or not region_name:
            continue
        rule: VerifyRule = {"match": region_name}
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict) or str(reg.get("name") or "").strip() != region_name:
                continue
            if "threshold" in reg:
                rule["threshold"] = reg["threshold"]
            if "min_match_saturation" in reg:
                rule["min_match_saturation"] = reg["min_match_saturation"]
            break
        out.setdefault(screen_id, []).append(rule)
    return out


@lru_cache(maxsize=8)
def _load_screen_verify_config_cached(
    fp: tuple[tuple[tuple[str, int, int], ...], tuple[tuple[str, int, int], ...]]
    | tuple[tuple[tuple[str, int, int], ...], tuple[str, int, int]]
    | tuple[tuple[str, int, int], tuple[str, int, int]]
    | tuple[str, int, int]
    | None = None,
) -> VerifyConfig:
    """Load route destination verification rules from root + module YAML.

    Cache key includes file mtime/size so edits are picked up automatically.
    """
    if fp is None:
        return _load_screen_verify_config_cached(_combined_config_fingerprint())
    if (
        fp
        and isinstance(fp[0], tuple)
        and fp[0]
        and isinstance(fp[0][0], tuple)
        and isinstance(fp[1], tuple)
        and (not fp[1] or isinstance(fp[1][0], tuple))
    ):
        yaml_fps_, area_fps_ = cast(
            "tuple[tuple[tuple[str, int, int], ...], tuple[tuple[str, int, int], ...]]", fp
        )
        paths = [Path(yaml_fp[0]) for yaml_fp in yaml_fps_]
        from config.paths import repo_root

        root = Path(area_fps_[0][0]).parent if area_fps_ else repo_root()
    elif fp and isinstance(fp[0], tuple) and fp[0] and isinstance(fp[0][0], tuple):
        yaml_fps_, area_fp_ = cast(
            "tuple[tuple[tuple[str, int, int], ...], tuple[str, int, int]]", fp
        )
        paths = [Path(yaml_fp[0]) for yaml_fp in yaml_fps_]
        root = Path(area_fp_[0]).parent
    elif fp and isinstance(fp[0], tuple):
        yaml_fp_, area_fp_ = cast(
            "tuple[tuple[str, int, int], tuple[str, int, int]]", fp
        )
        paths = [Path(yaml_fp_[0])]
        root = Path(area_fp_[0]).parent
    else:
        fp_single = cast("tuple[str, int, int]", fp)
        paths = [Path(fp_single[0])]
        from config.paths import repo_root

        root = repo_root()

    docs: list[dict[str, Any]] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        raw = raw or {}
        docs.append(raw if isinstance(raw, dict) else {})

    retry = next((doc.get("retry") for doc in docs if isinstance(doc.get("retry"), dict)), {})
    out_screens: dict[str, ScreenVerifyEntry] = {}
    for raw in docs:
        screens = raw.get("screens")
        if not isinstance(screens, dict):
            continue
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

    for screen, landmarks in _area_screen_region_landmarks(root).items():
        entry = out_screens.setdefault(screen, {"rules": [], "landmarks": [], "priority": 100})
        existing = entry.setdefault("landmarks", [])
        if not isinstance(existing, list):
            existing = []
            entry["landmarks"] = existing
        existing_keys = {
            str(rule.get("match") or rule.get("ocr") or "").strip()
            for rule in existing
            if isinstance(rule, dict)
        }
        for rule in landmarks:
            key = str(rule.get("match") or rule.get("ocr") or "").strip()
            if key and key not in existing_keys:
                existing.append(rule)
                existing_keys.add(key)

    # Synthesize per-hero wiki verify rules from the heroes wiki index so the
    # YAML doesn't have to enumerate 62 entries. A YAML override (same screen
    # key) wins over the synthesized default.
    for hid in _hero_ids():
        wiki_screen = f"heroes.{hid}.wiki"
        if wiki_screen in out_screens:
            continue
        out_screens[wiki_screen] = {
            "rules": [{"from_screen": [f"page.heroes.{hid}"]}],
            "landmarks": [],
            "priority": 100,
        }

    return {
        "retry": retry if isinstance(retry, dict) else {},
        "screens": out_screens,
    }


def load_screen_verify_config(
    fp: tuple[tuple[tuple[str, int, int], ...], tuple[tuple[str, int, int], ...]]
    | tuple[tuple[tuple[str, int, int], ...], tuple[str, int, int]]
    | tuple[tuple[str, int, int], tuple[str, int, int]]
    | tuple[str, int, int]
    | None = None,
) -> VerifyConfig:
    """Load screen verification config, keyed by current file fingerprints by default."""

    return _load_screen_verify_config_cached(
        _combined_config_fingerprint() if fp is None else fp
    )


load_screen_verify_config.cache_clear = _load_screen_verify_config_cached.cache_clear  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def screen_verify_rules(screen: str) -> list[VerifyRule]:
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return []
    entry = screens.get(screen)
    if isinstance(entry, list):
        return cast("list[VerifyRule]", list(entry))
    if not isinstance(entry, dict):
        return []
    rules = entry.get("rules")
    return cast("list[VerifyRule]", list(rules)) if isinstance(rules, list) else []


def screen_landmark_rules(screen: str) -> list[VerifyRule]:
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return []
    entry = screens.get(screen)
    if isinstance(entry, list):
        return cast("list[VerifyRule]", list(entry))
    if not isinstance(entry, dict):
        return []
    rules = entry.get("landmarks")
    return cast("list[VerifyRule]", list(rules)) if isinstance(rules, list) else []


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
    raw_d = cast("dict[str, Any]", raw)
    try:
        attempts = int(raw_d.get("attempts", default_attempts))
    except (TypeError, ValueError):
        attempts = default_attempts
    try:
        interval = float(raw_d.get("interval_seconds", default_interval))
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
            new_path = [*path, nb]
            if nb == dst:
                return new_path
            visited.add(nb)
            queue.append(new_path)
    return None


def route_taps(src: str, dst: str) -> list[list[Tap]] | None:
    """BFS path resolved to tap sequences using **static edges only**.

    Returns ``None`` when the route would require traversing a dynamic edge
    — those can't be resolved without an instance context. Async callers
    should use :func:`route_taps_async` instead. Kept synchronous for tests
    and tooling that only inspect static topology.
    """
    path = bfs_route(src, dst)
    if path is None:
        return None
    result: list[list[Tap]] = []
    for a, b in itertools.pairwise(path):
        taps = EDGE_TAPS.get((a, b))
        if taps is None:
            return None
        result.append(list(taps))
    return result


def route_hops(src: str, dst: str) -> list[tuple[str, list[Tap]]] | None:
    """Static-only variant of :func:`route_hops_async` — see that for full semantics."""
    path = bfs_route(src, dst)
    if path is None:
        return None
    result: list[tuple[str, list[Tap]]] = []
    for a, b in itertools.pairwise(path):
        taps = EDGE_TAPS.get((a, b))
        if taps is None:
            return None
        result.append((b, list(taps)))
    return result


async def route_taps_async(
    src: str,
    dst: str,
    *,
    instance_id: str,
    redis_client: Any,
) -> list[list[Tap]] | None:
    """Like :func:`route_taps` but resolves dynamic edges via :data:`EDGE_RESOLVERS`.

    If any hop on the BFS path is a dynamic edge whose resolver returns
    ``None`` (target not in current per-instance state), the whole route is
    treated as unavailable and ``None`` is returned. The caller can then
    fall back / retry after the state refreshes.
    """
    path = bfs_route(src, dst)
    if path is None:
        return None
    result: list[list[Tap]] = []
    for a, b in itertools.pairwise(path):
        taps = EDGE_TAPS.get((a, b))
        if taps is None:
            taps = await _resolve_dynamic_edge(
                a, b, instance_id=instance_id, redis_client=redis_client
            )
        if taps is None:
            return None
        result.append(list(taps))
    return result


async def route_hops_async(
    src: str,
    dst: str,
    *,
    instance_id: str,
    redis_client: Any,
) -> list[tuple[str, list[Tap]]] | None:
    """Per-hop variant of :func:`route_taps_async`."""
    path = bfs_route(src, dst)
    if path is None:
        return None
    result: list[tuple[str, list[Tap]]] = []
    for a, b in itertools.pairwise(path):
        taps = EDGE_TAPS.get((a, b))
        if taps is None:
            taps = await _resolve_dynamic_edge(
                a, b, instance_id=instance_id, redis_client=redis_client
            )
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
