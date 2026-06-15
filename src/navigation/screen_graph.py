"""Screen routing: BFS over FSM topology + tap action registry per directed edge.

Usage pattern
-------------
1. Detect current screen via ``navigation.detector.ScreenDetector``.
2. Call ``route_taps(current, target)`` to get the ordered list of tap sequences.
3. Execute each sequence with a short delay (Navigator uses 0.8 s per tap by default).

**Redis ``nav_error``:** the part before ``→`` is the navigator's last known source
screen (from detection), not the DSL scenario id. Directed taps are defined in
per-module ``modules/<id>/routes/edge_taps.yaml`` (this module only loads them).

Adding a new screen
--------------------
- Add ``src → dst: [region, ...]`` to ``modules/<id>/routes/edge_taps.yaml`` (regions must exist in area.json).
- Add ``rules`` to ``modules/<id>/routes/screen_verify.yaml`` (detection + nav verify). Optional
  ``landmarks`` only when detection must differ (e.g. shop sub-tabs).
- Add coordinate constants to ``layout.screens``.
"""
from __future__ import annotations

import heapq
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
ScreenFamilyEntry = dict[str, Any]
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
# Tap registry — loaded from per-module routes/edge_taps.yaml
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


def _load_edge_taps(
    game: str | None = None,
) -> tuple[
    dict[tuple[str, str], list[Tap]],
    dict[tuple[str, str], DynamicEdgeSpec],
]:
    """Parse module edge_taps.yaml into static + dynamic registries for ``game``.

    Edge value forms:
    * ``str`` — single static tap region (legacy shorthand).
    * ``list[str | dict]`` — static action sequence.
    * ``dict`` — dynamic edge resolved at runtime via an :data:`EDGE_RESOLVERS`
      entry; the dict is opaque to the loader and passed through to the
      resolver as-is.

    Phase 4: ``game`` defaults to :func:`services.get_active_game` so worker
    processes pick up only their bound game's edges. API / scheduler call
    sites that need cross-game routing pass ``game`` explicitly.
    """
    static: dict[tuple[str, str], list[Tap]] = {}
    dynamic: dict[tuple[str, str], DynamicEdgeSpec] = {}
    for path in _edge_taps_yaml_paths(game=game):
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
                    static[key] = [
                        dict(t) if isinstance(t, dict) else str(t) for t in taps
                    ]
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


def _edge_taps_yaml_paths(*, game: str | None = None) -> list[Path]:
    """Every per-module ``edge_taps.yaml`` / ``routes/edge_taps.yaml`` for ``game``."""
    from config.module_discovery import iter_module_dirs
    from config.paths import repo_root

    paths: list[Path] = []
    root = repo_root()
    for module_dir in iter_module_dirs(root, game=game):
        for rel in ("edge_taps.yaml", "routes/edge_taps.yaml"):
            path = module_dir / rel
            if path.is_file():
                paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Per-game graph cache. Workers bind ``services.bind_active_game`` before
# they import this module, so the first access populates the cache for the
# bound game; API / scheduler may add more games on demand via
# :func:`graph_for_game`.
# ---------------------------------------------------------------------------
_GAME_GRAPHS: dict[
    str,
    tuple[
        dict[tuple[str, str], list[Tap]],
        dict[tuple[str, str], DynamicEdgeSpec],
        dict[str, set[str]],
    ],
] = {}


def _resolve_active_game() -> str:
    try:
        from services import get_active_game

        return get_active_game()
    except Exception:
        from config.games import default_game

        return default_game()


def graph_for_game(
    game: str | None = None,
) -> tuple[
    dict[tuple[str, str], list[Tap]],
    dict[tuple[str, str], DynamicEdgeSpec],
    dict[str, set[str]],
]:
    """``(EDGE_TAPS, EDGE_DYNAMIC, _TAPS_GRAPH)`` for ``game`` (cached per process)."""
    g = (game or _resolve_active_game()).strip() or _resolve_active_game()
    cached = _GAME_GRAPHS.get(g)
    if cached is not None:
        return cached
    static, dynamic = _load_edge_taps(game=g)
    graph: dict[str, set[str]] = {}
    for s, d in static:
        graph.setdefault(s, set()).add(d)
    for s, d in dynamic:
        graph.setdefault(s, set()).add(d)
    _GAME_GRAPHS[g] = (static, dynamic, graph)
    return _GAME_GRAPHS[g]


def invalidate_edge_taps_cache() -> None:
    """Drop the per-game graph cache (reload button / tests)."""
    _GAME_GRAPHS.clear()


# Module-level attribute access via ``__getattr__`` so legacy callers that do
# ``from navigation.screen_graph import EDGE_TAPS`` keep working without
# threading the game through. They get a snapshot of the active game's edges
# at the moment of import — fine for worker processes (one game per process)
# and for API processes that read these at module load before serving multi-
# game requests through the explicit accessors.
def __getattr__(name: str) -> Any:
    if name in {"EDGE_TAPS", "EDGE_DYNAMIC", "_TAPS_GRAPH"}:
        static, dynamic, graph = graph_for_game()
        return {"EDGE_TAPS": static, "EDGE_DYNAMIC": dynamic, "_TAPS_GRAPH": graph}[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


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
    game: str | None = None,
) -> list[Tap] | None:
    _static, dynamic, _graph = graph_for_game(game)
    spec = dynamic.get((src, dst))
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

def _screen_verify_yaml_paths() -> list[Path]:
    """Every per-module ``screen_verify.yaml`` / ``routes/screen_verify.yaml``.

    Unit tests monkeypatch this entire function with ``new=lambda: [cfg]`` to
    inject a single temp-file fixture in isolation.
    """
    from config.paths import repo_root

    return list(
        _screen_verify_yaml_paths_cached(
            str(repo_root().resolve()), _resolve_active_game()
        )
    )


@lru_cache(maxsize=8)
def _screen_verify_yaml_paths_cached(root_s: str, game: str) -> tuple[Path, ...]:
    from config.module_discovery import iter_module_dirs

    paths: list[Path] = []
    root = Path(root_s)
    for module_dir in iter_module_dirs(root, game=game):
        for rel in ("screen_verify.yaml", "routes/screen_verify.yaml"):
            path = module_dir / rel
            if path.is_file():
                paths.append(path)
                break
    return tuple(paths)


def _area_yaml_paths() -> list[Path]:
    """Every per-module ``area.yaml`` manifest, for config-fingerprint caching."""
    from config.module_discovery import iter_module_area_manifests
    from config.paths import repo_root

    return list(iter_module_area_manifests(repo_root(), game=_resolve_active_game()))


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
    for key in (
        "threshold",
        "confidence",
        "min_match_saturation",
        # Active-tab (tab_active) calibration overrides — forwarded by the
        # detector to the overlay engine when a tab strip's contrast differs
        # from the mail-calibrated TAB_ACTIVE_* defaults (e.g. chat).
        "max_mean_saturation",
        "min_mean_value",
        "min_yellow_ratio",
    ):
        if key in raw_d:
            rule[key] = raw_d[key]
    return rule


def _normalize_screen_family(raw_name: object, raw: object) -> ScreenFamilyEntry | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw_name or "").strip()
    if not name:
        return None
    raw_d = cast("dict[str, Any]", raw)
    out: ScreenFamilyEntry = {"name": name}
    for key in (
        "hub",
        "prefix",
        "tab_region",
        "namespace",
        "advance_scenario",
        "next_region",
    ):
        value = raw_d.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    # Sensible defaults: a ``deals`` family covers ``deals`` and ``deals.*``.
    out.setdefault("hub", name)
    out.setdefault("prefix", f"{name}.")
    out.setdefault("namespace", name)
    return out


def _file_fingerprint(path: Path) -> tuple[str, int, int]:
    try:
        st = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


_FpType = tuple[
    tuple[tuple[str, int, int], ...],
    tuple[tuple[str, int, int], ...],
]
_cached_combined_fingerprint: _FpType | None = None


def _combined_config_fingerprint() -> _FpType:
    """Stat config files once per process lifetime.

    Config files are static at runtime in production — re-stat'ing every
    detect_screen tick dominated CPU (~30% of total in the pyroscope profile).
    Call ``invalidate_screen_verify_config()`` (or ``config.reload.reload_config()``)
    when the labeling editor / reload button mutates the on-disk config.
    """
    global _cached_combined_fingerprint
    if _cached_combined_fingerprint is None:
        _cached_combined_fingerprint = (
            tuple(_file_fingerprint(path) for path in _screen_verify_yaml_paths()),
            tuple(_file_fingerprint(path) for path in _area_yaml_paths()),
        )
    return _cached_combined_fingerprint


def invalidate_screen_verify_config() -> None:
    """Drop the frozen fingerprint + parsed config cache + path lists.

    Wired to the dashboard reload button and to tests that mutate config files.
    """
    global _cached_combined_fingerprint
    _cached_combined_fingerprint = None
    _screen_verify_yaml_paths_cached.cache_clear()
    _load_screen_verify_config_cached.cache_clear()
    invalidate_edge_taps_cache()


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
    from config.paths import repo_root

    if fp is None:
        return _load_screen_verify_config_cached(_combined_config_fingerprint())

    root = repo_root()

    if (
        fp
        and isinstance(fp[0], tuple)
        and (not fp[0] or isinstance(fp[0][0], tuple))
        and isinstance(fp[1], tuple)
    ):
        yaml_fps_, _ = cast(
            "tuple[tuple[tuple[str, int, int], ...], tuple[tuple[str, int, int], ...]]",
            fp,
        )
        paths = [Path(yaml_fp[0]) for yaml_fp in yaml_fps_]
    elif (
        fp
        and isinstance(fp[0], tuple)
        and isinstance(fp[0][0], tuple)
    ):
        yaml_fps_, _ = cast(
            "tuple[tuple[tuple[str, int, int], ...], tuple[str, int, int]]", fp
        )
        paths = [Path(yaml_fp[0]) for yaml_fp in yaml_fps_]
    elif fp and isinstance(fp[0], tuple):
        yaml_fp_, _ = cast(
            "tuple[tuple[str, int, int], tuple[str, int, int]]", fp
        )
        paths = [Path(yaml_fp_[0])]
    else:
        fp_single = cast("tuple[str, int, int]", fp)
        paths = [Path(fp_single[0])]

    docs: list[dict[str, Any]] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        raw = raw or {}
        docs.append(raw if isinstance(raw, dict) else {})

    retry = next((doc.get("retry") for doc in docs if isinstance(doc.get("retry"), dict)), {})
    out_families: dict[str, ScreenFamilyEntry] = {}
    for raw in docs:
        families = raw.get("families")
        if not isinstance(families, dict):
            continue
        for family_name, family_raw in families.items():
            family = _normalize_screen_family(family_name, family_raw)
            if family is not None:
                out_families[str(family_name).strip()] = family

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
            rules, landmarks = _coalesce_verify_lists(rules, landmarks)
            entry: ScreenVerifyEntry = {"rules": rules, "landmarks": landmarks}
            if isinstance(retry_raw, dict):
                entry["retry"] = retry_raw
            priority_raw = entry_raw.get("priority") if isinstance(entry_raw, dict) else None
            try:
                entry["priority"] = int(priority_raw) if priority_raw is not None else 100
            except (TypeError, ValueError):
                entry["priority"] = 100
            parent_raw = entry_raw.get("parent") if isinstance(entry_raw, dict) else None
            if isinstance(parent_raw, str) and parent_raw.strip():
                entry["parent"] = parent_raw.strip()
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

    for entry in out_screens.values():
        if not isinstance(entry, dict):
            continue
        raw_rules = entry.get("rules")
        raw_landmarks = entry.get("landmarks")
        rules_list = (
            list(raw_rules) if isinstance(raw_rules, list) else []
        )
        landmarks_list = (
            list(raw_landmarks) if isinstance(raw_landmarks, list) else []
        )
        co_rules, co_landmarks = _coalesce_verify_lists(rules_list, landmarks_list)
        entry["rules"] = co_rules
        entry["landmarks"] = co_landmarks

    out: VerifyConfig = {
        "retry": retry if isinstance(retry, dict) else {},
        "screens": out_screens,
    }
    if out_families:
        out["families"] = out_families
    return out


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


load_screen_verify_config.cache_clear = invalidate_screen_verify_config  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


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


def screen_verify_parent(screen: str) -> str | None:
    """Optional parent screen for ``screen`` (e.g. ``mail.wars`` -> ``mail``).

    The detector gates child evaluation on the parent's anchor template — when
    the parent's landmark group is known-negative for a frame, the child cannot
    match either and is skipped without running its unique landmark rules.
    """
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return None
    entry = screens.get(screen)
    if not isinstance(entry, dict):
        return None
    parent = entry.get("parent")
    if isinstance(parent, str) and parent.strip():
        return parent.strip()
    return None


def screen_family_configs() -> dict[str, ScreenFamilyEntry]:
    families = load_screen_verify_config().get("families")
    if not isinstance(families, dict):
        return {}
    return {
        str(name).strip(): cast("ScreenFamilyEntry", dict(entry))
        for name, entry in families.items()
        if str(name).strip() and isinstance(entry, dict)
    }


def screen_family_for(screen: str) -> tuple[str, ScreenFamilyEntry] | None:
    screen_s = str(screen or "").strip()
    if not screen_s:
        return None
    for name, cfg in screen_family_configs().items():
        hub = str(cfg.get("hub") or "").strip()
        prefix = str(cfg.get("prefix") or "").strip()
        if screen_s == hub or (prefix and screen_s.startswith(prefix)):
            return name, cfg
    return None


def same_screen_family(a: str, b: str) -> bool:
    fa = screen_family_for(a)
    fb = screen_family_for(b)
    return bool(fa and fb and fa[0] == fb[0])


def screen_verify_screen_names() -> list[str]:
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return []
    names = [str(screen).strip() for screen in screens if str(screen).strip()]
    return _sort_screen_names_by_priority(names, screens)


def _sort_screen_names_by_priority(
    names: list[str],
    screens: dict[str, object],
) -> list[str]:
    def _priority(screen: str) -> int:
        entry = screens.get(screen)
        if not isinstance(entry, dict):
            return 100
        try:
            return int(entry.get("priority") or 100)
        except (TypeError, ValueError):
            return 100

    return sorted(names, key=_priority)


def screen_verify_order_names(screen_names: list[str]) -> list[str]:
    """Return ``screen_names`` sorted by ``screen_verify.yaml`` priority (low first)."""
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return [str(s).strip() for s in screen_names if str(s).strip()]
    names = [str(s).strip() for s in screen_names if str(s).strip()]
    return _sort_screen_names_by_priority(names, screens)


# ``main_city`` hub priority in root ``screen_verify.yaml`` (modals use lower values).
MAIN_CITY_HUB_PRIORITY = 10


def screen_verify_config_fingerprint() -> tuple[Any, ...]:
    """Fingerprint for invalidating compiled landmark caches."""
    return _combined_config_fingerprint()


def screen_verify_modal_preempt_names(
    *,
    hub_priority: int = MAIN_CITY_HUB_PRIORITY,
) -> list[str]:
    """Template screens to probe before confirming a ``main_city`` sticky hint.

    Uses the same ``priority`` field as global detection order — typically
    loading / rewards / popups (``priority`` < hub) run before ``main_city``.
    """
    screens = load_screen_verify_config().get("screens")
    if not isinstance(screens, dict):
        return []
    out: list[str] = []
    for name in screen_verify_screen_names():
        entry = screens.get(name)
        if not isinstance(entry, dict):
            continue
        try:
            prio = int(entry.get("priority") or 100)
        except (TypeError, ValueError):
            prio = 100
        if prio < hub_priority:
            out.append(name)
    return out


def _coalesce_verify_lists(
    rules: list[VerifyRule],
    landmarks: list[VerifyRule],
) -> tuple[list[VerifyRule], list[VerifyRule]]:
    """Mirror ``rules`` ↔ ``landmarks`` when YAML lists only one side.

    Most screens use the same template/OCR checks for detection and for verifying a
    navigation hop. Authors can declare ``rules`` only; ``landmarks`` default to the
    same list at load time. Set ``landmarks`` explicitly only when detection must be
    narrower than verify (shop sub-pages are the common case).
    """
    if rules and not landmarks:
        return rules, list(rules)
    if landmarks and not rules:
        mirrored = list(landmarks)
        return mirrored, landmarks
    return rules, landmarks


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
# Cost-aware path finder
# ---------------------------------------------------------------------------

DEFAULT_EDGE_COST = 100
SAME_FAMILY_EDGE_COST = 40
MAIN_CITY_FALLBACK_PENALTY = 5_000
MAIN_CITY_DEPARTURE_PENALTY = 250


def _edge_cost(src: str, dst: str, *, target: str) -> int:
    cost = SAME_FAMILY_EDGE_COST if same_screen_family(src, dst) else DEFAULT_EDGE_COST
    if target != "main_city":
        if dst == "main_city" and src != "main_city":
            cost += MAIN_CITY_FALLBACK_PENALTY
        elif src == "main_city":
            cost += MAIN_CITY_DEPARTURE_PENALTY
    return cost


def route_path_cost(path: list[str] | None, *, target: str | None = None) -> int | None:
    if not path:
        return None
    target_s = str(target or path[-1]).strip()
    total = 0
    for src, dst in itertools.pairwise(path):
        total += _edge_cost(src, dst, target=target_s)
    return total


def bfs_route(src: str, dst: str, *, game: str | None = None) -> list[str] | None:
    """Lowest-cost path ``[src, …, dst]`` over the tap-action graph.

    Historically this was a pure BFS. It now keeps the public name for callers
    but ranks routes by edge cost so local screen-family hops beat a visually
    silly fallback through ``main_city`` when both are available.
    """
    if src == dst:
        return [src]
    _static, _dynamic, graph = graph_for_game(game)
    heap: list[tuple[int, int, tuple[str, ...], str]] = [(0, 0, (src,), src)]
    best: dict[str, tuple[int, int]] = {src: (0, 0)}
    while heap:
        cost, hops, path_t, node = heapq.heappop(heap)
        if node == dst:
            return list(path_t)
        if best.get(node) != (cost, hops):
            continue
        for nb in sorted(graph.get(node, set())):
            next_cost = cost + _edge_cost(node, nb, target=dst)
            next_hops = hops + 1
            prev = best.get(nb)
            if prev is not None and prev <= (next_cost, next_hops):
                continue
            best[nb] = (next_cost, next_hops)
            heapq.heappush(heap, (next_cost, next_hops, (*path_t, nb), nb))
    return None


def route_explain(src: str, dst: str, *, game: str | None = None) -> dict[str, Any]:
    """Small structured route explanation for logs/UI attention banners."""
    selected = bfs_route(src, dst, game=game)
    family_src = screen_family_for(src)
    family_dst = screen_family_for(dst)
    same_family = bool(family_src and family_dst and family_src[0] == family_dst[0])
    out: dict[str, Any] = {
        "src": src,
        "dst": dst,
        "selected_path": selected or [],
        "selected_cost": route_path_cost(selected, target=dst),
        "same_family": same_family,
        "family": family_src[0] if same_family and family_src else "",
    }
    if same_family and family_src:
        cfg = family_src[1]
        out["family_tab_region"] = cfg.get("tab_region", "")
        out["family_next_region"] = cfg.get("next_region", "")
    if src != "main_city" and dst != "main_city":
        to_hub = bfs_route(src, "main_city", game=game)
        from_hub = bfs_route("main_city", dst, game=game)
        via_hub = (
            [*to_hub, *from_hub[1:]]
            if to_hub and from_hub and to_hub[-1] == "main_city"
            else None
        )
        out["main_city_path"] = via_hub or []
        out["main_city_cost"] = route_path_cost(via_hub, target=dst)
    return out


def format_route_explain(src: str, dst: str, *, game: str | None = None) -> str:
    info = route_explain(src, dst, game=game)
    selected = " → ".join(info.get("selected_path") or []) or "unreachable"
    lines = [
        f"route {src} -> {dst}",
        f"selected: {selected} (cost={info.get('selected_cost')})",
    ]
    if info.get("same_family"):
        lines.append(
            "family: "
            f"{info.get('family')} tab_region={info.get('family_tab_region')}"
        )
    main_city_path = info.get("main_city_path")
    if main_city_path:
        lines.append(
            "main_city fallback: "
            f"{' → '.join(main_city_path)} (cost={info.get('main_city_cost')})"
        )
    return "\n".join(lines)


def route_taps(
    src: str, dst: str, *, game: str | None = None
) -> list[list[Tap]] | None:
    """BFS path resolved to tap sequences using **static edges only**.

    Returns ``None`` when the route would require traversing a dynamic edge
    — those can't be resolved without an instance context. Async callers
    should use :func:`route_taps_async` instead. Kept synchronous for tests
    and tooling that only inspect static topology.
    """
    static, _dynamic, _graph = graph_for_game(game)
    path = bfs_route(src, dst, game=game)
    if path is None:
        return None
    result: list[list[Tap]] = []
    for a, b in itertools.pairwise(path):
        taps = static.get((a, b))
        if taps is None:
            return None
        result.append(list(taps))
    return result


def route_hops(
    src: str, dst: str, *, game: str | None = None
) -> list[tuple[str, list[Tap]]] | None:
    """Static-only variant of :func:`route_hops_async` — see that for full semantics."""
    static, _dynamic, _graph = graph_for_game(game)
    path = bfs_route(src, dst, game=game)
    if path is None:
        return None
    result: list[tuple[str, list[Tap]]] = []
    for a, b in itertools.pairwise(path):
        taps = static.get((a, b))
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
    game: str | None = None,
) -> list[list[Tap]] | None:
    """Like :func:`route_taps` but resolves dynamic edges via :data:`EDGE_RESOLVERS`.

    If any hop on the BFS path is a dynamic edge whose resolver returns
    ``None`` (target not in current per-instance state), the whole route is
    treated as unavailable and ``None`` is returned. The caller can then
    fall back / retry after the state refreshes.
    """
    static, _dynamic, _graph = graph_for_game(game)
    path = bfs_route(src, dst, game=game)
    if path is None:
        return None
    result: list[list[Tap]] = []
    for a, b in itertools.pairwise(path):
        taps = static.get((a, b))
        if taps is None:
            taps = await _resolve_dynamic_edge(
                a, b, instance_id=instance_id, redis_client=redis_client, game=game
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
    game: str | None = None,
) -> list[tuple[str, list[Tap]]] | None:
    """Per-hop variant of :func:`route_taps_async`."""
    static, _dynamic, _graph = graph_for_game(game)
    path = bfs_route(src, dst, game=game)
    if path is None:
        return None
    result: list[tuple[str, list[Tap]]] = []
    for a, b in itertools.pairwise(path):
        taps = static.get((a, b))
        if taps is None:
            taps = await _resolve_dynamic_edge(
                a, b, instance_id=instance_id, redis_client=redis_client, game=game
            )
        if taps is None:
            return None
        result.append((b, list(taps)))
    return result


def reachable_screens(src: str, *, game: str | None = None) -> set[str]:
    """All screens reachable from *src* via the tap-action graph (excluding *src*)."""
    _static, _dynamic, graph = graph_for_game(game)
    visited: set[str] = {src}
    queue: deque[str] = deque([src])
    while queue:
        node = queue.popleft()
        for nb in graph.get(node, set()):
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)
    visited.discard(src)
    return visited
