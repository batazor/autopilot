"""Streamlit nested table — TanStack Table + Tailwind."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, Literal, TypedDict

import streamlit.components.v1 as components

_RELEASE = True

if not _RELEASE:
    _component = components.declare_component(
        "nested_table",
        url="http://localhost:3002",
    )
else:
    _parent = os.path.dirname(os.path.abspath(__file__))
    _build = os.path.join(_parent, "frontend", "build")
    _component = components.declare_component("nested_table", path=_build)


Align = Literal["left", "center", "right"]
CellType = Literal["text", "link", "bool", "pill"]


class TableColumn(TypedDict, total=False):
    id: str
    header: str
    accessor_key: str
    width: int | str | None
    align: Align
    cell_type: CellType
    link_text_key: str
    pill_preset: str


class NestedTableSelection(TypedDict, total=False):
    rowId: str
    depth: int
    row: dict[str, Any]


class NestedTableMultiSelection(TypedDict, total=False):
    selectedIds: list[str]
    lastRow: NestedTableSelection


def table_column(
    accessor_key: str,
    header: str,
    *,
    column_id: str | None = None,
    width: int | str | None = None,
    align: Align = "left",
    cell_type: CellType = "text",
    link_text_key: str | None = None,
    pill_preset: str | None = None,
) -> TableColumn:
    """Helper to build a column spec for :func:`nested_table`."""
    col_id = (column_id or accessor_key).strip()
    out: TableColumn = {
        "id": col_id,
        "header": header,
        "accessor_key": accessor_key,
        "width": width,
        "align": align,
        "cell_type": cell_type,
    }
    if link_text_key:
        out["link_text_key"] = link_text_key
    if pill_preset:
        out["pill_preset"] = pill_preset
    return out


def nested_table(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[TableColumn],
    *,
    sub_rows_key: str = "subRows",
    height: int = 420,
    width: int | None = None,
    key: str | None = None,
    default_expanded: bool = False,
    striped: bool = True,
    compact: bool = False,
    selectable: bool = False,
    multi_select: bool = False,
    selected_ids: Sequence[str] | None = None,
    get_row_id: str = "id",
    hide_expand: bool = False,
) -> NestedTableSelection | NestedTableMultiSelection | None:
    """Render a nested table with expand/collapse per parent row.

    Args:
        rows: Top-level row dicts. Nested children live under ``sub_rows_key``.
        columns: Column definitions (see :func:`table_column`).
        sub_rows_key: Key on each row whose value is a list of child row dicts.
        height: Pixel height of the scrollable table body.
        width: Optional pixel width (defaults to Streamlit column width).
        default_expanded: Expand all parent rows on first render.
        striped: Zebra striping on body rows.
        compact: Tighter vertical padding.
        selectable: Highlight row on click; returns selection to Python.
        multi_select: Checkboxes per leaf row; returns ``selectedIds`` to Python.
        selected_ids: Initial selection when ``multi_select=True``.
        get_row_id: Row dict key used as stable row id (default ``id``).
        hide_expand: Hide the chevron column when rows are not nested.

    Returns:
        When ``multi_select=True``, ``{selectedIds, lastRow?}``.
        When ``selectable=True``, the last clicked row ``{rowId, depth, row}``.
        Otherwise ``None``.
    """
    payload_rows = [dict(r) for r in rows]
    payload_columns = [dict(c) for c in columns]
    ids = [str(x) for x in (selected_ids or [])]
    value = _component(
        rows=payload_rows,
        columns=payload_columns,
        subRowsKey=sub_rows_key,
        height=int(height),
        width=int(width) if width is not None else None,
        defaultExpanded=bool(default_expanded),
        striped=bool(striped),
        compact=bool(compact),
        selectable=bool(selectable),
        multiSelect=bool(multi_select),
        selectedIds=ids,
        getRowId=get_row_id,
        hideExpand=bool(hide_expand),
        key=key,
        default=None,
    )
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value  # type: ignore[return-value]
    return None


__all__ = [
    "Align",
    "CellType",
    "NestedTableMultiSelection",
    "NestedTableSelection",
    "TableColumn",
    "nested_table",
    "table_column",
]
