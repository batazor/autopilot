"""Per-module analyzer breakdown + pushScenario dry-run candidates."""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from analysis.overlay import run_overlay_analysis
from analysis.overlay_manifest import load_merged_analyze_yaml
from api.services.overlay_test.types import ModuleAnalyzerRun, PushScenarioCandidate

if TYPE_CHECKING:
    import numpy as np


def _push_targets(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("pushScenario")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("type") or "").strip()
        if name:
            out.append(name)
    return out


def _push_skip_reason(
    target: str,
    *,
    repo: Any,
    active_player: str,
    current_screen: str,
) -> str:
    from dsl.dsl_schema import dsl_scenario_yaml_device_level, dsl_scenario_yaml_enabled

    if dsl_scenario_yaml_enabled(repo, target) is False:
        return "disabled"
    if not dsl_scenario_yaml_device_level(repo, target) and not active_player.strip():
        return "no_active_player"
    if "${hero_id}" in target and not current_screen.startswith("page.heroes."):
        return "no_hero_id"
    return ""


def _collect_push_candidates(
    results: dict[str, Any],
    *,
    repo: Any,
    active_player: str,
    current_screen: str,
) -> list[PushScenarioCandidate]:
    """Dry-run overlay ``pushScenario`` selection (same priority pick as the worker)."""
    from worker.instance_worker_overlay import _overlay_push_priority

    push_payloads: list[tuple[int, int, str, dict[str, Any]]] = []
    for order, (rule_name, payload) in enumerate(results.items()):
        if not isinstance(payload, dict) or not payload.get("matched"):
            continue
        priority = _overlay_push_priority(payload)
        if priority is not None:
            push_payloads.append((priority, order, str(rule_name), payload))

    selected_rule: str | None = None
    rule_blocked_sibling: dict[str, str] = {}
    for _priority, _order, rule_name, payload in sorted(
        push_payloads, key=lambda it: (-it[0], it[1])
    ):
        targets = _push_targets(payload)
        if not targets:
            continue
        sibling_skip = ""
        for target in targets:
            reason = _push_skip_reason(
                target,
                repo=repo,
                active_player=active_player,
                current_screen=current_screen,
            )
            if reason:
                sibling_skip = f"sibling_blocked:{target}={reason}"
                break
        if sibling_skip:
            rule_blocked_sibling[rule_name] = sibling_skip
            continue
        selected_rule = rule_name
        break

    out: list[PushScenarioCandidate] = []
    for _priority, _order, rule_name, payload in sorted(
        push_payloads, key=lambda it: (-it[0], it[1])
    ):
        region = str(payload.get("region") or "").strip()
        priority = _overlay_push_priority(payload) or 0
        for target in _push_targets(payload):
            skip_reason = _push_skip_reason(
                target,
                repo=repo,
                active_player=active_player,
                current_screen=current_screen,
            )
            is_selected = rule_name == selected_rule and not skip_reason
            if not skip_reason and not is_selected:
                if selected_rule is not None:
                    skip_reason = f"lost_to={selected_rule}"
                elif rule_name in rule_blocked_sibling:
                    skip_reason = rule_blocked_sibling[rule_name]
            out.append(
                PushScenarioCandidate(
                    scenario=target,
                    rule=rule_name,
                    region=region,
                    priority=priority,
                    selected=is_selected,
                    skip_reason=skip_reason,
                )
            )
    return out


def _module_has_overlay_rules(
    repo: Any,
    scope: str,
    *,
    device_level_only: bool = False,
) -> bool:
    """True when the module scope has overlay rules that would run in this mode."""
    merged = load_merged_analyze_yaml(repo, module_scope=scope)
    overlay = merged.get("overlay")
    if not isinstance(overlay, list):
        return False
    rules = [r for r in overlay if isinstance(r, dict)]
    if not rules:
        return False
    if not device_level_only:
        return True
    return any(r.get("device_level") is True for r in rules)


async def _run_module_analyzer_breakdown_async(
    image_bgr: np.ndarray,
    *,
    repo: Any,
    area_doc: dict[str, Any],
    current_screen: str | None,
    state_flat: dict[str, Any] | None,
    instance_id: str | None,
    device_level_only: bool = False,
) -> list[ModuleAnalyzerRun]:
    from config.module_discovery import load_module_yaml, module_meta_id, module_storage_key
    from dsl.registry import iter_module_analyze_manifests

    async def _run_one(module_id: str, label: str, scope: str) -> ModuleAnalyzerRun:
        t0 = time.perf_counter()
        results = await run_overlay_analysis(
            image_bgr,
            repo_root=repo,
            area_doc=area_doc,
            current_screen=current_screen,
            state_flat=state_flat,
            module_scope=scope,
            instance_id=instance_id,
            device_level_only=device_level_only,
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        matched_count = sum(
            1 for p in results.values() if isinstance(p, dict) and p.get("matched")
        )
        return ModuleAnalyzerRun(
            module_id=module_id,
            label=label,
            duration_ms=duration_ms,
            rule_count=len(results),
            matched_count=matched_count,
        )

    runs: list[ModuleAnalyzerRun] = []
    pending: list[tuple[int, asyncio.Task[ModuleAnalyzerRun]]] = []
    for manifest in iter_module_analyze_manifests(repo):
        module_dir = manifest.parent.parent
        module_id = module_meta_id(module_dir)
        meta = load_module_yaml(module_dir)
        label = str(meta.get("title") or module_id).strip() or module_id
        scope = module_storage_key(module_dir, repo)
        if not _module_has_overlay_rules(repo, scope, device_level_only=device_level_only):
            runs.append(
                ModuleAnalyzerRun(
                    module_id=module_id,
                    label=label,
                    duration_ms=0,
                    rule_count=0,
                    matched_count=0,
                )
            )
            continue
        runs.append(
            ModuleAnalyzerRun(
                module_id=module_id, label=label, duration_ms=0, rule_count=0, matched_count=0
            )
        )
        pending.append((len(runs) - 1, asyncio.create_task(_run_one(module_id, label, scope))))

    for slot, task in pending:
        runs[slot] = await task
    return runs


def _run_module_analyzer_breakdown(
    image_bgr: np.ndarray,
    *,
    repo: Any,
    area_doc: dict[str, Any],
    current_screen: str | None,
    state_flat: dict[str, Any] | None,
    instance_id: str | None,
    device_level_only: bool = False,
) -> tuple[list[ModuleAnalyzerRun], int]:
    modules_started = time.perf_counter()
    runs = asyncio.run(
        _run_module_analyzer_breakdown_async(
            image_bgr,
            repo=repo,
            area_doc=area_doc,
            current_screen=current_screen,
            state_flat=state_flat,
            instance_id=instance_id,
            device_level_only=device_level_only,
        )
    )
    modules_total_ms = int((time.perf_counter() - modules_started) * 1000)
    return runs, modules_total_ms
