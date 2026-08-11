from __future__ import annotations

import logging
import os
import time
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
        for source in (screen.get("regions"),):
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
        for source in (screen.get("regions"),):
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
        for source in (screen.get("regions"),):
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


def _cross_ref_severity() -> str:
    """``warning`` when an operator allowlist (``WOS_MODULES`` / ``WOS_SCENARIOS``) is active.

    A partial-fleet slice (e.g. intel+arena only) legitimately leaves kept
    modules pointing at scenarios/regions of excluded modules — those pushes /
    edges just soft-skip at runtime (``scenario_not_found`` / a nav edge that
    never fires). Downgrading these to warnings keeps the slice bootable while
    a full-fleet config still errors hard on a real typo. The scenario-key
    allowlist leaves exactly the same dangling references, so it downgrades too.
    """
    from config.module_discovery import _module_allowlist
    from dsl.registry import scenario_allowlist

    sliced = _module_allowlist() is not None or scenario_allowlist() is not None
    return "warning" if sliced else "error"


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
                _cross_ref_severity(),
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
                _cross_ref_severity(),
                source,
                f"{field} references missing scenario {name!r}",
            )
        )
        return
    if _tmpl.resolve(repo_root, name) is None:
        issues.append(
            StartupValidationIssue(
                _cross_ref_severity(),
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


def _load_merged_area_region_names_for_game(repo_root: Path, *, game: str) -> set[str]:
    """Region names merged across one game's per-module ``area.yaml`` manifests."""
    try:
        from layout.area_manifest import load_area_doc

        return _area_region_names(load_area_doc(repo_root, game=game))
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
    # preserved. ``inspect.getsource`` can raise ``OSError`` when the source
    # isn't available on disk — skip the check rather than fail in that case.
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


def _check_rule_template_file(
    issues: list[StartupValidationIssue],
    *,
    repo_root: Path,
    source: str,
    rule: dict[str, Any],
) -> None:
    """A ``template:`` on an overlay rule must resolve to a real file.

    The overlay engine (`analysis/overlay_engine.py` direct-template branch)
    resolves ``template:`` as ``repo_root / <path>`` and short-circuits to
    ``template_outside_repo`` / ``template_load_failed`` when the path escapes
    the repo or the file is missing — silently, on every tick. A deleted or
    mistyped crop therefore looks like "the icon is never on screen" instead
    of an error; catch it at startup.
    """
    tpl = str(rule.get("template") or "").replace("\\", "/").strip()
    if not tpl:
        return
    tpl_path = (repo_root / tpl.lstrip("/")).resolve()
    try:
        tpl_path.relative_to(repo_root.resolve())
    except ValueError:
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                f"template {tpl!r} escapes the repository — the overlay engine "
                "rejects it as template_outside_repo on every tick",
            )
        )
        return
    if not tpl_path.is_file():
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                f"template {tpl!r} does not exist on disk — the rule evaluates "
                "to template_load_failed on every tick and never matches; fix "
                "the path or re-export the crop",
            )
        )


def _direct_template_covered_regions(repo_root: Path) -> set[str]:
    """Region names targeted by an overlay rule with an on-disk ``template:``.

    These regions match through the direct-template branch, which runs before
    the exported-crop lookup — a missing area crop is harmless for them.
    """
    out: set[str] = set()
    analyze_doc = load_merged_analyze_yaml(repo_root)
    overlay = analyze_doc.get("overlay")
    if not isinstance(overlay, list):
        return out
    for raw_rule in overlay:
        if not isinstance(raw_rule, dict):
            continue
        tpl = str(raw_rule.get("template") or "").replace("\\", "/").strip()
        if not tpl or not (repo_root / tpl.lstrip("/")).is_file():
            continue
        region = str(raw_rule.get("region") or "").strip()
        if region:
            out.add(region)
    return out


def _validate_area_exist_region_sources(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    """Every ``exist`` region needs a template source the matcher can load.

    The findIcon/exist evaluation (`analysis/overlay_engine.py`) resolves the
    exported crop ``references/crop/<ref_stem>_<region>.png``; when it is
    missing it auto-exports the patch from the reference screenshot (the
    screen's ``ocr:`` path). With neither on disk the region silently returns
    ``missing_crop_png`` (or ``missing_bbox_or_ocr`` when the screen has no
    ``ocr:`` at all) on every tick — the rule looks healthy in YAML but can
    never match. Regions covered by a valid direct-template rule are exempt:
    that branch never touches the crop. ``static: true`` zones are exempt too:
    the engine blind-taps the bbox by contract and never loads a template.

    Warning severity: some of these are parked half-labeled modules; the goal
    is a loud fleet-health banner, not a bricked startup.
    """
    from config.games import iter_games
    from layout.area_regions import effective_ocr_for_region
    from layout.crop_paths import exported_crop_png

    covered = _direct_template_covered_regions(repo_root)
    seen: set[tuple[str, str, str]] = set()
    for g in iter_games(repo_root):
        try:
            from layout.area_manifest import load_area_doc

            area_doc = load_area_doc(repo_root, game=g)
        except Exception:
            continue
        for entry in area_doc.get("screens") or []:
            if not isinstance(entry, dict):
                continue
            screen = str(entry.get("screen_id") or "").strip() or "?"
            for source_regions in (entry.get("regions"),):
                if not isinstance(source_regions, list):
                    continue
                for reg in source_regions:
                    if not isinstance(reg, dict):
                        continue
                    if str(reg.get("action") or "").strip() != "exist":
                        continue
                    if bool(reg.get("static")):
                        continue
                    name = str(reg.get("name") or "").strip()
                    if not name or name in covered:
                        continue
                    source = f"area:{g}:{screen}"
                    ref_rel = effective_ocr_for_region(entry, reg)
                    key = (source, name, ref_rel)
                    if key in seen:
                        continue
                    if not ref_rel:
                        seen.add(key)
                        issues.append(
                            StartupValidationIssue(
                                "warning",
                                source,
                                f"exist region {name!r} has no `ocr:` reference "
                                "screenshot on its screen entry — the matcher "
                                "returns missing_bbox_or_ocr on every tick; "
                                "capture a reference in /labeling or add an "
                                "`ocr:` path",
                            )
                        )
                        continue
                    crop = exported_crop_png(repo_root, ref_rel, name)
                    if crop.is_file() or (repo_root / ref_rel).is_file():
                        continue
                    seen.add(key)
                    try:
                        crop_rel = crop.relative_to(repo_root).as_posix()
                    except ValueError:
                        crop_rel = crop.as_posix()
                    issues.append(
                        StartupValidationIssue(
                            "warning",
                            source,
                            f"exist region {name!r} has neither its exported crop "
                            f"({crop_rel}) nor the reference screenshot "
                            f"({ref_rel}) on disk — the matcher returns "
                            "missing_crop_png on every tick; re-capture the "
                            "reference in /labeling or commit the crop",
                        )
                    )


def _validate_analyze_manifest(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
    red_dot_regions: set[str],
    catalog: str | None = None,
) -> None:
    """Check the merged overlay rule set of ONE module catalog.

    ``catalog=None`` keeps the historical behaviour (whatever catalog the process
    has bound). Startup validation passes it explicitly: at boot no catalog is
    bound, so the default resolved to ``wos`` and the overlay catalogs' rules
    were checked by nobody.
    """
    analyze_doc = load_merged_analyze_yaml(repo_root, game=catalog)
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
        _check_rule_template_file(
            issues,
            repo_root=repo_root,
            source=source,
            rule=rule,
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
                    if action_type == "any_of":
                        # Alternative-tap edge (navigator._tap_any_of_async): one
                        # destination reachable by any of several buttons. Each
                        # candidate must be a real region.
                        regions = tap.get("regions")
                        if not isinstance(regions, list) or not regions:
                            issues.append(
                                StartupValidationIssue(
                                    "error",
                                    source,
                                    "any_of tap action must list a non-empty "
                                    "`regions` array",
                                )
                            )
                            continue
                        for region in regions:
                            _check_region(
                                issues,
                                region_names=region_names,
                                source=source,
                                field="any_of region",
                                value=region,
                            )
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
) -> None:
    """Walk every per-module ``edge_taps.yaml``."""

    from config.games import iter_games
    from config.module_discovery import iter_module_dirs

    for g in iter_games(repo_root):
        region_names = _load_merged_area_region_names_for_game(repo_root, game=g)
        for module_dir in iter_module_dirs(repo_root, game=g):
            for rel in ("edge_taps.yaml", "routes/edge_taps.yaml"):
                path = module_dir / rel
                if path.is_file():
                    _validate_edge_taps_file(path, issues, region_names=region_names)
                    break


def _collect_screen_families(repo_root: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in _screen_verify_yaml_paths(repo_root):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        families = doc.get("families")
        if not isinstance(families, dict):
            continue
        for raw_name, raw_cfg in families.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_cfg, dict):
                continue
            cfg: dict[str, str] = {"hub": name, "prefix": f"{name}.", "namespace": name}
            for key in (
                "hub",
                "prefix",
                "tab_region",
                "namespace",
                "advance_scenario",
                "next_region",
            ):
                value = raw_cfg.get(key)
                if isinstance(value, str) and value.strip():
                    cfg[key] = value.strip()
            out[name] = cfg
    return out


def _collect_edge_pairs(repo_root: Path) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for path in _edge_taps_yaml_paths(repo_root):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        edges = doc.get("edges")
        if not isinstance(edges, dict):
            continue
        for src, dsts in edges.items():
            if not isinstance(dsts, dict):
                continue
            src_s = str(src).strip()
            for dst in dsts:
                dst_s = str(dst).strip()
                if src_s and dst_s:
                    out.add((src_s, dst_s))
    return out


_UNIVERSAL_HUB = "main_city"
"""Screen every family can fall back through: ``member → main_city → sibling``."""


def _validate_screen_family_route_gaps(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    def _has_local_path(graph: dict[str, set[str]], src: str, dst: str) -> bool:
        seen = {src}
        queue = [src]
        while queue:
            node = queue.pop(0)
            for nb in sorted(graph.get(node, set())):
                if nb == dst:
                    return True
                if nb in seen:
                    continue
                seen.add(nb)
                queue.append(nb)
        return False

    families = _collect_screen_families(repo_root)
    if not families:
        return
    entries = _collect_screen_verify_entries(repo_root)
    edge_pairs = _collect_edge_pairs(repo_root)
    screen_names = sorted(entries)
    for name, cfg in sorted(families.items()):
        hub = str(cfg.get("hub") or name).strip()
        prefix = str(cfg.get("prefix") or f"{name}.").strip()
        members = [
            screen
            for screen in screen_names
            if screen == hub or (prefix and screen.startswith(prefix))
        ]
        if len(members) < 2:
            continue
        # ``main_city`` is the universal hub: leaving a family member out to
        # main_city (``icon.page.back``) and re-entering a sibling/hub from there
        # is a first-class, intentional fallback — Shop documents exactly this
        # ("cross-tab navigation goes main_city → shop → tab") and many Deals are
        # entered straight from main_city rather than from the deals hub. Credit
        # it as a routing waypoint so this check flags only members that are
        # genuinely unreachable, not the deliberate via-hub pattern.
        waypoints = set(members) | {_UNIVERSAL_HUB}
        local_graph: dict[str, set[str]] = {screen: set() for screen in waypoints}
        for src, dst in edge_pairs:
            if src in waypoints and dst in waypoints:
                local_graph.setdefault(src, set()).add(dst)

        gaps: list[tuple[str, str]] = []
        for src in members:
            for dst in members:
                if src == dst:
                    continue
                if src != hub and dst != hub:
                    continue
                if not _has_local_path(local_graph, src, dst):
                    gaps.append((src, dst))
        if not gaps:
            continue
        examples = ", ".join(f"{src}->{dst}" for src, dst in gaps[:5])
        more = "" if len(gaps) <= 5 else f", +{len(gaps) - 5} more"
        issues.append(
            StartupValidationIssue(
                "warning",
                f"screen_family:{name}",
                f"{len(gaps)} sibling route gap(s) inside family {name!r} "
                f"(tab_region={cfg.get('tab_region', '-')!r}); examples: "
                f"{examples}{more}",
            )
        )


def _screen_verify_yaml_paths_for_catalog(repo_root: Path, catalog: str) -> list[Path]:
    """``screen_verify.yaml`` paths for ONE module catalog, in discovery order.

    Discovery order is what decides shadowing, so the order here must be the
    order :func:`config.module_discovery.iter_module_dirs` returns.
    """
    from config.module_discovery import iter_module_dirs

    paths: list[Path] = []
    for module_dir in iter_module_dirs(repo_root, game=catalog):
        for rel in ("screen_verify.yaml", "routes/screen_verify.yaml"):
            mod_path = module_dir / rel
            if mod_path.is_file():
                paths.append(mod_path)
                break
    return paths


def _screen_verify_yaml_paths(repo_root: Path) -> list[Path]:
    """Every per-module ``screen_verify.yaml`` across all registered games.

    Mirrors :func:`_edge_taps_yaml_paths`. Kept for the collectors that
    legitimately want the flat union; anything that resolves a screen NAME must
    use :func:`_screen_verify_yaml_paths_for_catalog` instead, because names are
    only unique within a catalog.
    """
    from config.games import iter_games

    paths: list[Path] = []
    for g in iter_games(repo_root):
        paths.extend(_screen_verify_yaml_paths_for_catalog(repo_root, g))
    return paths


def _collect_screen_verify_entries_for_catalog(
    repo_root: Path, catalog: str
) -> dict[str, tuple[int, bool, str]]:
    """Screen name → ``(priority, terminal_opt_out, source_path)`` for ONE catalog.

    The LAST occurrence wins on duplicate names, matching the runtime loader
    (``navigation.screen_graph`` does a plain ``out_screens[screen] = entry`` as
    it walks the discovery order, so a later module shadows an earlier one).

    This used to be first-wins *and* pooled across games, with a docstring
    claiming first-wins was the runtime behaviour. Both halves were wrong, and
    the divergence had teeth: under the ``wos_ru`` catalog the overlay redefines
    ``chat`` / ``mail`` / ``welcome_back`` / ``survivor_status``, so the worker
    ran the overlay entries while the validator checked the base ones — the four
    RU documents were validated by nobody. Pooling made it worse in the other
    direction: kingshot and wos both define ``welcome_back``, and a global
    last-wins hands the name to whichever game sorts last, which no runtime ever
    does.
    """
    out: dict[str, tuple[int, bool, str]] = {}
    for path in _screen_verify_yaml_paths_for_catalog(repo_root, catalog):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        screens = doc.get("screens")
        if not isinstance(screens, dict):
            continue
        for raw_name, raw_entry in screens.items():
            name = str(raw_name).strip()
            if not name:
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


def _collect_screen_verify_entries(
    repo_root: Path,
) -> dict[str, tuple[int, bool, str]]:
    """Union of every catalog's screen entries.

    Retained for the checks that only ask "does this screen exist anywhere".
    A check that reasons about a screen's *definition* must go per-catalog via
    :func:`_collect_screen_verify_entries_for_catalog` — a name means different
    things in different catalogs.
    """
    from config.games import iter_games

    out: dict[str, tuple[int, bool, str]] = {}
    for g in iter_games(repo_root):
        out.update(_collect_screen_verify_entries_for_catalog(repo_root, g))
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


def _validate_unreachable_screens(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    """Flag detectable screens no declared edge can reach from ``main_city``.

    The sibling :func:`_validate_dead_end_screens` only checks the OUTBOUND
    direction — it catches "the navigator cannot leave this screen". The
    inbound direction was unchecked, so a screen could be perfectly detectable,
    have a clean exit edge, and still be impossible for the navigator to route
    *to*. Every scenario with that ``node:`` target then fails, and the usual
    workaround is a hand-rolled gesture ``exec:`` that taps blind.

    Motivating case: ``arena``. It declares ``arena -> main_city``, and
    ``arena.challenge_list -> arena`` (a dialog *inside* arena), but nothing in
    ``main_city`` / ``main_menu`` leads in. ``arena.fight`` therefore carried its
    own ``exec: open_arena_via_city`` swipe route, whose failures were invisible
    (the ``exec:`` step traced ``ok`` regardless — see
    :func:`tasks.dsl_scenario_helpers._exec_result_failure_reason`), and the
    scenario is disabled today because that route stopped working.

    Only real navigation targets are checked (``priority >=
    MAIN_CITY_HUB_PRIORITY``); modals and popups are reached by dismissal
    scenarios, not routing. ``terminal: true`` does NOT exempt a screen here —
    that flag says "its own scenario taps out of it", which says nothing about
    getting in. Use ``entry: scenario`` on the ``screen_verify.yaml`` entry for a
    screen that is deliberately only ever reached by a scenario's own taps.

    Reachability is computed over the BUILT graph, not the raw YAML: the builder
    synthesizes per-building and per-hero edges that exist in no ``edge_taps.yaml``
    (``screen_graph._load_edge_taps``), and a check that reads only the files
    reports every per-building screen as a false positive.
    """
    from config.games import iter_games
    from navigation.screen_graph import MAIN_CITY_HUB_PRIORITY, graph_for_game

    # Per catalog, never pooled. A screen name only means something inside the
    # catalog that declares it, and the graph a worker walks is that catalog's
    # graph. Unioning them let kingshot's reachability be computed through wos
    # edges (and vice versa), which no runtime ever does.
    for catalog in iter_games(repo_root):
        screens = _collect_screen_verify_entries_for_catalog(repo_root, catalog)
        if not screens:
            continue
        try:
            _static, _dynamic, graph = graph_for_game(catalog)
        except Exception:
            logger.debug(
                "reachability: graph build failed catalog=%s", catalog, exc_info=True
            )
            continue
        adjacency = {str(src): {str(d) for d in dsts} for src, dsts in graph.items()}
        if not adjacency:
            continue

        reachable = {"main_city"}
        frontier = ["main_city"]
        while frontier:
            for nxt in adjacency.get(frontier.pop(), ()):
                if nxt not in reachable:
                    reachable.add(nxt)
                    frontier.append(nxt)

        _emit_unreachable(repo_root, issues, screens, reachable, MAIN_CITY_HUB_PRIORITY)


def _emit_unreachable(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    screens: dict[str, tuple[int, bool, str]],
    reachable: set[str],
    hub_priority: int,
) -> None:
    for name, (prio, _terminal, src_path) in sorted(screens.items()):
        if prio < hub_priority or name in reachable:
            continue
        if _screen_verify_entry_opts_out_of_reachability(repo_root, name):
            continue
        issues.append(
            StartupValidationIssue(
                # WARNING, not error: 14 screens fail this today (battle results,
                # event screens, popups reached only by their own scenario's taps),
                # and hard-failing the boot on a pre-existing backlog helps nobody.
                # Annotate the legitimate ones with `entry: scenario`; once the list
                # is only real gaps this can be promoted to "error".
                "warning",
                f"screen_verify:{name}",
                f"screen {name!r} is detectable in {src_path} but NO declared "
                "edge_taps.yaml edge reaches it from main_city — the navigator "
                "cannot route to it, so every scenario with this `node:` target "
                "fails with `navigation_failed` and the only way in is a "
                "hand-rolled gesture `exec:`. Declare an inbound edge (e.g. "
                "`main_city: {<screen>: [<region>]}`) or annotate the "
                "screen_verify entry with `entry: scenario` if it is only ever "
                "reached by its own scenario's taps.",
            )
        )


def _screen_verify_entry_opts_out_of_reachability(repo_root: Path, screen: str) -> bool:
    """Whether ``screen``'s ``screen_verify.yaml`` entry sets ``entry: scenario``."""
    for path in _screen_verify_yaml_paths(repo_root):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        screens = doc.get("screens")
        if not isinstance(screens, dict):
            continue
        entry = screens.get(screen)
        if isinstance(entry, dict) and str(entry.get("entry") or "").strip() == "scenario":
            return True
    return False



def _validate_module_manifests(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    """Unknown keys and dead paths in ``module.yaml``.

    Nothing checked manifests, and the cost was already paid: two of them
    declare ``area: ../../../area.json``, a file Phase 3 removed. It went
    unnoticed because ``area:`` is read by the wiki editor and ignored by
    discovery, so the only symptom was a wiki page pointing at nothing.

    Unknown keys are a warning, not an error: a manifest may legitimately carry
    a field this build does not know yet, and refusing to boot over it would be
    worse than the typo it catches.
    """
    from config.games import iter_module_catalogs
    from config.module_discovery import (
        KNOWN_MANIFEST_KEYS,
        iter_module_dirs,
        load_manifest,
    )

    seen: set[Path] = set()
    for catalog in iter_module_catalogs(repo_root):
        for module_dir in iter_module_dirs(repo_root, game=catalog):
            if module_dir in seen:
                continue
            seen.add(module_dir)
            manifest = load_manifest(module_dir)
            try:
                rel_module = module_dir.relative_to(repo_root).as_posix()
            except ValueError:
                rel_module = module_dir.as_posix()

            unknown = sorted(
                k for k, _v in manifest.raw if k not in KNOWN_MANIFEST_KEYS
            )
            if unknown:
                issues.append(
                    StartupValidationIssue(
                        "warning",
                        f"module.yaml:{rel_module}",
                        f"unknown manifest key(s) {unknown} — a typo, or a field "
                        "this build does not read; add it to "
                        "config.module_discovery.KNOWN_MANIFEST_KEYS if it is real",
                    )
                )

            # Declared paths must exist. Each is relative to the module dir.
            for field, value in (
                ("area", manifest.area),
                ("analyze", manifest.analyze),
                ("references", manifest.references),
                ("scenarios", manifest.scenarios),
                ("exec", manifest.exec_path),
                ("routes", manifest.routes),
            ):
                if not value:
                    continue
                target = (module_dir / value).resolve()
                if target.exists():
                    continue
                issues.append(
                    StartupValidationIssue(
                        "warning",
                        f"module.yaml:{rel_module}",
                        f"`{field}: {value}` points at a path that does not "
                        f"exist ({target}) — the consumer silently falls back "
                        "to its default, so the declaration is dead",
                    )
                )



def _validate_region_name_uniqueness(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    """Region names that resolve first-wins, so the later copy is dead config.

    ``layout.area_regions.validate_unique_region_names`` exists but checks one
    screen entry at a time and runs only from the dashboard's save path — never
    at load, never at boot. The lookup index (``area_lookup``) is first-wins
    across the merged doc, so a duplicate name silently makes the second
    declaration unreachable while it still looks live in the file.

    Two categories are NOT collisions and are excluded, or the warning would be
    noise nobody acts on:

    * an overlay redeclaring a base name (``games/wos/ru/**``) — that IS the
      overlay mechanism;
    * the same name in two different games — their catalogs never merge.
    """

    from config.games import iter_games

    def _is_overlay(rel: str) -> bool:
        return "/ru/" in rel or "/beta/" in rel

    known_games = set(iter_games(repo_root))
    owners: dict[str, set[str]] = {}
    for path in sorted(
        [*repo_root.glob("games/**/area.yaml"), *repo_root.glob("games/**/area.json")]
    ):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        rel = path.relative_to(repo_root).as_posix()
        seen_here: dict[str, int] = {}
        for screen in doc.get("screens") or []:
            if not isinstance(screen, dict):
                continue
            for region in screen.get("regions") or []:
                if not isinstance(region, dict):
                    continue
                name = str(region.get("name") or "").strip()
                if not name:
                    continue
                seen_here[name] = seen_here.get(name, 0) + 1
                owners.setdefault(name, set()).add(rel)

        for name, count in sorted(seen_here.items()):
            if count > 1:
                issues.append(
                    StartupValidationIssue(
                        "warning",
                        f"area:{rel}",
                        f"region {name!r} is declared {count}x in this file — "
                        "region lookup is first-wins, so every copy after the "
                        "first is unreachable config that still looks live",
                    )
                )

    for name, files in sorted(owners.items()):
        base = sorted(f for f in files if not _is_overlay(f))
        if len(base) < 2:
            continue
        if len({f.split("/")[1] for f in base} & known_games) > 1:
            continue
        issues.append(
            StartupValidationIssue(
                "warning",
                f"area:{name}",
                f"region {name!r} is declared in {len(base)} files of the same "
                f"catalog ({', '.join(base)}) — first-wins picks one and the "
                "others are dead; rename them apart",
            )
        )


def _validate_overlay_region_drift(
    repo_root: Path,
    issues: list[StartupValidationIssue],
) -> None:
    """An overlay region that changes nothing at all is a drift bomb.

    Region lookup is first-wins over the merged doc with overlay screens
    prepended, so an overlay only declares the regions it CHANGES — every base
    region on the same screen keeps resolving. Verified on the live tree:
    ``exit_confirm.body`` comes from ``games/wos/ru`` while
    ``exit_confirm.cancel`` / ``.close`` / ``.confirm`` still come from base.

    **The override is not always in the region dict.** Both RU regions whose
    dicts match base verbatim — ``exit_confirm.body`` and ``chapter.title`` —
    are real overrides: the geometry is deliberately unchanged and the RU build
    ships a different template CROP at the same relative path (6442 vs 6991
    bytes for ``chapter_chapter.title.png``). Comparing dicts alone would tell
    an operator to delete the one thing making RU screen detection work, so this
    compares the resolved crop bytes too and only fires when BOTH match.

    Deliberately not reported: an overlay region with no base counterpart.
    ``upgrade_button_top`` exists only for the RU build and
    ``building.upgrade.yaml`` leans on that on purpose — "EN has no
    ``upgrade_button_top`` region → the block resolves no row and is skipped".
    """
    from pathlib import Path as _Path

    from config.games import iter_games
    from layout.crop_paths import exported_crop_png

    def _overlay_root(rel: str) -> str:
        """``games/wos/ru/core/x/area.yaml`` → ``games/wos/ru``, else ``""``."""
        parts = rel.split("/")
        for i, part in enumerate(parts[:3]):
            if part in {"ru", "beta"} and i >= 2:
                return "/".join(parts[: i + 1])
        return ""

    def _crop_bytes(rel_area: str, ocr_ref: str, region_name: str) -> bytes | None:
        """The template PNG this declaration actually matches against."""
        if not ocr_ref:
            return None
        module_root = _Path(rel_area).parent
        ref_rel = (module_root / ocr_ref).as_posix()
        crop = exported_crop_png(repo_root, ref_rel, region_name)
        try:
            return crop.read_bytes()
        except OSError:
            return None

    known_games = set(iter_games(repo_root))
    base: dict[str, dict[str, tuple[str, dict[str, Any], str]]] = {}
    overlay: list[tuple[str, str, str, dict[str, Any], str]] = []
    for path in sorted(
        [*repo_root.glob("games/**/area.yaml"), *repo_root.glob("games/**/area.json")]
    ):
        doc = _load_yaml_dict(path)
        if "__load_error__" in doc:
            continue
        rel = path.relative_to(repo_root).as_posix()
        parts = rel.split("/")
        if len(parts) < 3 or parts[1] not in known_games:
            continue
        game, ov = parts[1], _overlay_root(rel)
        for screen in doc.get("screens") or []:
            if not isinstance(screen, dict):
                continue
            ocr_ref = str(screen.get("ocr") or "").strip()
            for region in screen.get("regions") or []:
                if not isinstance(region, dict):
                    continue
                name = str(region.get("name") or "").strip()
                if not name:
                    continue
                if ov:
                    overlay.append((ov, rel, name, region, ocr_ref))
                else:
                    base.setdefault(game, {}).setdefault(name, (rel, region, ocr_ref))

    for ov, rel, name, region, ocr_ref in overlay:
        found = base.get(ov.split("/")[1], {}).get(name)
        if found is None:
            continue
        base_rel, base_region, base_ocr = found
        if _canonical_region(region) != _canonical_region(base_region):
            continue
        if _crop_bytes(rel, ocr_ref, name) != _crop_bytes(base_rel, base_ocr, name):
            continue
        issues.append(
            StartupValidationIssue(
                "warning",
                f"area:{rel}",
                f"overlay region {name!r} has the same geometry AND the same crop as "
                f"the one it shadows in {base_rel} — it overrides nothing, and it "
                "will hold the stale values the day the base one is retuned; delete "
                "it and let the base region resolve",
            )
        )


def _canonical_region(region: dict[str, Any]) -> str:
    """A region's content, ignoring key order and authoring-only annotations."""
    import json

    stripped = {
        k: v
        for k, v in region.items()
        if not k.startswith("_") and k not in {"aliases", "comment"}
    }
    return json.dumps(stripped, sort_keys=True, ensure_ascii=False)


def validate_startup_configs(repo_root: Path | None = None) -> list[StartupValidationIssue]:
    from config.games import iter_module_catalogs

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

    _validate_edge_taps(root, issues)
    _validate_screen_family_route_gaps(root, issues)
    _validate_dead_end_screens(root, issues)
    _validate_unreachable_screens(root, issues)
    _validate_cron_specs(root, issues)
    _validate_module_manifests(root, issues)
    _validate_region_name_uniqueness(root, issues)
    _validate_overlay_region_drift(root, issues)
    # Per catalog: an overlay ships real rules against its own regions, and the
    # region set differs per catalog, so checking a rule against the union would
    # be both too lax (a base rule using an RU-only region would pass) and
    # mislabelled. Duplicate findings from the shared base tree collapse in
    # `_dedupe_issues`.
    for _catalog in iter_module_catalogs(root):
        # Names AND capabilities must come from the SAME catalog document.
        # Mixing them (per-catalog names against the union's capability set)
        # invents errors: kingshot's `mail.tab.wars.red_dot` is a real red-dot
        # region in kingshot's area doc and absent from wos's, so the rule was
        # reported as targeting a region with no capability.
        try:
            _catalog_doc = load_area_doc(root, game=_catalog)
        except Exception:
            logger.debug("analyze validation: area load failed catalog=%s", _catalog)
            continue
        _validate_analyze_manifest(
            root,
            issues,
            region_names=_area_region_names(_catalog_doc),
            red_dot_regions=_area_regions_with_red_dot_capability(_catalog_doc),
            catalog=_catalog,
        )
    _validate_overlay_runtime_area_manifest(root, issues)
    _validate_area_exist_region_sources(root, issues)
    _validate_scenarios(
        root,
        issues,
        region_names=region_names,
        red_dot_regions=red_dot_regions,
        text_search_regions=text_search_regions,
    )
    return _dedupe_issues(issues)


def _dedupe_issues(
    issues: list[StartupValidationIssue],
) -> list[StartupValidationIssue]:
    """Collapse byte-identical findings, preserving order.

    Catalogs share ~99% of their module tree, so a check that runs per catalog
    reports the same defect in the same shared file once per catalog. Identity is
    the finding itself — ``(severity, source, message)`` — not the catalog that
    happened to surface it.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[StartupValidationIssue] = []
    for issue in issues:
        key = (issue.severity, issue.source, issue.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def log_startup_config_validation(repo_root: Path | None = None) -> list[StartupValidationIssue]:
    issues = validate_startup_configs(repo_root)
    if not issues:
        logger.info("startup config validation: ok")
        _publish_startup_validation_failures([])
        return []

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity != "error")
    log_summary = logger.error if error_count else logger.warning
    log_summary(
        "startup config validation: %d error(s), %d warning(s) found",
        error_count,
        warning_count,
    )
    for issue in issues:
        log = logger.error if issue.severity == "error" else logger.warning
        log("startup config validation: [%s] %s: %s", issue.severity, issue.source, issue.message)
    _publish_startup_validation_failures(issues)
    return issues


def _format_startup_validation_trace(issues: list[StartupValidationIssue]) -> str:
    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity != "error")
    lines = [
        "startup config validation: "
        f"{error_count} error(s), {warning_count} warning(s) found"
    ]
    lines.extend(
        f"[{issue.severity}] {issue.source}: {issue.message}" for issue in issues
    )
    if error_count:
        lines.append(f"override: set {_ACK_ENV_VAR}=1")
    return "\n".join(lines)


def _publish_startup_validation_failures(issues: list[StartupValidationIssue]) -> None:
    """Expose startup validation failures to the dashboard attention banner."""
    try:
        import redis

        from config.loader import load_settings
        from dashboard.load_failures import record_load_failures

        settings = load_settings()
        client = redis.Redis.from_url(
            settings.redis.url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        try:
            trace = _format_startup_validation_trace(issues) if issues else ""
            failures = [
                {
                    "file": issue.source,
                    "error": issue.message,
                    "severity": issue.severity,
                    "ts": time.time(),
                    "trace": trace,
                }
                for issue in issues
            ]
            record_load_failures(client, "startup_validation", failures)
        finally:
            client.close()
    except Exception:
        logger.debug("startup validation dashboard publish skipped", exc_info=True)


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
    errors = [issue for issue in issues if issue.severity == "error"]
    if not errors:
        return

    if _validation_ack_via_env():
        logger.warning(
            "startup config validation: %d error(s) acknowledged via %s — continuing",
            len(errors),
            _ACK_ENV_VAR,
        )
        return

    msg = (
        f"startup config validation failed: {len(errors)} error(s), "
        f"{len(issues) - len(errors)} warning(s). "
        f"Fix the modules above or set {_ACK_ENV_VAR}=1 to override."
    )
    raise RuntimeError(msg)
