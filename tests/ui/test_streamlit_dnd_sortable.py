"""Tests for streamlit-dnd-sortable helper API (no iframe)."""

from __future__ import annotations

from streamlit_dnd_sortable import apply_order_to_list


def test_apply_order_to_list_identity() -> None:
    a = ["x", "y", "z"]
    assert apply_order_to_list(a, ["0", "1", "2"]) is False
    assert a == ["x", "y", "z"]


def test_apply_order_to_list_reverse() -> None:
    a = ["x", "y", "z"]
    assert apply_order_to_list(a, ["2", "1", "0"]) is True
    assert a == ["z", "y", "x"]


def test_apply_order_to_list_bad_ids() -> None:
    a = ["a", "b"]
    assert apply_order_to_list(a, ["0"]) is False
    assert apply_order_to_list(a, ["0", "2"]) is False


def test_apply_order_to_list_apply_order_import() -> None:
    assert callable(apply_order_to_list)
