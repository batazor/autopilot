"""Pydantic schema for module-owned DSL scenario YAMLs.

Mirrors the runtime executor in ``tasks/dsl_scenario.py``. The action key set is
authoritative there (``_DSL_STEP_ACTION_KEYS``); this schema covers the same set
plus ``cond`` guards, composite ``cond`` blocks, ``break``, and ``long_click``.
Models use ``extra="allow"`` so round-tripping existing YAML never silently drops
fields the executor still understands.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from pathlib import Path

OCR_SCOPES: tuple[str, ...] = ("player", "instance")
"""Valid values for the ``ocr: scope:`` field. A typo (``instnace``) used to
fall back to ``player`` silently — runtime warning only — which corrupted
both the write target and the scenario-start cleanup. Editor + runtime now
both gate on this allow-list (see :class:`DslStep` and
:func:`validate_dsl_steps`)."""

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
    "swipe_direction",
    "push_scenario",
    "exec",
    "wait",
    "ttl",
    "repeat",
    "loop",
    "break",
    "system_back",
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
    exec: str | None = None
    wait: str | int | float | None = None
    # ``ttl:`` exits the scenario early and reschedules it for ``now + ttl``.
    # Accepts the same duration grammar as ``wait:`` plus ``m`` / ``h``
    # (e.g. ``"30m"``, ``"2h"``). Used inside ``while_match.else`` to back off
    # when a popup never appeared, without aborting the queue position.
    ttl: str | int | float | None = None

    push_scenario: str | dict[str, Any] | None = None
    swipe_direction: dict[str, Any] | None = None
    # Typed specs so nested ``steps`` go through ``DslStep`` validation —
    # otherwise inner step shapes (e.g. ``long_click + wait`` as duration)
    # slip past the schema even though the runtime executes them.
    repeat: RepeatSpec | int | None = None
    loop: LoopSpec | None = None

    break_: str | None = Field(default=None, alias="break")
    system_back: bool | None = None

    steps: list[DslStep] | None = None
    # ``while_match`` only: steps to run when the probe finds zero iterations
    # (icon was never visible). Lets a scenario specify a fallback — e.g.
    # set a TTL, push another scenario, or log a marker — instead of silently
    # falling through to the next top-level step (or rescheduling via strict).
    else_: list[DslStep] | None = Field(default=None, alias="else")

    threshold: float | None = None
    min_match_saturation: int | None = None
    max: int | None = None
    min: int | None = None
    # OCR storage scope — ``player`` (default) writes to the player state
    # hash; ``instance`` writes to the instance state hash. Typed strictly
    # so a typo (``scope: instnace``) fails parse instead of falling back
    # to ``player`` and silently writing to the wrong key.
    scope: Literal["player", "instance"] | None = None

    @model_validator(mode="after")
    def _exactly_one_action(self) -> DslStep:
        if "set_node" in (self.model_extra or {}):
            msg = (
                "set_node is no longer a DSL action; screen state is detected "
                "automatically"
            )
            raise ValueError(
                msg
            )
        present = [k for k in DSL_ACTION_KEYS if self._has(k)]
        # ``long_click`` consumes the step-level ``wait`` (or ``duration``) as
        # its long-press time — see the runtime handler in
        # ``tasks/dsl_scenario_inline_mixin.py:483``. The combination is one
        # action, so don't count ``wait`` as a competing key.
        if "long_click" in present and "wait" in present:
            present = [k for k in present if k != "wait"]
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
            msg = (
                "step must carry exactly one action key "
                f"(one of {', '.join(DSL_ACTION_KEYS)}) or a non-empty 'steps' "
                "group (optionally guarded by 'cond')"
            )
            raise ValueError(
                msg
            )
        if len(present) > 1:
            msg = (
                f"step carries multiple action keys: {', '.join(present)} — "
                "split into separate steps"
            )
            raise ValueError(
                msg
            )
        return self

    def _has(self, key: str) -> bool:
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


class LoopSpec(BaseModel):
    """``loop:`` body — bounded iteration with optional exit cond / ttl.

    Mirrors ``tasks/dsl_scenario_inline_mixin.py:686`` (``if "loop" in step``):
    ``max`` caps iterations, ``cond`` is the exit guard re-evaluated at the
    top of each iteration, ``ttl`` deadlines the loop wall-clock, and ``steps``
    holds the inner DslSteps (validated through this model, unlike the legacy
    opaque-dict shape).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    max: int | None = None
    cond: str | None = None
    ttl: str | int | float | None = None
    steps: list[DslStep] = Field(default_factory=list)


class RepeatSpec(BaseModel):
    """``repeat:`` body — same iteration shape as ``while_match`` but with the
    inner steps inlined under the repeat itself.

    Mirrors ``tasks/dsl_scenario_inline_mixin.py:576``: ``max`` caps iterations,
    ``until_match`` / ``until_any_match`` are early-exit probes, and
    ``stop_after_click`` / ``stop_after_click_regions`` short-circuit once an
    inner tap has fired.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    max: int | None = None
    until_match: str | None = None
    until_any_match: list[str] | None = None
    stop_after_click: bool | None = None
    stop_after_click_regions: list[str] | None = None
    steps: list[DslStep] = Field(default_factory=list)


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
    icon: str | None = None
    steps: list[DslStep] = Field(default_factory=list)


def parse_scenario(raw: dict[str, Any] | None) -> DslScenario:
    """Parse a YAML-loaded mapping into a ``DslScenario``."""
    if not isinstance(raw, dict):
        msg = f"scenario root must be a mapping, got {type(raw).__name__}"
        raise TypeError(msg)
    return DslScenario.model_validate(raw)


def validate_dsl_steps(steps: Any, *, path: str = "") -> list[str]:
    """Walk a raw ``steps`` tree and return human-readable errors.

    Pydantic ``DslStep`` covers the same checks at parse time, but the
    runtime executor reads scenarios as plain dicts (template-rendered YAML
    via ``scenarios.template_resolver.load_doc``) and never goes through
    Pydantic. This walker is the runtime gate — called at the top of
    ``DslScenarioTask.execute`` — so a scenario with ``scope: instnace``
    (typo) fails fast with a clear ``reason="scenario_invalid"`` instead of
    silently writing to player state and corrupting the cleanup sibling
    walk in :func:`tasks.dsl_scenario_helpers._collect_ocr_store_targets`.

    Returns ``[]`` when the tree is fine. Each error string carries a
    dotted ``path`` so the operator can find the offending step
    (``steps.6.loop.steps.2.scope``).
    """
    out: list[str] = []
    if not isinstance(steps, list):
        return out
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_path = f"{path}.{i}" if path else str(i)

        if "set_node" in step:
            out.append(
                f"steps.{step_path}.set_node: unsupported DSL action. "
                "Screen state is detected automatically; use top-level "
                "``node`` only when a scenario needs navigation."
            )

        if "ocr" in step:
            scope_raw = step.get("scope")  # ty: ignore[invalid-argument-type]
            if scope_raw is not None:
                scope_s = (
                    str(scope_raw).strip()
                    if not isinstance(scope_raw, bool)
                    else repr(scope_raw)
                )
                if scope_s not in OCR_SCOPES:
                    valid = ", ".join(repr(s) for s in OCR_SCOPES)
                    out.append(
                        f"steps.{step_path}.scope: invalid value {scope_raw!r} "
                        f"on ``ocr`` step — expected one of {valid}."
                    )

        for nested_key in ("steps", "else"):
            nested = step.get(nested_key)  # ty: ignore[invalid-argument-type]
            if isinstance(nested, list):
                out.extend(
                    validate_dsl_steps(nested, path=f"{step_path}.{nested_key}")
                )
        for container_key in ("loop", "repeat"):
            spec = step.get(container_key)  # ty: ignore[invalid-argument-type]
            if isinstance(spec, dict):
                inner = spec.get("steps")
                if isinstance(inner, list):
                    out.extend(
                        validate_dsl_steps(
                            inner, path=f"{step_path}.{container_key}.steps"
                        )
                    )
    return out


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
    """Path to the scenario YAML for ``scenario_key``.

    Literal ``{key}.yaml`` wins inside module scenario roots; falls back to template files
    (e.g. ``level_up_{hero}.yaml``) — note the returned path is the **template
    file**, so direct ``read_text()`` callers will see ``${hero_id}``-style
    placeholders. Use ``scenarios.template_resolver.load_doc`` if you need the
    rendered document.
    """
    from dsl import template_resolver as _tmpl

    resolved = _tmpl.resolve(repo_root, scenario_key)
    return resolved.path if resolved is not None else None


def dsl_scenario_yaml_priority(repo_root: Path, scenario_key: str) -> int | None:
    """Top-level ``priority`` from the scenario YAML file, if set and integral."""
    from dsl import template_resolver as _tmpl

    loaded = _tmpl.load_doc(repo_root, scenario_key)
    if loaded is None:
        return None
    _path, raw = loaded
    if not isinstance(raw, dict):
        return None
    p = raw.get("priority")
    if p is None or isinstance(p, bool):
        return None
    try:
        return int(p)
    except (TypeError, ValueError):
        return None


def dsl_scenario_yaml_enabled(repo_root: Path, scenario_key: str) -> bool | None:
    """Top-level ``enabled`` from scenario YAML; ``None`` when the key is unresolved."""
    from dsl import template_resolver as _tmpl

    loaded = _tmpl.load_doc(repo_root, scenario_key)
    if loaded is None:
        return None
    _path, raw = loaded
    if not isinstance(raw, dict):
        return None
    return bool(raw.get("enabled", False))


def scenario_allowed_nodes(doc: dict[Any, Any] | Any) -> tuple[str, ...]:
    """Return the allowed FSM node set declared on a scenario YAML doc.

    Accepts three legal shapes (in priority order; first non-empty wins):

    * ``nodes: [a, b, c]`` — explicit list alias.
    * ``node: a``           — single string (legacy).
    * ``node: [a, b, c]``   — list under the legacy key.

    The runtime skips navigation when ``current_screen`` is in the returned
    tuple; otherwise it navigates to the FIRST entry. Ranking uses the *min*
    BFS hops over the set, so a multi-node scenario reachable from many
    sub-pages doesn't lose priority to the worst-case distance.

    Empty tuple = scenario declares no FSM dependency (runs anywhere, no
    pre-flight nav).
    """
    if not isinstance(doc, dict):
        return ()
    for key in ("nodes", "node"):
        raw = doc.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            s = raw.strip()
            return (s,) if s else ()
        if isinstance(raw, list):
            out = tuple(str(n).strip() for n in raw if str(n).strip())
            if out:
                return out
    return ()


def dsl_scenario_yaml_device_level(repo_root: Path, scenario_key: str) -> bool:
    """Whether the rendered scenario declares ``device_level: true``."""
    from dsl import template_resolver as _tmpl

    loaded = _tmpl.load_doc(repo_root, scenario_key)
    if loaded is None:
        return False
    _path, raw = loaded
    if not isinstance(raw, dict):
        return False
    return raw.get("device_level") is True
