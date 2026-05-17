from collections.abc import Mapping, MutableSequence, Sequence
from typing import Any, TypedDict

__all__ = ['SortableItem', 'SortableReorder', 'apply_order_to_list', 'sortable_list']

class SortableItem(TypedDict, total=False):
    id: str
    title: str
    subtitle: str

class SortableReorder(TypedDict, total=False):
    order: list[str]
    revision: int

def sortable_list(items: Sequence[SortableItem | Mapping[str, str]], *, revision: int = 0, disabled: bool = False, key: str | None = None) -> SortableReorder | None: ...
def apply_order_to_list(target: MutableSequence[Any], order_ids: Sequence[str]) -> bool: ...
