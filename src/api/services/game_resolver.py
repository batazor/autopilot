"""Resolve the active game id for a request.

API endpoints scoped to a game accept ``?game=`` directly. When omitted, the
helper falls back to the game of an instance referenced by ``?instance_id=``
(looked up in the devices SQLite). Endpoints raise ``HTTPException(400)`` via
:func:`require_game_for_request` if neither is available.

A request-scoped :class:`contextvars.ContextVar` (``current_request_game``)
holds the resolved game id so deep service helpers can read it without
threading ``game`` through every signature. The router-level FastAPI
dependency :func:`request_game` resolves once per request and sets the var.
"""
from __future__ import annotations

from contextvars import ContextVar

from fastapi import HTTPException, Query

from config.games import default_game, is_known_game, is_known_module_catalog

# Request-scoped active game. Routers set this via the :func:`request_game`
# dependency; service helpers read via :func:`current_request_game`.
_current_game: ContextVar[str | None] = ContextVar(
    "autopilot_api_current_game", default=None
)


def current_request_game() -> str:
    """Active game for the current request, or :func:`default_game` if unset."""
    g = _current_game.get()
    if g:
        return g
    return default_game()


def set_current_request_game(game: str) -> None:
    """Pin the active game for the current request (called by dependency)."""
    _current_game.set((game or "").strip() or default_game())


def request_game(
    game: str | None = Query(default=None),
    instance_id: str | None = Query(default=None),
) -> str:
    """FastAPI dependency: resolve game, set the request context var, return it.

    Routes with game-scoped data use ``Depends(request_game)`` to populate the
    context var automatically; service helpers then call
    :func:`current_request_game` instead of taking game in every signature.
    """
    g = require_game_for_request(
        game=game, instance_id=instance_id, allow_default=True
    )
    set_current_request_game(g)
    return g


def resolve_game(
    *,
    game: str | None = None,
    instance_id: str | None = None,
) -> str | None:
    """Game/module-catalog id from explicit param, then instance lookup, else ``None``.

    The instance lookup goes through ``config.devices_db`` so it stays in sync
    with the dashboard's source of truth. Unknown game ids are rejected with
    ``ValueError`` so the caller can return a 400.
    """
    g = (game or "").strip()
    if g:
        if not is_known_module_catalog(g):
            msg = f"unknown game/module catalog: {g!r}"
            raise ValueError(msg)
        return g

    if instance_id:
        try:
            from config import devices_db

            entries = devices_db.list_devices()
            for entry in entries:
                if entry.name == instance_id or entry.adb_serial == instance_id:
                    resolved = entry.game_for_profile()
                    if resolved and is_known_game(resolved):
                        return resolved
                    break
        except Exception:
            # Fall through to default — devices_db may be unavailable in some
            # API contexts (e.g. labeling-only deployments).
            pass
    return None


def require_game_for_request(
    *,
    game: str | None = None,
    instance_id: str | None = None,
    allow_default: bool = False,
) -> str:
    """Resolve game or raise ``HTTPException(400)``.

    When ``allow_default`` is set, fall back to :func:`config.games.default_game`
    instead of raising — used by endpoints that historically defaulted to WOS
    and need a soft migration path. New game-scoped endpoints should leave it
    ``False`` so callers learn to pass ``?game=`` explicitly.
    """
    try:
        resolved = resolve_game(game=game, instance_id=instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resolved:
        return resolved
    if allow_default:
        return default_game()
    raise HTTPException(
        status_code=400,
        detail=(
            "missing game scope: pass ?game=<id> or ?instance_id=<id> with a "
            "device whose profile resolves to a known game"
        ),
    )
