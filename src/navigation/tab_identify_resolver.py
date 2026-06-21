"""Dynamic edge resolver: navigate to a family tab identified by its template.

Unlike :mod:`navigation.tab_index_resolver` (which assumes the target sits at a
fixed, already-visible slot index), this resolver lets the navigator find the
target tab *visually* — segment the strip, identify each tab by its per-page
icon template, click the match, and scroll (``advance``) the strip when the tab
is not yet on screen. That makes it robust to scrollable strips like Shop where
a tab's screen position shifts with the active tab and the scroll offset.

YAML shape (per edge in ``edge_taps.yaml``)::

    shop:
      shop.artisans_trove:
        resolver: tab_identify
        region: shop.tabs_strip
        page: shop.artisans_trove
        namespace: shop

``page`` defaults to the edge destination at tap time; ``namespace`` defaults to
the strip region's prefix. The actual detect → identify → click → advance loop
runs in ``Navigator._tap_tab_identify_async``.
"""
from __future__ import annotations

from typing import Any

from navigation.screen_graph import DynamicEdgeSpec, Tap, register_edge_resolver


async def resolve_tab_identify(
    spec: DynamicEdgeSpec,
    instance_id: str,
    redis_client: Any,
) -> list[Tap] | None:
    region = str(spec.get("region") or "").strip()
    if not region:
        return None
    tap_spec: dict[str, Any] = {"type": "tab_identify", "region": region}
    page = str(spec.get("page") or "").strip()
    if page:
        tap_spec["page"] = page
    namespace = str(spec.get("namespace") or "").strip()
    if namespace:
        tap_spec["namespace"] = namespace
    return [tap_spec]


register_edge_resolver("tab_identify", resolve_tab_identify)
