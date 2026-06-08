"""Dynamic edge resolver for tab-strip clicks by detected tab index."""
from __future__ import annotations

from typing import Any

from navigation.screen_graph import DynamicEdgeSpec, Tap, register_edge_resolver


async def resolve_tab_index(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    region = str(spec.get("region") or "").strip()
    if not region:
        return None
    try:
        index = int(spec.get("index"))
    except (TypeError, ValueError):
        return None
    tap_spec: dict[str, Any] = {
        "type": "tab_index",
        "region": region,
        "index": index,
    }
    return [tap_spec]


register_edge_resolver("tab_index", resolve_tab_index)
