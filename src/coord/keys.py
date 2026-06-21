"""Redis key builders + TTL constants for the cross-instance coordination layer.

Single source of truth for every ``wos:coord:*`` key, mirroring
``scheduler.queue._queue_key`` / ``dashboard.redis_client._queue_key``. Pure
strings — no Redis, no IO — so both the worker and the scheduler import the
same builders and can't drift.

The fleet *view* is derived from the existing per-instance state hash
(``wos:instance:<id>:state``) rather than a parallel hash: coord only *adds*
fields to it (and reads the ones the worker already writes), so there is one
source of truth for "what is this instance doing". Everything genuinely new
lives under the ``wos:coord:`` subtree.
"""
from __future__ import annotations

# --- Namespace ----------------------------------------------------------------
PREFIX = "wos:coord"


# --- Fleet registry (fields on the existing instance-state hash) --------------
def instance_state_key(instance_id: str) -> str:
    """The existing ``wos:instance:<id>:state`` hash (shared with the worker/UI)."""
    iid = str(instance_id or "").strip() or "unknown"
    return f"wos:instance:{iid}:state"


# Fields coord *adds* to the instance-state hash (writer: worker heartbeat).
FIELD_ALLIANCE_TAG = "alliance_tag"
FIELD_MARCH_SLOTS_TOTAL = "march_slots_total"
FIELD_MARCH_SLOTS_FREE = "march_slots_free"
# Coord-specific heartbeat, decoupled from the UI ``last_seen_at`` so a coord
# schema change can never perturb the dashboard's liveness display.
FIELD_COORD_SEEN_AT = "coord_seen_at"

# Existing fields coord only *reads*.
FIELD_ACTIVE_PLAYER = "active_player"
FIELD_GAME = "game"
FIELD_CURRENT_SCREEN = "current_screen"
FIELD_STATE = "state"
FIELD_PAUSED = "paused"

# Reverse index: fid -> "<instance_id>|<unix_ts>" (the device a fid is ACTIVE on
# right now). Source of truth stays each instance's own ``active_player``; this
# is an advisory, self-correcting index (see fleet.reap_stale).
FID_ACTIVE_KEY = f"{PREFIX}:fid_active"


# --- Directive bus ------------------------------------------------------------
def directive_inbox_key(instance_id: str) -> str:
    """Per-instance directive LIST (LPUSH by poster, RPOP by the worker drain)."""
    iid = str(instance_id or "").strip() or "unknown"
    return f"{PREFIX}:directive:{iid}"


def directive_seen_key(instance_id: str) -> str:
    """Per-instance dedup SET of consumed directive ids (SADD-claim, TTL'd)."""
    iid = str(instance_id or "").strip() or "unknown"
    return f"{PREFIX}:directive:seen:{iid}"


def directive_status_key(directive_id: str) -> str:
    """Per-directive status HASH the poster polls; TTL'd so it self-cleans."""
    did = str(directive_id or "").strip() or "unknown"
    return f"{PREFIX}:directive:status:{did}"


# Append-only audit STREAM of every directive/barrier/lease event — durable,
# ordered, cursor-tailable observability (the reason it's a Stream, not a list).
AUDIT_STREAM = f"{PREFIX}:audit"


# --- Lease (generalized gift-code SET NX EX) ----------------------------------
def lease_key(name: str) -> str:
    nm = str(name or "").strip() or "unnamed"
    return f"{PREFIX}:lease:{nm}"


# --- Barrier / rendezvous -----------------------------------------------------
def barrier_key(barrier_id: str) -> str:
    bid = str(barrier_id or "").strip() or "unknown"
    return f"{PREFIX}:barrier:{bid}"


def barrier_arrived_key(barrier_id: str) -> str:
    bid = str(barrier_id or "").strip() or "unknown"
    return f"{PREFIX}:barrier:{bid}:arrived"


def barrier_events_channel(barrier_id: str) -> str:
    bid = str(barrier_id or "").strip() or "unknown"
    return f"{PREFIX}:barrier:{bid}:events"


# --- TTLs / tunables ----------------------------------------------------------
DIRECTIVE_SEEN_TTL_S = 3600          # dedup window for redelivered directives
DIRECTIVE_STATUS_TTL_S = 3600        # status hash self-clean
AUDIT_MAXLEN = 10_000                # approximate stream trim
FLEET_STALE_AFTER_S = 15.0           # ~7 missed 2s heartbeats → offline
BARRIER_GRACE_S = 30.0               # extra TTL past a barrier deadline before reap
