"""Dynamic edge resolver for rows inside the scrollable main-menu City panel."""
from __future__ import annotations

from typing import Any

from navigation.screen_graph import DynamicEdgeSpec, Tap, register_edge_resolver


async def resolve_main_menu_panel_row(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    _ = (instance_id, redis_client)
    section = str(spec.get("section") or "").strip().lower()
    row = str(spec.get("row") or "").strip().lower()
    rows_raw = spec.get("rows")
    rows = [
        str(r).strip().lower()
        for r in rows_raw
        if str(r).strip()
    ] if isinstance(rows_raw, list) else []
    if not section or (not row and not rows):
        return None

    tap_spec: dict[str, Any] = {
        "type": "main_menu_panel_row",
        "section": section,
    }
    if row:
        tap_spec["row"] = row
    if rows:
        tap_spec["rows"] = rows
    if spec.get("approval_region"):
        tap_spec["approval_region"] = str(spec["approval_region"]).strip()
    return [tap_spec]


register_edge_resolver("main_menu_panel_row", resolve_main_menu_panel_row)
