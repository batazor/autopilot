"""Cross-instance coordination primitives — the bot fleet's "nervous system".

Game-agnostic (no ``games/`` import): a fleet registry + ``fid → instance``
reverse index, a directive bus (one instance/orchestrator signals another), a
generalized distributed lease, and a barrier/rendezvous. The higher-level
saga engine lives under :mod:`coord.campaign`; WoS campaigns that consume these
primitives live under ``games/wos/core/fleet``.

Pure logic (``models``, ``routing``, ``barrier_logic``) imports no Redis and is
unit-tested without it; the IO wrappers (``fleet``, ``bus``, ``lease``,
``barrier``) take an injected async Redis client, mirroring ``RedisQueue``.
"""
from __future__ import annotations

from . import barrier_logic, handlers, keys, routing
from .barrier import Barrier
from .bus import DirectiveBus
from .fleet import Fleet
from .lease import Lease, lease
from .models import (
    BarrierSpec,
    BarrierState,
    Directive,
    DirectiveStatus,
    DirectiveTarget,
    FleetView,
    InstanceSnapshot,
)
from .worker_integration import CoordWorkerMixin

__all__ = [
    "Barrier",
    "BarrierSpec",
    "BarrierState",
    "CoordWorkerMixin",
    "Directive",
    "DirectiveBus",
    "DirectiveStatus",
    "DirectiveTarget",
    "Fleet",
    "FleetView",
    "InstanceSnapshot",
    "Lease",
    "barrier_logic",
    "handlers",
    "keys",
    "lease",
    "routing",
]
