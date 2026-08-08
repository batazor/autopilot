"""The reason registry is only worth having because this test exists.

A `StrEnum` nobody is forced to use rots in a month: the next person writes
`{"reason": "some_new_thing"}` inline, it passes review, and the registry
quietly stops describing reality. So the source of truth is the *source* — this
scans it and fails if a literal is not registered.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tasks.reasons import REASON_CATEGORY, TaskReason, is_known_reason, reason_category

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCANNED_ROOTS = ("src/tasks", "src/worker")

# Calls that carry a `reason=` of a DIFFERENT vocabulary. Dashboard events are a
# UI notification channel ("running", "finished", "history"); `_overlay_tick_now`
# labels why a capture tick fired. Neither is a task outcome.
_FOREIGN_REASON_CALLS = ("dashboard_event", "publish", "_overlay_tick_now")


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _scan_reason_literals() -> dict[str, set[str]]:
    """``{reason_literal: {file, ...}}`` for the task-outcome vocabulary."""
    found: dict[str, set[str]] = {}

    def _record(value: str, rel: str) -> None:
        if value:
            found.setdefault(value, set()).add(rel)

    for root_rel in _SCANNED_ROOTS:
        for path in (_REPO_ROOT / root_rel).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            rel = path.relative_to(_REPO_ROOT).as_posix()

            foreign: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and any(
                    hint in _call_name(node) for hint in _FOREIGN_REASON_CALLS
                ):
                    foreign.update(id(sub) for sub in ast.walk(node))

            for node in ast.walk(tree):
                if id(node) in foreign:
                    continue
                if isinstance(node, ast.Dict):
                    for key, val in zip(node.keys, node.values, strict=False):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "reason"
                            and isinstance(val, ast.Constant)
                            and isinstance(val.value, str)
                        ):
                            _record(val.value, rel)
                elif isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if (
                            kw.arg == "reason"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                        ):
                            _record(kw.value.value, rel)
    return found


def test_every_reason_literal_in_the_source_is_registered() -> None:
    found = _scan_reason_literals()
    assert found, "the scanner found no reason literals at all — it is broken"

    unregistered = {
        value: sorted(files) for value, files in found.items() if not is_known_reason(value)
    }

    assert not unregistered, (
        "these reason literals are not in tasks.reasons.TaskReason:\n"
        + "\n".join(f"  {v!r} — {', '.join(f)}" for v, f in sorted(unregistered.items()))
        + "\n\nAdd the member (value must equal the literal — the coordinator's "
        "circuit breaker persists reasons, so changing one is a state migration) "
        "and give it a REASON_CATEGORY entry."
    )


def test_every_member_has_a_category() -> None:
    """A reason with no category is invisible to the thing the registry is for."""
    missing = sorted(r.value for r in TaskReason if r not in REASON_CATEGORY)

    assert not missing, f"TaskReason members with no REASON_CATEGORY: {missing}"


def test_values_are_bare_strings() -> None:
    """`str(reason)` must survive every Redis / JSON boundary unchanged."""
    for member in TaskReason:
        assert str(member) == member.value
        assert isinstance(member.value, str)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("navigation_failed", "navigation"),
        ("scenario_cond_false", "scenario"),
        ("tap_not_approved", "approval"),
        ("unknown_step_key", "config"),
        ("preempted_by_higher_priority", "preempted"),
    ],
)
def test_category_lookup(reason: str, expected: str) -> None:
    assert reason_category(reason) == expected


def test_unknown_reason_has_no_category_but_is_not_an_error() -> None:
    """An unregistered reason yields no category — it must not raise, and must
    not masquerade as a known one."""
    assert reason_category("navigaton_faild") == ""
    assert reason_category("") == ""
    assert is_known_reason("navigaton_faild") is False


def test_foreign_vocabularies_are_not_swept_in() -> None:
    """Dashboard-event reasons share the keyword and mean something else.
    Sweeping them in would force unrelated UI strings into the registry."""
    found = _scan_reason_literals()

    for ui_only in ("running", "finished", "history", "avatar_identity"):
        assert ui_only not in found, (
            f"{ui_only!r} is a dashboard-event reason; the scanner's exclusion "
            "list stopped working"
        )


# --- metric labelling ------------------------------------------------------
#
# `safe_reason_label` is the only thing between a reason and a metric label, so
# its three-way behaviour is part of the registry's contract.


def test_registered_reason_passes_through_as_a_label() -> None:
    from config.telemetry import safe_reason_label

    assert safe_reason_label("match_region_not_found") == "match_region_not_found"


def test_unregistered_but_token_shaped_reason_is_not_collapsed(caplog) -> None:
    """Collapsing it would hide the one thing the registry exists to surface.

    It is reported verbatim and warned about once; CI's registry test is what
    turns that warning into a fix.
    """
    import config.telemetry as telemetry

    telemetry._warned_unregistered_reasons.discard("brand_new_reason")
    with caplog.at_level("WARNING"):
        assert telemetry.safe_reason_label("brand_new_reason") == "brand_new_reason"

    assert "not registered" in caplog.text


def test_free_text_still_collapses_to_one_bucket() -> None:
    """An exception repr must not become its own metric series."""
    from config.telemetry import safe_reason_label

    assert safe_reason_label("RuntimeError: adb offline (device 5615)") == "error"
    assert safe_reason_label("navigation_failed: deals → main_city (no route)") == "error"


def test_empty_reason_stays_empty() -> None:
    from config.telemetry import safe_reason_label

    assert safe_reason_label("") == ""
    assert safe_reason_label("   ") == ""
