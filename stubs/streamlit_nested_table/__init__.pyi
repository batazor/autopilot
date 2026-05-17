from collections.abc import Sequence
from typing import Any, TypedDict

from _typeshed import Incomplete

__all__ = ['Align', 'CellType', 'NestedTableMultiSelection', 'NestedTableSelection', 'TableColumn', 'nested_table', 'table_column']

Align: Incomplete
CellType: Incomplete

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

def table_column(accessor_key: str, header: str, *, column_id: str | None = None, width: int | str | None = None, align: Align = 'left', cell_type: CellType = 'text', link_text_key: str | None = None, pill_preset: str | None = None) -> TableColumn: ...
def nested_table(rows: Sequence[dict[str, Any]], columns: Sequence[TableColumn], *, sub_rows_key: str = 'subRows', height: int = 420, width: int | None = None, key: str | None = None, default_expanded: bool = False, striped: bool = True, compact: bool = False, selectable: bool = False, multi_select: bool = False, selected_ids: Sequence[str] | None = None, get_row_id: str = 'id', hide_expand: bool = False) -> NestedTableSelection | NestedTableMultiSelection | None: ...
