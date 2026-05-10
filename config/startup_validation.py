from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from analysis.overlay_manifest import default_analyze_yaml_path, load_analyze_yaml
from analysis.overlay_rules import optional_push_scenario_tasks

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
                name = str(reg.get("name") or "").strip()
                if name:
                    out.add(name)
    return out


def _scenario_keys(scenarios_root: Path) -> set[str]:
    out: set[str] = set()
    if not scenarios_root.is_dir():
        return out
    for path in scenarios_root.rglob("*.yaml"):
        rel = path.relative_to(scenarios_root).as_posix()
        if rel.startswith("drafts/"):
            continue
        out.add(path.stem)
    return out


def _scenario_exists(scenario_keys: set[str], name: str) -> bool:
    return str(name or "").strip() in scenario_keys


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
    scenario_keys: set[str],
    source: str,
    field: str,
    value: Any,
) -> None:
    name = str(value or "").strip()
    if name and not _scenario_exists(scenario_keys, name):
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                f"{field} references missing scenario {name!r}",
            )
        )


def _validate_analyze_manifest(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
    scenario_keys: set[str],
) -> None:
    manifest_path = default_analyze_yaml_path(repo_root)
    if not manifest_path.is_file():
        issues.append(
            StartupValidationIssue("error", manifest_path.as_posix(), "analyze manifest not found")
        )
        return

    manifest = _load_yaml_dict(manifest_path)
    if "__load_error__" in manifest:
        issues.append(
            StartupValidationIssue(
                "error",
                manifest_path.as_posix(),
                f"cannot parse YAML: {manifest['__load_error__']}",
            )
        )
        return

    includes = manifest.get("include")
    if isinstance(includes, list):
        for item in includes:
            rel = str(item or "").strip()
            if not rel:
                continue
            inc_path = Path(rel)
            if not inc_path.is_absolute():
                inc_path = manifest_path.parent / inc_path
            if not inc_path.is_file():
                issues.append(
                    StartupValidationIssue(
                        "error",
                        manifest_path.as_posix(),
                        f"include references missing analyzer {rel!r}",
                    )
                )

    analyze_doc = load_analyze_yaml(manifest_path)
    overlay = analyze_doc.get("overlay")
    if not isinstance(overlay, list):
        issues.append(
            StartupValidationIssue("error", manifest_path.as_posix(), "analyze overlay is missing")
        )
        return

    for idx, rule in enumerate(overlay):
        if not isinstance(rule, dict):
            continue
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
        for task in optional_push_scenario_tasks(rule):
            _check_scenario(
                issues,
                scenario_keys=scenario_keys,
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
    region_names: set[str],
    scenario_keys: set[str],
) -> None:
    if not isinstance(steps, list):
        return
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
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
                region_names=region_names,
                scenario_keys=scenario_keys,
            )

        if "push_scenario" in step:
            spec = step.get("push_scenario")
            name = spec.get("name") if isinstance(spec, dict) else spec
            _check_scenario(
                issues,
                scenario_keys=scenario_keys,
                source=step_source,
                field="push_scenario",
                value=name,
            )

        _walk_steps(
            step.get("steps"),
            source=step_source,
            issues=issues,
            region_names=region_names,
            scenario_keys=scenario_keys,
        )


def _validate_scenarios(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
    scenario_keys: set[str],
) -> None:
    scenarios_root = repo_root / "scenarios"
    if not scenarios_root.is_dir():
        issues.append(
            StartupValidationIssue(
                "error",
                scenarios_root.as_posix(),
                "scenarios directory not found",
            )
        )
        return

    for path in sorted(scenarios_root.rglob("*.yaml")):
        rel = path.relative_to(scenarios_root).as_posix()
        if rel.startswith("drafts/"):
            continue
        doc = _load_yaml_dict(path)
        source = f"scenario:{rel}"
        if "__load_error__" in doc:
            issues.append(
                StartupValidationIssue(
                    "error",
                    source,
                    f"cannot parse YAML: {doc['__load_error__']}",
                )
            )
            continue
        _walk_steps(
            doc.get("steps"),
            source=source,
            issues=issues,
            region_names=region_names,
            scenario_keys=scenario_keys,
        )


def _validate_edge_taps(
    repo_root: Path,
    issues: list[StartupValidationIssue],
    *,
    region_names: set[str],
) -> None:
    path = repo_root / "navigation" / "edge_taps.yaml"
    if not path.is_file():
        issues.append(
            StartupValidationIssue("error", path.as_posix(), "navigation edge_taps.yaml not found")
        )
        return

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
            else:
                issues.append(
                    StartupValidationIssue(
                        "error",
                        source,
                        "tap sequence must be a region name or list of region names",
                    )
                )
                continue
            for tap in tap_names:
                _check_region(
                    issues,
                    region_names=region_names,
                    source=source,
                    field="tap",
                    value=tap,
                )


def validate_startup_configs(repo_root: Path | None = None) -> list[StartupValidationIssue]:
    root = (repo_root or Path(__file__).resolve().parent.parent).resolve()
    issues: list[StartupValidationIssue] = []

    area_path = root / "area.json"
    area_doc = _load_yaml_dict(area_path) if area_path.is_file() else {}
    if not area_path.is_file():
        issues.append(StartupValidationIssue("error", area_path.as_posix(), "area.json not found"))
    elif "__load_error__" in area_doc:
        issues.append(
            StartupValidationIssue(
                "error",
                area_path.as_posix(),
                f"cannot parse area.json: {area_doc['__load_error__']}",
            )
        )

    region_names = _area_region_names(area_doc)
    scenario_keys = _scenario_keys(root / "scenarios")

    _validate_edge_taps(root, issues, region_names=region_names)
    _validate_analyze_manifest(
        root,
        issues,
        region_names=region_names,
        scenario_keys=scenario_keys,
    )
    _validate_scenarios(
        root,
        issues,
        region_names=region_names,
        scenario_keys=scenario_keys,
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


def assert_startup_configs_valid(repo_root: Path | None = None) -> None:
    issues = log_startup_config_validation(repo_root)
    if issues:
        raise RuntimeError(f"startup config validation failed: {len(issues)} issue(s)")
