"""Screen routes graph (React Flow payload)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from api.services import routes_graph as routes_svc

router = APIRouter(prefix="/api/routes", tags=["routes"])


@router.get("/graph")
def get_routes_graph(
    route_from: Annotated[str | None, Query(alias="from")] = None,
    route_to: Annotated[str | None, Query(alias="to")] = None,
    focus: str | None = None,
    view: Annotated[str, Query()] = "hub",
    hub_depth: Annotated[int, Query(ge=1, le=6)] = 2,
) -> dict[str, Any]:
    allowed = {"hub", "focus", "path", "full"}
    graph_view = view if view in allowed else "hub"
    return routes_svc.build_graph_payload(
        route_from=route_from,
        route_to=route_to,
        focus=focus or None,
        view=graph_view,  # type: ignore[arg-type]
        hub_depth=hub_depth,
    )


@router.get("/edges")
def get_routes_edges(
    q: str = "",
    status: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    return routes_svc.list_edges(query=q, statuses=status)


@router.get("/nodes/{node_id}")
def get_routes_node(node_id: str) -> dict[str, Any]:
    if node_id not in routes_svc.tap_graph_nodes():
        raise HTTPException(status_code=404, detail=f"unknown screen: {node_id}")
    return routes_svc.node_details(node_id)


@router.get("/screen-zones/{screen_id}")
def get_routes_screen_zones(screen_id: str) -> dict[str, Any]:
    """Transition tap-zones + labeled regions for ``screen_id`` (overlay view).

    Lenient by design: a screen with a labeled reference but no edges yet is a
    valid thing to inspect (it shows every region is unmapped), so we return an
    empty/has_reference payload rather than 404 for unknown ids.
    """
    return routes_svc.screen_zones(screen_id)
