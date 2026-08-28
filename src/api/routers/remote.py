"""Token-addressed remote control: watch one device's screen and tap it.

Everything here is reachable with nothing but the share link. The token is the
whole credential, so these routes expose the minimum a helper needs — the live
stream and a tap — and never reveal the instance id, the fleet, or any other
state. See :mod:`api.services.remote_control` for the trust model.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse  # noqa: TC002 — FastAPI reads it at runtime

from api.deps import get_redis
from api.routers.screen import TapBody, stream_response
from api.services import remote_control, screen_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/remote", tags=["remote"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


def _instance_or_404(client: redis.Redis, token: str) -> str:
    instance_id = remote_control.resolve(client, token)
    if instance_id is None:
        raise HTTPException(status_code=404, detail="link expired or unknown")
    return instance_id


@router.get("/{token}")
def get_remote_session(token: str, client: RedisDep) -> dict[str, Any]:
    """Confirm the link is live and report whether the screen is being served."""
    instance_id = _instance_or_404(client, token)
    status = screen_stream.status(client, instance_id)
    return {"ok": True, "screen": status}


@router.get("/{token}/stream")
def get_remote_stream(
    token: str, request: Request, client: RedisDep
) -> StreamingResponse:
    return stream_response(_instance_or_404(client, token), request, client)


@router.post("/{token}/tap")
def post_remote_tap(token: str, body: TapBody, client: RedisDep) -> dict[str, Any]:
    """Tap the live screen.

    A miss (no worker subscribed) is reported rather than raised: the helper's
    page shows "bot offline" instead of a red error, and the click is simply
    dropped — there is no point queueing a coordinate that will be stale by the
    time a worker boots.
    """
    instance_id = _instance_or_404(client, token)
    delivered = remote_control.publish_tap(client, instance_id, body.x, body.y)
    if delivered == 0:
        logger.info("remote tap dropped (no worker) instance=%s", instance_id)
    return {"ok": delivered > 0, "delivered": delivered}
