"""Smoke tests for streamlit-nested-table Python API."""

from __future__ import annotations

from streamlit_nested_table import nested_table, table_column


def test_table_column_helper() -> None:
    col = table_column("count", "Count", align="right", width=80)
    assert col["accessor_key"] == "count"
    assert col["header"] == "Count"
    assert col["align"] == "right"
    assert col["width"] == 80


def test_table_column_link_and_bool() -> None:
    col = table_column(
        "edit",
        "Edit",
        cell_type="link",
        link_text_key="edit_text",
    )
    assert col["cell_type"] == "link"
    assert col["link_text_key"] == "edit_text"


def test_nested_table_callable() -> None:
    rows = [
        {
            "id": "a",
            "name": "Parent",
            "count": 2,
            "subRows": [{"id": "a1", "name": "Child", "count": 1}],
        },
    ]
    assert callable(nested_table)
    assert rows[0]["subRows"][0]["name"] == "Child"
