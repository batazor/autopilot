"""Dashboard realtime events (SSE)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import redis
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from api.deps import get_redis
from api.services.dashboard_stream import stream_dashboard_events

router = APIRouter(prefix="/api", tags=["events"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


@router.get("/events/stream")
async def get_events_stream(
    request: Request,
    client: RedisDep,
    topics: Annotated[list[str] | None, Query()] = None,
    instance_id: str | None = None,
) -> StreamingResponse:
    """Push queue / approval / notification changes to the dashboard.

    Query params:
    - ``topics``: repeat or comma-separated (``queue``, ``approval``, ``notifications``)
    - ``instance_id``: required for approval and notifications topics
    """
    raw_topics = topics or ["queue"]
    expanded: set[str] = set()
    for item in raw_topics:
        for part in item.split(","):
            part = part.strip()
            if part:
                expanded.add(part)

    async def should_continue() -> bool:
        return not await request.is_disconnected()

    async def body() -> AsyncIterator[str]:
        async for chunk in stream_dashboard_events(
            client,
            topics=expanded,
            instance_id=instance_id,
            should_continue=should_continue,
        ):
            yield chunk

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
