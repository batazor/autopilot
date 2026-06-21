"""WoS step kinds + their wired/deferred status.

The generic saga engine treats ``Step.kind`` as an opaque string; this is where
WoS gives them meaning. ``WIRED`` kinds dispatch to a real scenario today;
``DEFERRED`` kinds need on-device scenarios that don't exist yet (account switch,
troop recall, attack-at-coords, reinforce) — they're carried through the planner
and traced, but not posted, so the foundation is testable on synthetic state
without firing anything on a device.
"""
from __future__ import annotations

# Step kinds the catalog uses.
RUN_SCENARIO = "run_scenario"     # run an existing DSL scenario (event gather/rally/claim)
SWITCH_PLAYER = "switch_player"   # switch the active account on a device
RECALL = "recall"                 # farm withdraws troops (city empties)
ATTACK_COORDS = "attack_coords"   # fighter attacks a tile/city at coordinates
REINFORCE = "reinforce"           # helper marches troops to an ally

# Kinds that dispatch to a real on-device action today.
WIRED: frozenset[str] = frozenset({RUN_SCENARIO})

# Kinds blocked on on-device scenarios (+ map coordinates for attack/reinforce).
# Each lands later by flipping a catalog placeholder to a real scenario key —
# no engine change. Listed here so the dispatcher can trace "deferred" precisely.
DEFERRED: frozenset[str] = frozenset({SWITCH_PLAYER, RECALL, ATTACK_COORDS, REINFORCE})


def is_wired(kind: str) -> bool:
    return kind in WIRED
