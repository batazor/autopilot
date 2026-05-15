"""Overlay rule ↔ area.json checks for Wiki · Analyze (shared with startup validation)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from analysis.overlay_rules import optional_push_scenario_tasks
from config.module_registry import (
    ALL_MODULES_KEY,
    CORE_MODULE_KEY,
    get_wiki_module,
    merge_all_area_docs,
    normalize_module_scope,
)
from config.paths import repo_root
from config.startup_validation import (
    StartupValidationIssue,
    _area_region_names,
    _area_regions_with_red_dot_capability,
    _check_red_dot_capability,
    _check_region,
    _check_scenario,
    _rule_uses_red_dot,
)


@dataclass(frozen=True)
class OverlayAuditIssue:
    severity: str
    rule_name: str
    source: str
    message: str


def area_doc_for_module_scope(repo_root_path: Path, module_scope: str) -> dict[str, Any]:
    """Area manifest used to validate overlay regions for the active scope."""
    scope = normalize_module_scope(module_scope)
    root = repo_root_path.resolve()
    if scope == ALL_MODULES_KEY:
        return merge_all_area_docs(root)
    if scope == CORE_MODULE_KEY:
        path = root / "area.json"
        if not path.is_file():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    ctx = get_wiki_module(root, scope)
    path = ctx.area_path
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _issue_from_startup(
    issue: StartupValidationIssue,
    *,
    rule_name: str,
) -> OverlayAuditIssue:
    return OverlayAuditIssue(
        severity=issue.severity,
        rule_name=rule_name,
        source=issue.source,
        message=issue.message,
    )


def audit_overlay_rule(
    rule: dict[str, Any],
    *,
    region_names: set[str],
    red_dot_regions: set[str],
    repo_root_path: Path,
    rule_name: str,
) -> list[OverlayAuditIssue]:
    """Run the same overlay checks as startup validation for one rule."""
    issues: list[StartupValidationIssue] = []
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
            repo_root=repo_root_path,
            source=source,
            field="pushScenario",
            value=task.get("dsl_scenario") or task.get("type"),
        )

    action = str(rule.get("action") or "").strip()
    if action == "exist":
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                "overlay YAML must use `findIcon`, not `exist` (exist is for area.json only)",
            )
        )
    if not rule_name or rule_name == "(unnamed)":
        issues.append(
            StartupValidationIssue(
                "error",
                source,
                "overlay rule is missing a non-empty `name`",
            )
        )

    return [_issue_from_startup(i, rule_name=rule_name) for i in issues]


def audit_overlay_rules(
    area_doc: dict[str, Any],
    rules: list[dict[str, Any]],
    *,
    repo_root_path: Path | None = None,
) -> list[OverlayAuditIssue]:
    """Validate every overlay rule against ``area_doc`` region names."""
    root = (repo_root_path or repo_root()).resolve()
    region_names = _area_region_names(area_doc)
    red_dot_regions = _area_regions_with_red_dot_capability(area_doc)
    out: list[OverlayAuditIssue] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_name = str(rule.get("name") or "").strip() or "(unnamed)"
        out.extend(
            audit_overlay_rule(
                rule,
                region_names=region_names,
                red_dot_regions=red_dot_regions,
                repo_root_path=root,
                rule_name=rule_name,
            )
        )
    return out
