from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from config.paths import repo_root
from dsl.registry import iter_scenario_yaml_files

if TYPE_CHECKING:
    from pathlib import Path

TARGET_REGION = "tapanywhereyoexit"


def _has_expected(step: dict[str, Any]) -> bool:
    expected = step.get("expected")
    if isinstance(expected, str):
        return bool(expected.strip())
    if isinstance(expected, list):
        return any(str(item).strip() for item in expected)
    return False


def _walk_steps(
    steps: Any,
    *,
    path: Path,
    source: str,
    guarded_by_target: bool = False,
) -> list[str]:
    if not isinstance(steps, list):
        return []

    issues: list[str] = []
    for index, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            continue

        step = raw_step
        location = f"{path.relative_to(repo_root())}:{source}[{index}]"
        is_target_guard = step.get("while_match") == TARGET_REGION
        if is_target_guard and not _has_expected(step):
            issues.append(f"{location}: while_match {TARGET_REGION} missing expected")

        if step.get("click") == TARGET_REGION and not guarded_by_target:
            issues.append(f"{location}: click {TARGET_REGION} outside while_match")

        child_guarded = guarded_by_target or is_target_guard
        issues.extend(
            _walk_steps(
                step.get("steps"),
                path=path,
                source=f"{source}[{index}].steps",
                guarded_by_target=child_guarded,
            )
        )
        issues.extend(
            _walk_steps(
                step.get("else"),
                path=path,
                source=f"{source}[{index}].else",
                guarded_by_target=guarded_by_target,
            )
        )

        repeat = step.get("repeat")
        if isinstance(repeat, dict):
            issues.extend(
                _walk_steps(
                    repeat.get("steps"),
                    path=path,
                    source=f"{source}[{index}].repeat.steps",
                    guarded_by_target=child_guarded,
                )
            )

    return issues


def test_tapanywhereyoexit_clicks_are_guarded_by_while_match() -> None:
    issues: list[str] = []

    for _root, path in iter_scenario_yaml_files(repo_root()):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        issues.extend(_walk_steps(doc.get("steps"), path=path, source="steps"))

    assert issues == []
