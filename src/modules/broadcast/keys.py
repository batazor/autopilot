"""Redis key builders for broadcast cooldown + claim locks (pure strings).

Both are keyed per ``(game, alliance, message)`` — the alliance, not the account,
is the unit of de-duplication: many accounts may be in one alliance but the chat
should see a reminder once.

* **cooldown** (``…:sent:…``) — written with ``EX`` after a successful post; its
  mere presence means "still cooling down", so other accounts' ticks skip it.
* **claim** (``…:claim:…``) — short-lived ``SET NX EX`` taken just before posting,
  so two accounts ticking in the same window can't both post (race guard).
"""
from __future__ import annotations

import re

PREFIX = "bcast"


def _slug(value: str) -> str:
    """Collapse an alliance name to a Redis-safe token (no spaces/colons)."""
    s = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return s or "none"


def sent_key(game: str, alliance: str, message_id: str) -> str:
    """Per-alliance cooldown stamp (TTL = the message's cooldown/cron interval)."""
    return f"{PREFIX}:{_slug(game)}:{_slug(alliance)}:sent:{message_id}"


def claim_key(game: str, alliance: str, message_id: str) -> str:
    """Per-alliance same-tick claim lock (short TTL; SET NX EX before posting)."""
    return f"{PREFIX}:{_slug(game)}:{_slug(alliance)}:claim:{message_id}"
