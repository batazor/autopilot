"""Unit tests for the sandboxed cond evaluator in ``dsl/cond_eval.py``."""

from __future__ import annotations

from dsl.cond_eval import eval_cond


def test_eval_cond_dotted_key_truthy() -> None:
    state = {"heroes.norah.level": 7}
    assert eval_cond("heroes.norah.level >= 6", state) is True


def test_eval_cond_dotted_key_falsy() -> None:
    state = {"heroes.norah.level": 3}
    assert eval_cond("heroes.norah.level >= 6", state) is False


def test_eval_cond_coerces_numeric_strings_from_state() -> None:
    assert eval_cond("buildings.furnace.level >= 4", {"buildings.furnace.level": "5"}) is True
    assert eval_cond("buildings.furnace.level >= 4", {"buildings.furnace.level": "3"}) is False


def test_eval_cond_missing_key_returns_false() -> None:
    assert eval_cond("heroes.gisela.level >= 6", {}) is False


def test_eval_cond_empty_returns_false() -> None:
    assert eval_cond("", {"x": 1}) is False
    assert eval_cond("   ", {"x": 1}) is False


def test_eval_cond_syntax_error_returns_false() -> None:
    assert eval_cond("heroes.norah.level >>>= 6", {"heroes.norah.level": 9}) is False


def test_eval_cond_logical_ops() -> None:
    state = {"a.b": 5, "c.d": True}
    assert eval_cond("a.b > 3 and c.d", state) is True
    assert eval_cond("a.b > 100 or not c.d", state) is False


def test_eval_cond_keywords_not_rewritten() -> None:
    assert eval_cond("True", {}) is True
    assert eval_cond("not False", {}) is True


def test_eval_cond_bare_identifier_uses_state() -> None:
    assert eval_cond("level >= 6", {"level": 7}) is True


def test_eval_cond_missing_bare_identifier_is_silent_false(caplog) -> None:
    # A bare flag the producer hasn't written yet (e.g. ``joe_event_active``)
    # must opt out silently — same as a missing dotted key — not warn every tick.
    with caplog.at_level("WARNING"):
        assert eval_cond("joe_event_active", {}) is False
        assert eval_cond("joe_event_active and stamina > 10", {"stamina": 50}) is False
    assert "joe_event_active" not in caplog.text
    assert "eval_cond failed" not in caplog.text


def test_eval_cond_coerces_only_referenced_keys() -> None:
    """Coercion must not walk the whole state dict — a developed account's flat
    state holds thousands of keys and eval_cond runs per rule per overlay tick.
    Only fields the expression references may be touched."""
    import dsl.cond_eval as ce
    from dsl.cond_eval import _coerce_cond_value, _cond_referenced_keys

    touched: list[str] = []

    class _Tracking(str):
        __slots__ = ()

    real_coerce = _coerce_cond_value

    def _spy(value):
        if isinstance(value, _Tracking):
            touched.append(str(value))
        return real_coerce(value)

    state = {
        "buildings.furnace.level": "5",
        "unrelated.key.one": _Tracking("1"),
        "unrelated.key.two": _Tracking("2"),
    }
    orig = ce._coerce_cond_value
    ce._coerce_cond_value = _spy
    try:
        assert eval_cond("buildings.furnace.level >= 4", state) is True
    finally:
        ce._coerce_cond_value = orig
    assert touched == []

    # Key extraction covers both dotted (_state subscript) and bare names.
    import ast

    tree = ast.parse('_state["a.b"] > 1 and flag', mode="eval")
    assert _cond_referenced_keys(tree) == {"a.b", "flag"}
