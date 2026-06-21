"""Dynamic edge resolver for template-matched icons inside a search region."""
from __future__ import annotations

from typing import Any

from navigation.screen_graph import DynamicEdgeSpec, Tap, register_edge_resolver


async def resolve_template_icon(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    region = str(spec.get("region") or "").strip()
    template = str(spec.get("template") or "").strip()
    if not region or not template:
        return None
    tap_spec: dict[str, Any] = {
        "type": "template_icon",
        "region": region,
        "template": template,
    }
    for key in ("threshold", "search_region"):
        if key in spec:
            tap_spec[key] = spec[key]
    return [tap_spec]


register_edge_resolver("template_icon", resolve_template_icon)
