"""Pydantic schema for DSL scenarios under ``scenarios/``.

Mirrors the runtime executor in ``tasks/dsl_scenario.py``. The action key set is
authoritative there (``_DSL_STEP_ACTION_KEYS``); this schema covers the same set
plus ``cond`` guards, composite ``cond`` blocks, ``if``, ``break``, ``long_click``
and the legacy ``action`` key. Models use ``extra="allow"`` so round-tripping
existing YAML never silently drops fields the executor still understands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_SCENARIO_PRIORITY = 80_000
"""Queue priority assigned to any DSL scenario that doesn't carry an explicit
``priority`` of its own — used uniformly across all three enqueue paths
(overlay push, cron, hand-pointer resume).

The unification is deliberate: a scenario's importance is a property of the
work it does, not of how it got scheduled. Treating cron-scheduled scenarios
as "lower-tier housekeeping by default" routinely starved them under overlay
load (e.g. ``claim_exploration_rewards`` getting cooperatively preempted on
its first step every cron tick). Scenarios that intentionally want to defer
to interactive work still set an explicit lower ``priority`` in their YAML.
"""


DSL_ACTION_KEYS: tuple[str, ...] = (
    "click",
    "long_click",
    "match",
    "while_match",
    "ocr",
    "set_node",
    "swipe_direction",
    "push_scenario",
    "exec",
    "wait",
    "repeat",
    "loop",
    "if",
    "break",
    "action",
)

COMPOSITE_KEYS: tuple[str, ...] = ("cond",)


class DslStep(BaseModel):
    """One DSL step. Exactly one action key is expected, plus optional ``cond``.

    Two action-less forms are also valid, matching the runtime executor in
    ``tasks/dsl_scenario.py``:

    * **Composite ``cond``** — a ``cond`` guard plus nested ``steps``: the
      group runs when the condition holds, else is skipped wholesale.
    * **Bare group** — only ``steps`` with no action key and no ``cond``: the
      runtime simply iterates the inner steps inline (see
      ``dsl_scenario.py`` grouped-step handler). Useful for inlining a
      YAML anchor that points to a list of steps.

    Unknown keys are preserved (``extra="allow"``).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    cond: str | None = None

    click: str | None = None
    long_click: str | None = None
    match: str | None = None
    while_match: str | None = None
    ocr: str | None = None
    set_node: str | None = None
    exec: str | None = None
    wait: str | int | float | None = None

    push_scenario: str | dict[str, Any] | None = None
    swipe_direction: dict[str, Any] | None = None
    repeat: dict[str, Any] | int | None = None
    loop: dict[str, Any] | None = None

    if_: str | None = Field(default=None, alias="if")
    break_: str | None = Field(default=None, alias="break")
    action: str | None = None

    steps: list[DslStep] | None = None

    threshold: float | None = None
    min_match_saturation: int | None = None
    max: int | None = None
    min: int | None = None

    @model_validator(mode="after")
    def _exactly_one_action(self) -> DslStep:
        present = [k for k in DSL_ACTION_KEYS if self._has(k)]
        # Action-less group: must carry ``steps`` (optionally with ``cond``).
        # The runtime grouped-step handler iterates these inline.
        is_group = (
            not present
            and isinstance(self.steps, list)
            and bool(self.steps)
        )
        if is_group:
            return self
        if not present:
            raise ValueError(
                "step must carry exactly one action key "
                f"(one of {', '.join(DSL_ACTION_KEYS)}) or a non-empty 'steps' "
                "group (optionally guarded by 'cond')"
            )
        if len(present) > 1:
            raise ValueError(
                f"step carries multiple action keys: {', '.join(present)} — "
                "split into separate steps"
            )
        return self

    def _has(self, key: str) -> bool:
        if key == "if":
            return self.if_ is not None
        if key == "break":
            return self.break_ is not None
        return getattr(self, key, None) is not None

    def step_type(self) -> str:
        for k in DSL_ACTION_KEYS:
            if self._has(k):
                return k
        if self.cond is not None and self.steps is not None:
            return "cond"
        if isinstance(self.steps, list) and self.steps:
            return "group"
        return ""


DslStep.model_rebuild()


class DslScenario(BaseModel):
    """Top-level scenario YAML."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    enabled: bool = False
    device_level: bool = False
    priority: int | None = None
    cron: str | None = None
    node: str | None = None
    cond: str | None = None
    steps: list[DslStep] = Field(default_factory=list)


def parse_scenario(raw: dict[str, Any] | None) -> DslScenario:
    """Parse a YAML-loaded mapping into a ``DslScenario``."""
    if not isinstance(raw, dict):
        raise TypeError(f"scenario root must be a mapping, got {type(raw).__name__}")
    return DslScenario.model_validate(raw)


def dump_scenario(scenario: DslScenario) -> dict[str, Any]:
    """Serialize a scenario back to a plain dict suitable for ``yaml.safe_dump``.

    Preserves only keys whose values are not ``None``/empty defaults so the YAML
    stays as compact as the originals.
    """
    raw = scenario.model_dump(by_alias=True, exclude_none=True)
    return _strip_defaults(raw)


def _strip_defaults(d: Any) -> Any:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            if v is None:
                continue
            if k in {"enabled", "device_level"} and v is False:
                continue
            v2 = _strip_defaults(v)
            if v2 in (None, [], {}):
                if k == "steps":
                    out[k] = []
                continue
            out[k] = v2
        return out
    if isinstance(d, list):
        return [_strip_defaults(x) for x in d]
    return d


def resolve_dsl_scenario_yaml_path(repo_root: Path, scenario_key: str) -> Path | None:
    """First ``scenarios/**/{scenario_key}.yaml`` excluding ``drafts/`` (deterministic)."""
    key = str(scenario_key or "").strip()
    if not key:
        return None
    scenarios_root = repo_root / "scenarios"
    if not scenarios_root.is_dir():
        return None
    hits: list[Path] = []
    for p in scenarios_root.rglob(f"{key}.yaml"):
        rel = p.relative_to(scenarios_root).as_posix()
        if rel.startswith("drafts/"):
            continue
        hits.append(p)
    if not hits:
        return None
    hits.sort(key=lambda p: (len(p.relative_to(scenarios_root).parts), p.as_posix()))
    return hits[0]


def dsl_scenario_yaml_priority(repo_root: Path, scenario_key: str) -> int | None:
    """Top-level ``priority`` from the scenario YAML file, if set and integral."""
    path = resolve_dsl_scenario_yaml_path(repo_root, scenario_key)
    if path is None or not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    p = raw.get("priority")
    if p is None or isinstance(p, bool):
        return None
    try:
        return int(p)
    except (TypeError, ValueError):
        return None
