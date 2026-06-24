"""Redis key builders for broadcast cooldown + claim locks (pure strings).

Both are keyed per ``(game, scope, message)``. ``scope`` is the de-duplication
unit: the alliance name for alliance-chat messages (many accounts share one
alliance but the chat should see a reminder once), or the literal ``"world"`` for
world/global-chat messages (one post per game across the whole fleet).

* **cooldown** (``…:sent:…``) — written with ``EX`` after a successful post; its
  mere presence means "still cooling down", so other accounts' ticks skip it.
* **claim** (``…:claim:…``) — short-lived ``SET NX EX`` taken just before posting,
  so two accounts ticking in the same window can't both post (race guard).
"""
from __future__ import annotations

import re

PREFIX = "bcast"


def _slug(value: str) -> str:
    """Collapse a scope/game token to a Redis-safe string (no spaces/colons)."""
    s = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return s or "none"


def sent_key(game: str, scope: str, message_id: str) -> str:
    """Per-scope cooldown stamp (TTL = the message's cooldown/cron interval)."""
    return f"{PREFIX}:{_slug(game)}:{_slug(scope)}:sent:{message_id}"


def claim_key(game: str, scope: str, message_id: str) -> str:
    """Per-scope same-tick claim lock (short TTL; SET NX EX before posting)."""
    return f"{PREFIX}:{_slug(game)}:{_slug(scope)}:claim:{message_id}"
