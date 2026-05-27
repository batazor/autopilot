"""First-run wizard: milestones + environment health."""

from __future__ import annotations

from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends

from api.deps import get_redis
from api.services import onboarding

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


@router.get("/state")
def get_state(client: RedisDep) -> dict[str, Any]:
    return onboarding.read_state(client)


@router.get("/env-health")
def get_env_health(client: RedisDep) -> dict[str, Any]:
    return onboarding.check_env_health(client)
