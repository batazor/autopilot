"""Streamlit drag-and-drop sortable list (``@dnd-kit`` preset)."""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableSequence, Sequence
from typing import TypedDict

import streamlit.components.v1 as components

_RELEASE = True

if not _RELEASE:
    _component = components.declare_component(
        "dnd_sortable",
        url="http://localhost:3010",
    )
else:
    _parent = os.path.dirname(os.path.abspath(__file__))
    _build = os.path.join(_parent, "frontend", "build")
    _component = components.declare_component("dnd_sortable", path=_build)


class SortableItem(TypedDict, total=False):
    id: str
    title: str
    subtitle: str


class SortableReorder(TypedDict, total=False):
    """Returned when the user reorders rows (only after drag end)."""

    order: list[str]
    revision: int


def _frame_height_for_count(n: int) -> int:
    return min(52 + max(n, 1) * 58, 420)


def sortable_list(
    items: Sequence[SortableItem | Mapping[str, str]],
    *,
    revision: int = 0,
    disabled: bool = False,
    key: str | None = None,
) -> SortableReorder | None:
    """Sortable drag-and-drop list; returns ``{ order, revision }`` after reorder.

    Each item must include ``id`` (stable keys for indices use ``\"0\"..\"n-1\"``),
    ``title`` (shown bold), optional ``subtitle`` (muted one-liner).

    Pass ``revision`` from ``st.session_state`` and bump after applying a reorder
    so stale component state from a previous render is ignored.

    To apply reorder to a Python list ``steps``::

        order = result.get("order") or []
        expected = {str(i) for i in range(len(steps))}
        if len(order) == len(steps) and set(order) == expected:
            idx = [int(x) for x in order]
            if idx != list(range(len(steps))):
                reordered = [steps[i] for i in idx]
                steps[:] = reordered

    Args:
        items: Row payloads for the widget.
        revision: Monotonic counter stored server-side per widget scope.
        disabled: Toggle off dragging (still shows list).
        key: Stable Streamlit key (include parent path when nested editors share shape).
    """
    payload = []
    for row in items:
        d = dict(row)
        entry: dict[str, str] = {
            "id": str(d.get("id", "")),
            "title": str(d.get("title", "")),
        }
        subt = str(d.get("subtitle") or "").strip()
        if subt:
            entry["subtitle"] = subt
        payload.append(entry)
    fh = _frame_height_for_count(len(payload))
    val = _component(
        items=payload,
        revision=int(revision),
        disabled=bool(disabled),
        frameHeight=int(fh),
        key=key,
        default=None,
    )
    if not val or not isinstance(val, dict):
        return None
    od = val.get("order")
    if not isinstance(od, list):
        return None
    order_out = [str(x) for x in od]
    rev_raw = val.get("revision")
    try:
        rev_out = int(rev_raw)
    except (TypeError, ValueError):
        return None
    return {"order": order_out, "revision": rev_out}


def apply_order_to_list(
    target: MutableSequence[Any],
    order_ids: Sequence[str],
) -> bool:
    """Reorder ``target`` using ``order_ids`` (subset of ``target`` indices).

    Expects ``order_ids`` to contain each index ``\"0\"..str(len-1)`` exactly once.
    Returns ``True`` if mutation happened.
    """
    n = len(target)
    if n == 0 or len(order_ids) != n:
        return False
    try:
        idx = [int(x) for x in order_ids]
    except ValueError:
        return False
    if set(idx) != set(range(n)):
        return False
    if idx == list(range(n)):
        return False
    reordered = [target[i] for i in idx]
    target[:] = list(reordered)
    return True


__all__ = ["SortableItem", "SortableReorder", "apply_order_to_list", "sortable_list"]
