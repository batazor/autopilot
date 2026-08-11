"""Every action the normaliser can emit must have an evaluator.

The dispatch used to be a 130-line if-chain of near-identical call blocks, each
passing its handler a slightly different bundle of tick-wide keyword arguments.
It is now a dict lookup, which moves the failure mode: a miss no longer fails to
compile, it returns ``unsupported_action`` at runtime, on device, on a rule an
operator thought was live.

``normalize_overlay_action`` is the only thing that invents action names —
``isRedDot: false`` becomes ``red_dot_absent``, ``exist`` becomes ``findIcon``.
So the invariant worth holding is between those two, and it fails the day
someone teaches the normaliser a new gate without wiring an evaluator.
"""

from __future__ import annotations

import ast
import inspect

from analysis import overlay_rules
from analysis.overlay_engine import _RULE_EVALUATORS, RuleEvalContext

# ``text`` rules do not evaluate in place: they are batched into one
# ``ocr_regions`` call per tick, which is what replaced 130+ sequential HTTP
# calls on screen_verify.yaml. Absence from the registry is the design.
_DEFERRED = frozenset({"text"})


def _actions_the_normaliser_emits() -> set[str]:
    """String literals assigned to ``action`` inside ``normalize_overlay_action``."""
    tree = ast.parse(inspect.getsource(overlay_rules.normalize_overlay_action))
    return {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "action" for t in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def test_every_emittable_action_has_an_evaluator() -> None:
    emitted = _actions_the_normaliser_emits()

    assert emitted, "parsed no action literals — the normaliser was probably restructured"
    missing = sorted(emitted - set(_RULE_EVALUATORS) - _DEFERRED)

    assert not missing, (
        f"normalize_overlay_action can emit {missing}, but _RULE_EVALUATORS has no entry — "
        "rules using them evaluate to unsupported_action at runtime"
    )


def test_the_registry_has_no_entry_nothing_can_produce() -> None:
    """A dead entry is a handler nobody calls, which reads as live coverage."""
    emitted = _actions_the_normaliser_emits()
    # An action passes straight through when the YAML names it directly, so the
    # normaliser's literals are not the whole story — but the gate-derived ones
    # (`*_absent`) can ONLY come from there.
    derived = {a for a in _RULE_EVALUATORS if a.endswith("_absent")}

    assert derived <= emitted, f"unreachable evaluators: {sorted(derived - emitted)}"


def test_every_evaluator_takes_the_uniform_signature() -> None:
    """The registry is only possible because they agree on one signature; a
    handler that re-adds a bespoke keyword breaks the lookup call, not the
    import, so nothing else would catch it."""
    for action, fn in sorted(_RULE_EVALUATORS.items()):
        params = list(inspect.signature(fn).parameters.values())

        assert [p.name for p in params] == ["rule", "compiled", "ctx"], (
            f"{action} → {fn.__name__} has signature {[p.name for p in params]}"
        )
        assert all(p.kind is p.POSITIONAL_OR_KEYWORD for p in params), (
            f"{action} → {fn.__name__} declares keyword-only parameters again"
        )


def test_the_context_is_immutable() -> None:
    """It is built once per tick and handed to every evaluator; one handler
    mutating it would silently change what the next one sees."""
    import dataclasses

    assert dataclasses.is_dataclass(RuleEvalContext)
    assert RuleEvalContext.__dataclass_params__.frozen


def test_no_evaluator_is_called_with_the_old_keyword_bundle() -> None:
    """The signature check above does not cover call sites, and that is where
    the mistake actually happened: ``_eval_cta_button_rule`` delegates to the
    green/blue detectors, and those two internal calls kept passing
    ``image_bgr=``/``area_doc=``/``set_node_s=`` after the switch. Nothing failed
    at import — every ``cta_button`` rule raised TypeError at tick time instead.
    """
    import ast
    import pathlib

    from analysis import overlay_engine

    tree = ast.parse(pathlib.Path(overlay_engine.__file__).read_text(encoding="utf-8"))
    offenders = [
        f"line {node.lineno}: {node.func.id}({', '.join(k.arg or '**' for k in node.keywords)})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("_eval_")
        and node.func.id.endswith("_rule")
        and node.keywords
    ]

    assert not offenders, (
        "evaluators take (rule, compiled, ctx) positionally; these calls still pass "
        f"the old per-tick keyword bundle: {offenders}"
    )
