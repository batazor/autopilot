from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import yaml

from analysis.overlay_manifest import (
    load_merged_analyze_yaml,
)
from analysis.overlay_rules import optional_push_scenario_tasks
from config.paths import repo_root as default_repo_root
from dsl import template_resolver as _tmpl
from dsl.cron_specs import (
    load_root_mapping,
    resolve_cron_task_type,
)
from dsl.dsl_schema import validate_dsl_steps
from layout.area_regions import region_names_for

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupValidationIssue:
    severity: str
    source: str
    message: str


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"__load_error__": str(exc)}
    return raw if isinstance(raw, dict) else {}


def _area_region_names(area_doc: dict[str, Any]) -> set[str]:
    """Every region name a use case might reference (base + version blocks)."""
    out: set[str] = set()
    for screen in area_doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        for source in (screen.get("regions"), *(
            v.get("regions") for v in (screen.get("versions") or []) if isinstance(v, dict)
        )):
            if not isinstance(source, list):
                continue
            for reg in source:
                if not isinstance(reg, dict):
                    continue
                out.update(region_names_for(reg))
    return out


def _area_regions_with_red_dot_capability(area_doc: dict[str, Any]) -> set[str]:
    """Region names whose area.json definition has ``has_red_dot: true``.

    The overlay engine (`analysis/overlay_engine.py`) and the DSL match path
    (`tasks/dsl_match_mixin._build_red_dot_only_row`) both short-circuit
    `isRedDot:` / `action: red_dot` rules to ``red_dot_capability_disabled``
    when the targeted region lacks this flag. Without a startup check that's
    a silent runtime no-op: the rule looks healthy in YAML but never fires
    a tap. The annotator UI is the canonical place to enable the flag.
    """
    out: set[str] = set()
    for screen in area_doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        for source in (screen.get("regions"), *(
            v.get("regions") for v in (screen.get("versions") or []) if isinstance(v, dict)
        )):
            if not isinstance(source, list):
                continue
            for reg in source:
                if not isinstance(reg, dict):
                    continue
                if not bool(reg.get("has_red_dot")):
                    continue
                out.update(region_names_for(reg))
    return out


def _area_regions_text_action_with_search_sibling(area_doc: dict[str, Any]) -> set[str]:
    """Text-action regions whose ``<name>_search`` auxiliary bbox exists.

    These are regions where the overlay engine's ``_search`` fallback path
    (``analysis/overlay_engine.py`` text branch) is the ONLY thing that catches
    popup variants which moved the prompt out of the primary bbox. The fallback
    is only triggered when the rule carries ``expected``; without it the DSL
    ``match:`` / ``while_match:`` step silently exits with iterations=0 on
    those popup variants. Catching the missing ``expected`` at startup avoids
    a phantom success in queue history.
    """
    text_regions: set[str] = set()
    all_regions: set[str] = set()
    for screen in area_doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        for source in (screen.get("regions"), *(
            v.get("regions") for v in (screen.get("versions") or []) if isinstance(v, dict)
        )):
            if not isinstance(source, list):
                continue
            for reg in source:
                if not isinstance(reg, dict):
                    continue
                names = region_names_for(reg)
                if not names:
                    continue
                all_regions.update(names)
                if str(reg.get("action") or "").strip() == "text":
                    text_regions.update(names)
    return {r for r in text_regions if f"{r}_search" in all_regions}


def _check_text_action_expected_required(
    issues: list[StartupValidationIssue],
    *,
    text_search_regions: set[str],
    region_names: set[str],
    source: str,
    field: str,
    step: dict[str, Any],
) -> None:
    """Flag ``match:``/``while_match:`` on a text+search region with no ``expected:``.

    Without ``expected``, the overlay engine evaluates ``matched = bool(txt)``
    on the primary bbox alone — the ``_search`` fallback never runs. Popup
    variants whose text shifted out of the primary bbox then return empty
    OCR (matched=False) and the step exits as a phantom success.
    """
    region = str(step.get(field) or "").strip()
    if not region or region not in region_names:
        return
    if region not in text_search_regions:
        return
    expected = step.get("expected")
    has_expected = (
        (isinstance(expected, list) and any(str(x).strip() for x in expected))
        or (isinstance(expected, str) and bool(expected.strip()))
    )
    if has_expected:
        return
    issues.append(
        StartupValidationIssue(
            "error",
            source,
            f"{field} {region!r} is a text-action region with a `_search` "
            "sibling — must carry `expected: [...]` so the overlay engine's "
            "fuzzy + _search fallback can run; otherwise popup variants "
            "silently exit with iterations=0",
        )
    )


def _rule_uses_red_dot(rule: dict[str, Any]) -> bool:
    """Does the overlay rule rely on the red-dot detector?

    Covers both YAML shapes the engine recognises: ``isRedDot: true|false``
    and the long form ``action: red_dot`` / ``action: red_dot_absent``.
    """
    if "isRedDot" in rule and isinstance(rule.get("isRedDot"), bool):
        return True
    action = str(rule.get("action") or "").strip().lower()
    return action in {"red_dot", "red_dot_absent"}


def _check_red_dot_capability(
    issues: list[StartupValidationIssue],
    *,
    red_dot_regions: set[str],
    region_names: set[str],
    source: str,
    field: str,
    value: Any,
) -> None:
    """Verify the region targeted by a red-dot rule has the capability flag."""
    region = str(value or "").strip()
    if not region:
        return
    if region not in region_names:
        # Already reported by ``_check_region`` — don't double-flag.
        return
    if region not in red_dot_regions:
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                f"{field} {region!r} is used with isRedDot/red_dot but the "
                "area.json region has no `has_red_dot: true` capability — "
                "enable it in the annotator or the rule will silently no-op",
            )
        )


def _check_region(
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
    source: str,
    field: str,
    value: Any,
) -> None:
    region = str(value or "").strip()
    if region and region not in region_names:
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                f"{field} references missing area region {region!r}",
            )
        )


def _check_scenario(
    issues: list[StartupValidationIssue],
    *,
    repo_root: Path,
    source: str,
    field: str,
    value: Any,
) -> None:
    """Validate via the runtime resolver so template keys (``level_up_ahmose``)
    aren't false-positives.

    The old ``path.stem`` set treated ``level_up_{hero}.yaml`` as a literal
    file and would reject every concrete hero key the worker actually runs.
    Going through ``template_resolver.resolve`` is the same path the worker's
    ``DslScenarioTask`` takes via ``template_resolver.load_doc``, so startup
    and runtime can't drift.
    """
    name = str(value or "").strip()
    if not name:
        return
    # Names with ``${...}`` placeholders are resolved at enqueue time by the
    # overlay worker (e.g. ``heroes.${hero_id}.wiki`` → ``heroes.ahmose.wiki``
    # after reading ``current_screen``). At startup the placeholder is opaque,
    # so just confirm the template file exists by checking the resolved-key
    # space rather than passing the literal ``${...}`` string to the resolver.
    if "${" in name:
        from dsl.template_resolver import iter_resolved_keys

        prefix, _, rest = name.partition("${")
        _, _, suffix = rest.partition("}")
        for resolved in iter_resolved_keys(repo_root):
            k = resolved.key
            if k.startswith(prefix) and k.endswith(suffix):
                return
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                f"{field} references missing scenario {name!r}",
            )
        )
        return
    if _tmpl.resolve(repo_root, name) is None:
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                f"{field} references missing scenario {name!r}",
            )
        )


def _load_merged_area_region_names(repo_root: Path) -> set[str]:
    """Region names merged across every per-module ``area.yaml`` manifest."""
    try:
        from layout.area_manifest import load_area_doc

        return _area_region_names(load_area_doc(repo_root))
    except Exception:
        return set()


def _overlay_rule_region_refs(rule: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for field in ("region", "search_region"):
        name = str(rule.get(field) or "").strip()
        if name:
            out.append((field, name))
    return out


def _validate_overlay_runtime_area_manifest(repo_root: Path, issues: list[StartupValidationIssue]) -> None:
    """Overlay rules must resolve in the same area doc ``run_overlay_analysis`` loads.

    Startup already merged ``load_area_doc`` for generic region checks, but the
    worker historically read core ``area.json`` only — validation passed while
    module overlay rules returned ``unknown_region`` at runtime. This pass
    binds checks to :func:`analysis.overlay_area.default_area_doc_for_overlay`
    and flags module-only regions when that manifest would omit them.
    """
    import inspect

    from analysis import overlay as overlay_mod
    from analysis import overlay_area as overlay_area_mod
    from analysis.overlay_area import default_area_doc_for_overlay

    # These two checks read function source to assert a specific call site is
    # preserved. ``inspect.getsource`` raises ``OSError`` for Nuitka-compiled
    # modules (no readable source on disk) — in that build mode the validation
    # already ran during the source-tree build, so silently skip here.
    try:
        helper_src = inspect.getsource(overlay_area_mod.default_area_doc_for_overlay)
        if "load_area_doc" not in helper_src:
            issues.append(
                StartupValidationIssue(
                    "error",
                    "src/analysis/overlay_area.py",
                    "default_area_doc_for_overlay must call layout.area_manifest.load_area_doc()",
                )
            )

        run_src = inspect.getsource(overlay_mod.run_overlay_analysis)
        if "default_area_doc_for_overlay" not in run_src:
            issues.append(
                StartupValidationIssue(
                    "error",
                    "src/analysis/overlay.py",
                    "run_overlay_analysis must call default_area_doc_for_overlay() "
                    "when area_doc is omitted (module overlay regions otherwise "
                    "resolve as unknown_region)",
                )
            )
    except OSError:
        # Compiled build (no .py source) — trust the build-time check.
        pass

    try:
        runtime_doc = default_area_doc_for_overlay(repo_root)
    except Exception as exc:
        issues.append(
            StartupValidationIssue(
                "error",
                "overlay:runtime_area",
                f"cannot load overlay runtime area manifest: {exc}",
            )
        )
        return

    runtime_names = _area_region_names(runtime_doc)
    merged_names = _load_merged_area_region_names(repo_root)
    analyze_doc = load_merged_analyze_yaml(repo_root)
    overlay = analyze_doc.get("overlay")
    if not isinstance(overlay, list):
        return

    for idx, raw_rule in enumerate(overlay):
        if not isinstance(raw_rule, dict):
            continue
        rule = cast("dict[str, Any]", raw_rule)
        rule_name = str(rule.get("name") or f"overlay[{idx}]").strip()
        source = f"analyze:{rule_name}"
        for field, region in _overlay_rule_region_refs(rule):
            if region in runtime_names:
                continue
            if region not in merged_names:
                continue
            msg = (
                f"{field} {region!r} is defined under games/wos/*/area.yaml but is "
                "absent from the overlay runtime area manifest "
                "(default_area_doc_for_overlay)"
            )
            issues.append(StartupValidationIssue("error", source, msg))


def _validate_analyze_manifest(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
    red_dot_regions: set[str],
) -> None:
    analyze_doc = load_merged_analyze_yaml(repo_root)
    from config.games import MODULES_DIR_NAME

    overlay = analyze_doc.get("overlay")
    if not isinstance(overlay, list):
        issues.append(
            StartupValidationIssue(
                "error",
                f"{MODULES_DIR_NAME}/*/analyze/analyze.yaml",
                "merged analyze overlay is missing or invalid",
            )
        )
        return

    for idx, raw_rule in enumerate(overlay):
        if not isinstance(raw_rule, dict):
            continue
        rule = cast("dict[str, Any]", raw_rule)
        rule_name = str(rule.get("name") or f"overlay[{idx}]").strip()
        source = f"analyze:{rule_name}"
        _check_region(
            issues,
            region_names=region_names,
            source=source,
            field="region",
            value=rule.get("region"),
        )
        _check_region(
            issues,
            region_names=region_names,
            source=source,
            field="search_region",
            value=rule.get("search_region"),
        )
        if _rule_uses_red_dot(rule):
            _check_red_dot_capability(
                issues,
                red_dot_regions=red_dot_regions,
                region_names=region_names,
                source=source,
                field="region",
                value=rule.get("region"),
            )
        for task in optional_push_scenario_tasks(rule):
            _check_scenario(
                issues,
                repo_root=repo_root,
                source=source,
                field="pushScenario",
                value=task.get("dsl_scenario") or task.get("type"),
            )


_REGION_STEP_KEYS = frozenset({"click", "long_click", "match", "while_match", "ocr"})


def _walk_steps(
    steps: Any,
    *,
    source: str,
    issues: list[StartupValidationIssue],
    repo_root: Path,
    region_names: set[str],
    red_dot_regions: set[str],
    text_search_regions: set[str],
) -> None:
    if not isinstance(steps, list):
        return
    for idx, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            continue
        step = cast("dict[str, Any]", raw_step)
        step_source = f"{source}:step[{idx}]"
        for key in _REGION_STEP_KEYS:
            if key in step:
                _check_region(
                    issues,
                    region_names=region_names,
                    source=step_source,
                    field=key,
                    value=step.get(key),
                )
        # DSL `match:` / `while_match:` with `isRedDot:` go through the
        # red-dot-only short-circuit in dsl_match_mixin._build_red_dot_only_row,
        # which silently sets matched=False when the region lacks the
        # `has_red_dot: true` capability. Catch that mismatch at startup so a
        # forgotten annotator checkbox shows up loud, not as a phantom
        # match_guard_failed in queue history.
        if "isRedDot" in step and isinstance(step.get("isRedDot"), bool):
            for key in ("match", "while_match"):
                if key in step:
                    _check_red_dot_capability(
                        issues,
                        red_dot_regions=red_dot_regions,
                        region_names=region_names,
                        source=step_source,
                        field=key,
                        value=step.get(key),
                    )
        # DSL `match:` / `while_match:` on a text-action region that has a
        # `<name>_search` auxiliary sibling: require `expected:` so the
        # overlay engine's fuzzy + `_search` fallback path activates. Without
        # it the step silently exits with iterations=0 on popup variants
        # that moved the prompt out of the primary bbox.
        for key in ("match", "while_match"):
            if key in step:
                _check_text_action_expected_required(
                    issues,
                    text_search_regions=text_search_regions,
                    region_names=region_names,
                    source=step_source,
                    field=key,
                    step=step,
                )
        _check_region(
            issues,
            region_names=region_names,
            source=step_source,
            field="search_region",
            value=step.get("search_region"),
        )

        repeat = step.get("repeat")
        if isinstance(repeat, dict):
            _check_region(
                issues,
                region_names=region_names,
                source=step_source,
                field="repeat.until_match",
                value=repeat.get("until_match"),
            )
            until_any = repeat.get("until_any_match")
            if isinstance(until_any, list):
                for reg in until_any:
                    _check_region(
                        issues,
                        region_names=region_names,
                        source=step_source,
                        field="repeat.until_any_match",
                        value=reg,
                    )
            stop_regs = repeat.get("stop_after_click_regions")
            if isinstance(stop_regs, list):
                for reg in stop_regs:
                    _check_region(
                        issues,
                        region_names=region_names,
                        source=step_source,
                        field="repeat.stop_after_click_regions",
                        value=reg,
                    )
            _walk_steps(
                repeat.get("steps"),
                source=step_source,
                issues=issues,
                repo_root=repo_root,
                region_names=region_names,
                red_dot_regions=red_dot_regions,
                text_search_regions=text_search_regions,
            )

        if "push_scenario" in step:
            spec = step.get("push_scenario")
            name = spec.get("name") if isinstance(spec, dict) else spec
            _check_scenario(
                issues,
                repo_root=repo_root,
                source=step_source,
                field="push_scenario",
                value=name,
            )

        _walk_steps(
            step.get("steps"),
            source=step_source,
            issues=issues,
            repo_root=repo_root,
            region_names=region_names,
            red_dot_regions=red_dot_regions,
            text_search_regions=text_search_regions,
        )


def duplicate_scenario_names_for_repo(repo_root: Path) -> dict[str, list[str]]:
    """Duplicate ``name:`` values across module-aware scenario roots."""
    from dsl.registry import iter_scenario_yaml_files

    by_name: dict[str, list[str]] = {}
    for _root, path in iter_scenario_yaml_files(repo_root):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            rel = path.as_posix()
        by_name.setdefault(name, []).append(rel)
    return {n: rels for n, rels in by_name.items() if len(rels) > 1}


def _validate_scenarios(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
    red_dot_regions: set[str],
    text_search_regions: set[str],
) -> None:
    from dsl.registry import iter_scenario_yaml_files, scenario_source_label

    scenario_files = iter_scenario_yaml_files(repo_root)
    if not scenario_files:
        return

    for name, rels in duplicate_scenario_names_for_repo(repo_root).items():
        joined = ", ".join(rels)
        issues.append(
            StartupValidationIssue(
                "error",
                "scenarios:names",
                f"duplicate scenario name {name!r} in: {joined}",
            )
        )

    resolved_templates_by_path: dict[Path, list[_tmpl.ResolvedKey]] = {}
    for resolved in _tmpl.iter_resolved_keys(repo_root):
        if resolved.context:
            resolved_templates_by_path.setdefault(resolved.path, []).append(resolved)

    for _root, path in scenario_files:
        rel = scenario_source_label(path, repo_root)
        resolved_keys = resolved_templates_by_path.get(path)
        docs_to_validate: list[tuple[str, dict[str, Any]]] = []
        if resolved_keys:
            for resolved in resolved_keys:
                loaded = _tmpl.load_doc(repo_root, resolved.key)
                if loaded is None:
                    docs_to_validate.append((f"scenario:{rel}({resolved.key})", {}))
                    continue
                _loaded_path, doc = loaded
                docs_to_validate.append((f"scenario:{rel}({resolved.key})", doc))
        else:
            doc = _load_yaml_dict(path)
            docs_to_validate.append((f"scenario:{rel}", doc))

        for source, doc in docs_to_validate:
            if "__load_error__" in doc:
                issues.append(
                    StartupValidationIssue(
                        "error",
                        source,
                        f"cannot parse YAML: {doc['__load_error__']}",
                    )
                )
                continue
            if not str(doc.get("name") or "").strip():
                issues.append(
                    StartupValidationIssue(
                        "error",
                        source,
                        "scenario `name` is empty or missing",
                    )
                )
            # Mirrors the runtime gate in ``DslScenarioTask.execute`` so a typo
            # like ``scope: instnace`` fails at startup instead of silently
            # corrupting state during the first run.
            issues.extend(
                StartupValidationIssue("error", source, err)
                for err in validate_dsl_steps(doc.get("steps"))
            )
            _walk_steps(
                doc.get("steps"),
                source=source,
                issues=issues,
                repo_root=repo_root,
                region_names=region_names,
                red_dot_regions=red_dot_regions,
                text_search_regions=text_search_regions,
            )


def _validate_cron_specs(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    """Every cron YAML's effective ``task_type`` must resolve to a scenario.

    The scheduler enqueues ``resolve_cron_task_type(raw, yml)`` and the worker
    later resolves that key via ``template_resolver.load_doc``. A typo like
    ``task: arena_check`` with no matching scenario silently lands in the
    queue every cron tick and fails as ``scenario_not_found`` — invisible
    unless someone is tailing the worker. Catch the mismatch at startup.
    """
    from dsl.cron_specs import iter_cron_yaml_files_for_repo

    for yml in iter_cron_yaml_files_for_repo(repo_root):
        raw = load_root_mapping(yml)
        if raw is None:
            continue
        task_type = resolve_cron_task_type(raw, yml)
        if not task_type:
            continue
        if _tmpl.resolve(repo_root, task_type) is None:
            try:
                rel = yml.relative_to(repo_root).as_posix()
            except ValueError:
                rel = yml.as_posix()
            issues.append(
                StartupValidationIssue(
                    "error",
                    f"cron:{rel}",
                    f"task {task_type!r} does not resolve to any scenario "
                    "(no literal YAML and no template match) — fix `task:` "
                    "or move the file under `drafts/`",
                )
            )


def _edge_taps_yaml_paths(repo_root: Path) -> list[Path]:
    """Every per-module ``edge_taps.yaml`` / ``routes/edge_taps.yaml`` the
    screen_graph loader merges.

    Validation runs once at supervisor boot and must catch issues in every
    registered game's edges, so we walk all games here.
    """

    from config.games import iter_games
    from config.module_discovery import iter_module_dirs

    paths: list[Path] = []
    for g in iter_games(repo_root):
        for module_dir in iter_module_dirs(repo_root, game=g):
            for rel in ("edge_taps.yaml", "routes/edge_taps.yaml"):
                mod_path = module_dir / rel
                if mod_path.is_file():
                    paths.append(mod_path)
                    break
    return paths


def _validate_edge_taps_file(
    path: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
) -> None:
    doc = _load_yaml_dict(path)
    if "__load_error__" in doc:
        issues.append(
            StartupValidationIssue(
                "error",
                path.as_posix(),
                f"cannot parse YAML: {doc['__load_error__']}",
            )
        )
        return

    edges = doc.get("edges")
    if edges is None:
        return
    if not isinstance(edges, dict):
        issues.append(
            StartupValidationIssue("error", path.as_posix(), "edges must be a mapping")
        )
        return

    for src, dsts in edges.items():
        if not isinstance(dsts, dict):
            issues.append(
                StartupValidationIssue(
                    "error",
                    path.as_posix(),
                    f"edge source {src!r} must map to destination taps",
                )
            )
            continue
        for dst, taps in dsts.items():
            source = f"edge_taps:{src}->{dst}"
            if isinstance(taps, str):
                tap_names = [taps]
            elif isinstance(taps, list):
                tap_names = taps
            elif isinstance(taps, dict):
                # Dynamic edge: resolved at runtime via screen_graph.EDGE_RESOLVERS.
                # Validate the spec shape but skip region-name checks — taps don't
                # exist statically.
                resolver = str(taps.get("resolver") or "").strip()
                if not resolver:
                    issues.append(
                        StartupValidationIssue(
                            "error",
                            source,
                            "dynamic edge spec must include a non-empty `resolver`",
                        )
                    )
                continue
            else:
                issues.append(
                    StartupValidationIssue(
                        "error",
                        source,
                        "tap sequence must be a region name, list of region names, "
                        "or a dynamic edge spec ({resolver, target})",
                    )
                )
                continue
            for tap in tap_names:
                if isinstance(tap, dict):
                    action_type = str(tap.get("type") or "").strip()
                    if action_type == "system_back":
                        continue
                    issues.append(
                        StartupValidationIssue(
                            "error",
                            source,
                            "tap action dict must use a supported `type` "
                            f"(got {action_type!r})",
                        )
                    )
                    continue
                _check_region(
                    issues,
                    region_names=region_names,
                    source=source,
                    field="tap",
                    value=tap,
                )


def _validate_edge_taps(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
) -> None:
    """Walk every per-module ``edge_taps.yaml``."""

    for path in _edge_taps_yaml_paths(repo_root):
        _validate_edge_taps_file(path, issues, region_names=region_names)


def _screen_verify_yaml_paths(repo_root: Path) -> list[Path]:
    """Every per-module ``screen_verify.yaml`` / ``routes/screen_verify.yaml``
    across all registered games.

    Mirrors :func:`_edge_taps_yaml_paths` so the dead-end check stays in lockstep
    with the navigator's screen registry.
    """
    from config.games import iter_games
    from config.module_discovery import iter_module_dirs

    paths: list[Path] = []
    for g in iter_games(repo_root):
        for module_dir in iter_module_dirs(repo_root, game=g):
            for rel in ("screen_verify.yaml", "routes/screen_verify.yaml"):
                mod_path = module_dir / rel
                if mod_path.is_file():
                    paths.append(mod_path)
                    break
    return paths


def _collect_screen_verify_entries(
    repo_root: Path,
) -> dict[str, tuple[int, bool, str]]:
    """Map detectable screen name → ``(priority, terminal_opt_out, source_path)``.

    The first occurrence wins on duplicate names (same as the runtime loader's
    behaviour — later modules don't shadow earlier definitions).
    """
    out: dict[str, tuple[int, bool, str]] = {}
    for path in _screen_verify_yaml_paths(repo_root):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        screens = doc.get("screens")
        if not isinstance(screens, dict):
            continue
        for raw_name, raw_entry in screens.items():
            name = str(raw_name).strip()
            if not name or name in out:
                continue
            prio = 100
            terminal = False
            if isinstance(raw_entry, dict):
                try:
                    prio = int(raw_entry.get("priority") or 100)
                except (TypeError, ValueError):
                    prio = 100
                terminal = bool(raw_entry.get("terminal"))
            try:
                rel = path.relative_to(repo_root).as_posix()
            except ValueError:
                rel = path.as_posix()
            out[name] = (prio, terminal, rel)
    return out


def _collect_edge_sources(repo_root: Path) -> set[str]:
    """Every screen that declares at least one outgoing edge in any
    ``edge_taps.yaml``.

    Both static (``[region]``) and dynamic (``{resolver: ...}``) edges count —
    each gives the navigator a way to leave the screen.
    """
    out: set[str] = set()
    for path in _edge_taps_yaml_paths(repo_root):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        edges = doc.get("edges")
        if not isinstance(edges, dict):
            continue
        for src, dsts in edges.items():
            if not isinstance(dsts, dict) or not dsts:
                continue
            name = str(src).strip()
            if name:
                out.add(name)
    return out


def _validate_dead_end_screens(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    """Flag detectable screens with no outgoing edges in any ``edge_taps.yaml``.

    A screen registered in ``screen_verify.yaml`` but absent as a source key in
    every ``edge_taps.yaml`` is a one-way trap: the navigator can identify that
    the device is parked there but cannot route anywhere from it, so every
    scenario with a ``node:`` target dies with ``navigation_failed`` until the
    device drifts off the screen on its own (motivating case: ``shop.artisans_trove``
    used to lack the ``→ main_city: [icon.page.back]`` edge that all other shop
    sub-tabs had, and caused a cascade of ``navigation_failed`` until escaped).

    Modal / popup screens (``priority < MAIN_CITY_HUB_PRIORITY``) are exempt —
    they're dismissed by overlay-driven popup scenarios, not graph routing.
    Screens that are legitimately transient and handled entirely by their own
    scenario (e.g. ``exploration.victory`` → tap "next") can opt out by setting
    ``terminal: true`` on the ``screen_verify.yaml`` entry.
    """
    from navigation.screen_graph import MAIN_CITY_HUB_PRIORITY

    screens = _collect_screen_verify_entries(repo_root)
    if not screens:
        return
    sources = _collect_edge_sources(repo_root)

    for name, (prio, terminal, src_path) in sorted(screens.items()):
        if prio < MAIN_CITY_HUB_PRIORITY:
            continue
        if terminal:
            continue
        if name in sources:
            continue
        issues.append(
            StartupValidationIssue(
                "error",
                f"screen_verify:{name}",
                f"screen {name!r} is detectable in {src_path} but has no "
                "outgoing edges in any edge_taps.yaml — navigator cannot route "
                "away from it; any scenario with `node:` target will dump "
                "`navigation_failed` until the device leaves the screen. Add an "
                "edge (e.g. `→ main_city: [icon.page.back]`) or annotate the "
                "screen_verify entry with `terminal: true` if it is handled "
                "entirely by its own tap-driven scenario.",
            )
        )


def validate_startup_configs(repo_root: Path | None = None) -> list[StartupValidationIssue]:
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    issues: list[StartupValidationIssue] = []

    area_doc: dict[str, Any] = {}
    try:
        from layout.area_manifest import load_area_doc

        area_doc = load_area_doc(root)
    except Exception as exc:
        from config.games import MODULES_DIR_NAME

        issues.append(
            StartupValidationIssue(
                "error",
                f"{MODULES_DIR_NAME}/**/area.yaml",
                f"cannot parse merged area docs: {exc}",
            )
        )

    region_names = _area_region_names(area_doc)
    red_dot_regions = _area_regions_with_red_dot_capability(area_doc)
    text_search_regions = _area_regions_text_action_with_search_sibling(area_doc)

    _validate_edge_taps(root, issues, region_names=region_names)
    _validate_dead_end_screens(root, issues)
    _validate_cron_specs(root, issues)
    _validate_analyze_manifest(
        root,
        issues,
        region_names=region_names,
        red_dot_regions=red_dot_regions,
    )
    _validate_overlay_runtime_area_manifest(root, issues)
    _validate_scenarios(
        root,
        issues,
        region_names=region_names,
        red_dot_regions=red_dot_regions,
        text_search_regions=text_search_regions,
    )
    return issues


def log_startup_config_validation(repo_root: Path | None = None) -> list[StartupValidationIssue]:
    issues = validate_startup_configs(repo_root)
    if not issues:
        logger.info("startup config validation: ok")
        return []

    logger.error("startup config validation: %d issue(s) found", len(issues))
    for issue in issues:
        log = logger.error if issue.severity == "error" else logger.warning
        log("startup config validation: [%s] %s: %s", issue.severity, issue.source, issue.message)
    return issues


_ACK_ENV_VAR = "WOS_VALIDATION_ACK"


def _validation_ack_via_env() -> bool:
    return os.environ.get(_ACK_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "y"}


def assert_startup_configs_valid(repo_root: Path | None = None) -> None:
    """Raise on startup config issues unless explicitly acknowledged.

    No interactive TTY prompt — that would hang the embedded supervisor
    forever waiting on a stdin readline from a background thread no operator
    can see. Acknowledge by setting ``WOS_VALIDATION_ACK=1`` in the env;
    otherwise the supervisor aborts so the operator notices broken modules
    immediately instead of running with a half-functional scenario set.
    """
    issues = log_startup_config_validation(repo_root)
    if not issues:
        return

    if _validation_ack_via_env():
        logger.warning(
            "startup config validation: %d issue(s) acknowledged via %s — continuing",
            len(issues),
            _ACK_ENV_VAR,
        )
        return

    msg = (
        f"startup config validation failed: {len(issues)} issue(s). "
        f"Fix the modules above or set {_ACK_ENV_VAR}=1 to override."
    )
    raise RuntimeError(msg)
