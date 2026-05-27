"""Module catalog routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.services import modules_api as svc

router = APIRouter(prefix="/api/modules", tags=["modules"])


class EnabledBody(BaseModel):
    enabled: bool


class AssignmentBody(BaseModel):
    scenario_id: str | None = None


class CreateModuleBody(BaseModel):
    id: str
    title: str
    description: str = ""
    parent: str = ""
    wiki: bool = False


@router.get("")
def list_modules(scope: str = Query(default="all")) -> dict[str, object]:
    return {
        "scope": scope,
        "modules": svc.list_modules(module_scope=scope),
    }


@router.post("", status_code=201)
def create_module(body: CreateModuleBody) -> dict[str, object]:
    try:
        row = svc.create_module(
            module_id=body.id,
            title=body.title,
            description=body.description,
            parent=body.parent,
            wiki=body.wiki,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"module": row}


@router.get("/scenarios")
def list_scenarios(scope: str = Query(default="all")) -> dict[str, object]:
    return {"scenarios": svc.list_scenarios(module_scope=scope)}


@router.post("/scenarios/reload")
def post_scenarios_reload() -> dict[str, object]:
    """Re-scan scenario YAMLs from disk and wake the scheduler.

    Replaces the previous watchdog-based hot reload: the UI calls this after
    edits instead of paying for a polling observer on every scenarios tree.
    """
    from services import get_scenario_loader

    loader = get_scenario_loader()
    loader.reload()
    return {"loaded": len(loader.load_all())}


@router.patch("/scenarios/{scenario_key}/enabled")
def patch_enabled(scenario_key: str, body: EnabledBody) -> dict[str, object]:
    try:
        svc.set_scenario_enabled(scenario_key, body.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"key": scenario_key, "enabled": body.enabled}


@router.get("/players")
def list_players() -> dict[str, object]:
    return {"players": svc.list_players_with_assignments()}


@router.put("/players/{player_id}/assignment")
def put_assignment(player_id: str, body: AssignmentBody) -> dict[str, object]:
    svc.set_player_assignment(player_id, body.scenario_id)
    return {"player_id": player_id, "assigned_scenario": body.scenario_id}
