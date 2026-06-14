"""Redis handoff between the farm registration flow and the dashboard.

The registration process (``games.wos.farm.register``) fills the signup form,
tries to solve the image-code and slider captcha with ddddocr, then publishes a
"waiting for human" marker and blocks until the operator checks the browser,
clicks Sign Up, and presses **Done** (or **Failed**) in the dashboard. That
button hits the farm API, which writes the outcome signal here; the registration
process polls for it.

Keys (single operator → one pending registration at a time):
  - ``wos:farm:register:pending``        hash {username, started_at, ...status}
  - ``wos:farm:register:done:<username>`` str  "done" | "failed"  (short TTL)

All clients use ``decode_responses=True`` (see ``api.deps.get_redis``), so reads
come back as ``str``.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis

KEY_PENDING = "wos:farm:register:pending"
_PENDING_TTL_S = 1800  # 30 min — the registration process clears it sooner
_SIGNAL_TTL_S = 300


def _done_key(username: str) -> str:
    return f"wos:farm:register:done:{username}"


def set_pending(client: Redis, username: str, **status: object) -> None:
    """Publish that ``username`` is filled in the browser and awaiting the human.

    Clears any stale outcome signal for the same username first so a previous
    run's "done" can't be mistaken for this one.
    """
    client.delete(_done_key(username))
    extra = {str(k): str(v) for k, v in status.items() if v is not None}
    client.hset(
        KEY_PENDING,
        mapping={"username": username, "started_at": str(time.time()), **extra},
    )
    client.expire(KEY_PENDING, _PENDING_TTL_S)


def get_pending(client: Redis) -> dict[str, str] | None:
    """The registration currently awaiting the operator, or ``None``."""
    data = client.hgetall(KEY_PENDING)
    return dict(data) if data else None


def clear_pending(client: Redis, username: str | None = None) -> None:
    client.delete(KEY_PENDING)
    if username:
        client.delete(_done_key(username))


def signal(client: Redis, username: str, outcome: str) -> None:
    """Record the operator's verdict for ``username`` ("done" or "failed")."""
    clean = outcome.strip().lower()
    if clean not in {"done", "failed"}:
        msg = f"outcome must be 'done' or 'failed', got {outcome!r}"
        raise ValueError(msg)
    client.set(_done_key(username), clean, ex=_SIGNAL_TTL_S)


def read_signal(client: Redis, username: str) -> str | None:
    return client.get(_done_key(username))
