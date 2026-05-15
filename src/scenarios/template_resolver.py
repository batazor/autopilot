"""Template-aware scenario YAML resolution.

Many hero scenarios share an identical body — only the hero id/name varies.
Instead of N near-duplicate files, the repo keeps one template per shape with
a placeholder in the filename (e.g. ``level_up_{hero}.yaml``) and ``${hero_id}``
/ ``${hero_name}`` placeholders in the body. At lookup time the resolver:

1. Tries a literal ``{key}.yaml`` match inside module scenario roots.
2. Falls back to scanning template filenames; matches the placeholder against
   the key, validates the captured value against the heroes wiki index,
   and returns ``(template_path, substitution_context)``.

Body rendering is plain ``${name}`` substitution before YAML parse — kept
deliberately simple so it survives ``yaml.safe_load`` without escaping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Extend when a new substitution axis lands. Each axis must:
#   * appear as ``{axis}`` in template filenames,
#   * resolve to one or more ``${...}`` body placeholders via ``_axis_context``.
_AXES = ("hero",)
_FILENAME_PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(_AXES) + r")\}")
# Hero ids in the heroes wiki index are lowercase ASCII + underscores.
_AXIS_VALUE_RE = r"[a-z0-9_]+"


@dataclass(frozen=True)
class ResolvedScenario:
    """Path to the YAML and the substitution context to render it with.

    ``context`` is empty for literal (non-template) matches.
    """

    path: Path
    context: dict[str, str]


@lru_cache(maxsize=4)
def _hero_index(repo_root_s: str) -> dict[str, str]:
    """``hero_id → display name`` from ``modules/core/heroes/wiki/heroes/index.yaml``."""
    from config.heroes import hero_index_path

    idx_path = hero_index_path(Path(repo_root_s))
    try:
        raw = yaml.safe_load(idx_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, str] = {}
    for entry in raw.get("heroes") or []:
        if not isinstance(entry, dict):
            continue
        hid = str(entry.get("id") or "").strip()
        if not hid:
            continue
        name = str(entry.get("name") or hid).strip() or hid
        out[hid] = name
    return out


def _axis_context(repo_root: Path, axis: str, value: str) -> dict[str, str] | None:
    """Map a captured filename placeholder to ``${...}`` body substitutions.

    Returns ``None`` when the captured value isn't valid for the axis (e.g. a
    hero id that isn't in the heroes wiki index) so the caller can move on
    to the next template instead of rendering nonsense.
    """
    if axis == "hero":
        index = _hero_index(str(repo_root))
        name = index.get(value)
        if name is None:
            return None
        return {"hero_id": value, "hero_name": name}
    return None


def _stem_to_regex(stem: str) -> tuple[re.Pattern[str], list[str]]:
    """``level_up_{hero}`` → ``^level_up_(?P<hero>[a-z0-9_]+)$`` + ``["hero"]``."""
    parts = _FILENAME_PLACEHOLDER_RE.split(stem)
    if len(parts) == 1:
        return re.compile("^" + re.escape(parts[0]) + "$"), []
    rebuilt: list[str] = [re.escape(parts[0])]
    axes: list[str] = []
    i = 1
    while i < len(parts):
        axis = parts[i]
        axes.append(axis)
        rebuilt.append(f"(?P<{axis}>{_AXIS_VALUE_RE})")
        if i + 1 < len(parts):
            rebuilt.append(re.escape(parts[i + 1]))
        i += 2
    return re.compile("^" + "".join(rebuilt) + "$"), axes


def _iter_template_yaml_paths(scenarios_root: Path) -> list[Path]:
    """All ``*.yaml`` whose filename contains a known ``{axis}`` placeholder.

    Drafts are excluded — they're never executable. Sorted for determinism.
    """
    out: list[Path] = []
    for p in scenarios_root.rglob("*.yaml"):
        rel = p.relative_to(scenarios_root).as_posix()
        if rel.startswith("drafts/"):
            continue
        if _FILENAME_PLACEHOLDER_RE.search(p.name):
            out.append(p)
    return sorted(out, key=lambda p: (len(p.relative_to(scenarios_root).parts), p.as_posix()))


def _scenario_roots(repo_root: Path) -> list[Path]:
    """Every module-owned scenario directory.

    Order matches :func:`scenarios.registry.scenario_roots`. The first root
    with a hit wins for literal lookups so resolution stays deterministic when
    two modules expose the same key.
    """
    # Local import: ``scenarios.registry`` already imports from this module's
    # neighbourhood, and pulling it at module-load time creates an unnecessary
    # import-order risk for early bootstrap callers.
    from scenarios.registry import scenario_roots

    return [r.path for r in scenario_roots(repo_root)]


def resolve(repo_root: Path, scenario_key: str) -> ResolvedScenario | None:
    """Literal-then-template resolution across module scenario roots."""
    key = (scenario_key or "").strip()
    if not key:
        return None

    roots = _scenario_roots(repo_root)
    if not roots:
        return None

    for root in roots:
        literal_hits: list[Path] = []
        for p in root.rglob(f"{key}.yaml"):
            rel = p.relative_to(root).as_posix()
            if rel.startswith("drafts/"):
                continue
            literal_hits.append(p)
        if literal_hits:
            literal_hits.sort(key=lambda p: (len(p.relative_to(root).parts), p.as_posix()))
            return ResolvedScenario(path=literal_hits[0], context={})

    for root in roots:
        for tmpl in _iter_template_yaml_paths(root):
            regex, axes = _stem_to_regex(tmpl.stem)
            m = regex.match(key)
            if not m:
                continue
            ctx: dict[str, str] = {}
            ok = True
            for axis in axes:
                sub = _axis_context(repo_root, axis, m.group(axis))
                if sub is None:
                    ok = False
                    break
                ctx.update(sub)
            if ok:
                return ResolvedScenario(path=tmpl, context=ctx)
    return None


def render(text: str, ctx: dict[str, str]) -> str:
    """Replace ``${key}`` body placeholders. Unknown ``${...}`` is kept as-is."""
    if not ctx:
        return text
    out = text
    for k, v in ctx.items():
        out = out.replace(f"${{{k}}}", v)
    return out


def load_doc(repo_root: Path, scenario_key: str) -> tuple[Path, dict[str, Any]] | None:
    """Resolve, render placeholders, and parse the scenario YAML.

    Returns ``(path, doc)``. ``doc`` is ``{}`` on parse failure (matches
    ``tasks.dsl_scenario_helpers._load_yaml`` semantics so the calling layer's
    "invalid steps" branch can take over).
    """
    resolved = resolve(repo_root, scenario_key)
    if resolved is None:
        return None
    try:
        st = resolved.path.stat()
    except OSError:
        return None
    ctx_items = tuple(sorted(resolved.context.items()))
    doc = _load_doc_cached(str(resolved.path), st.st_mtime_ns, st.st_size, ctx_items)
    return resolved.path, doc


@lru_cache(maxsize=1024)
def _load_doc_cached(
    path_s: str, mtime_ns: int, size: int, ctx_items: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    _ = (mtime_ns, size)
    try:
        text = Path(path_s).read_text(encoding="utf-8")
    except OSError:
        return {}
    rendered = render(text, dict(ctx_items))
    try:
        raw = yaml.safe_load(rendered)
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


def display_name(repo_root: Path, scenario_key: str) -> str:
    """Rendered ``name:`` for ``scenario_key``; falls back to the key itself.

    Used by UI surfaces that show the human label of a scenario currently
    running on the worker (Click Approvals card, queue history, etc.) — for
    template-resolved keys like ``level_up_ahmose`` this returns
    ``"⬆️ Level up · Ahmose"`` instead of the raw key.
    """
    key = (scenario_key or "").strip()
    if not key:
        return ""
    loaded = load_doc(repo_root, key)
    if loaded is None:
        return key
    _path, doc = loaded
    name = str(doc.get("name") or "").strip()
    return name or key


@dataclass(frozen=True)
class ResolvedKey:
    """One concrete scenario key — either a literal file or a template fill.

    ``key`` is what the worker / queue uses (e.g. ``level_up_ahmose``);
    ``path`` points to the source YAML (literal or template); ``context`` is
    the substitution dict (empty for literal entries).
    """

    key: str
    path: Path
    context: dict[str, str]


def iter_resolved_keys(repo_root: Path) -> list[ResolvedKey]:
    """Every concrete scenario key the worker can run, across core + modules.

    Literal files contribute one entry each; template files fan out to one
    entry per known axis value (e.g. ``level_up_{hero}.yaml`` × 62 heroes).
    Used by UI listings (Debug runner picker, click_approvals name lookup)
    that need to enumerate the full key space, not just on-disk filenames.

    Core wins for duplicate ``key`` collisions — module-shadowed core keys
    keep their core path so UI listings stay stable when a module ships an
    override file by accident.
    """
    out: list[ResolvedKey] = []
    seen: set[str] = set()

    for root in _scenario_roots(repo_root):
        for p in sorted(root.rglob("*.yaml")):
            rel = p.relative_to(root).as_posix()
            if rel.startswith("drafts/"):
                continue
            if not _FILENAME_PLACEHOLDER_RE.search(p.name):
                if p.stem in seen:
                    continue
                seen.add(p.stem)
                out.append(ResolvedKey(key=p.stem, path=p, context={}))
                continue
            _regex, axes = _stem_to_regex(p.stem)
            if axes == ["hero"]:
                for hid, hname in _hero_index(str(repo_root)).items():
                    concrete_key = p.stem.replace("{hero}", hid)
                    if concrete_key in seen:
                        continue
                    seen.add(concrete_key)
                    out.append(
                        ResolvedKey(
                            key=concrete_key,
                            path=p,
                            context={"hero_id": hid, "hero_name": hname},
                        )
                    )
    return out
