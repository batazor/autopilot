"""Overlay analyze audit API."""
from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Any

import yaml

from config.module_registry import normalize_module_scope
from config.paths import repo_root
from dashboard.overlay_analyze_audit import area_doc_for_module_scope, audit_overlay_rules
from dsl.registry import iter_module_analyze_manifests

_REPO = repo_root()


def _load_rules(manifest: Path) -> list[dict[str, Any]]:
    try:
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    rules = raw.get("regions") if isinstance(raw, dict) else None
    if not isinstance(rules, list):
        return []
    return [r for r in rules if isinstance(r, dict)]


def audit_scope(*, module_scope: str = "all") -> dict[str, Any]:
    scope = normalize_module_scope(module_scope)
    area_doc = area_doc_for_module_scope(_REPO, scope)
    manifests = iter_module_analyze_manifests(_REPO, scope)
    issues: list[dict[str, Any]] = []
    for manifest in manifests:
        rel = manifest.relative_to(_REPO).as_posix()
        rules = _load_rules(manifest)
        issues.extend(
            {
                "manifest": rel,
                "rule": issue.rule_name,
                "severity": issue.severity,
                "source": issue.source,
                "message": issue.message,
            }
            for issue in audit_overlay_rules(area_doc, rules, repo_root_path=_REPO)
        )
    return {
        "scope": scope,
        "manifest_count": len(manifests),
        "issue_count": len(issues),
        "issues": issues,
    }
