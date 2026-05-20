"""Debug scenario runner routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.services import debug_api as svc

router = APIRouter(prefix="/api/debug", tags=["debug"])


class RunBody(BaseModel):
    instance_id: str
    scenario_key: str
    player_id: str = ""
    priority: int = 0
    start_step_index: int = Field(default=0, ge=0)


@router.post("/run")
def post_run(body: RunBody) -> dict[str, object]:
    try:
        return svc.run_scenario(
            instance_id=body.instance_id,
            scenario_key=body.scenario_key,
            player_id=body.player_id,
            priority=body.priority,
            start_step_index=body.start_step_index,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
