"""Pure data model for one alliance-broadcast message (game-agnostic).

No IO. The SQLite row in :mod:`~.db` round-trips to/from this dataclass; the
:mod:`~.engine` selection logic consumes only these (so it's unit-tested without
a database). Keep this serialisable and free of Redis/SQL imports.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

# Which game(s) a message applies to.
SCOPE_WOS = "wos"
SCOPE_KINGSHOT = "kingshot"
SCOPE_ALL = "all"
VALID_SCOPES: tuple[str, ...] = (SCOPE_WOS, SCOPE_KINGSHOT, SCOPE_ALL)

# How a message is triggered.
TRIGGER_CRON = "cron"      # fire on a fixed cadence (the cron interval)
TRIGGER_EVENT = "event"    # fire while an ``eval_cond`` expression is true
VALID_TRIGGERS: tuple[str, ...] = (TRIGGER_CRON, TRIGGER_EVENT)

# Which in-game chat tab the message posts to.
CHANNEL_ALLIANCE = "alliance"  # alliance chat — de-duplicated per alliance
CHANNEL_WORLD = "world"        # world/global chat (e.g. recruiting) — one post per game
VALID_CHANNELS: tuple[str, ...] = (CHANNEL_ALLIANCE, CHANNEL_WORLD)

# UI grouping only — not behavioural.
CATEGORIES: tuple[str, ...] = ("event", "tip", "daily", "custom")

# In-game chat input is length-capped; keep messages short and plain.
MAX_TEXT_LEN = 200


@dataclass(frozen=True, slots=True)
class BroadcastMessage:
    """One configured reminder: the text plus when it should be posted."""

    id: str
    title: str                          # short label for the dashboard list
    text: str                           # the actual chat message (free-form)
    category: str = "custom"
    game_scope: str = SCOPE_ALL         # wos | kingshot | all
    channel: str = CHANNEL_ALLIANCE     # alliance | world
    trigger_kind: str = TRIGGER_CRON    # cron | event
    cron: str = ""                      # cron expr when trigger_kind == cron
    cond: str = ""                      # eval_cond expr when trigger_kind == event
    # Pre-event lead: when > 0 on an event message, fire while the event STARTS
    # within this many hours (a heads-up), instead of while it is already live.
    lead_hours: int = 0
    cooldown_minutes: int = 360         # min spacing between sends to one scope
    priority: int = 100                 # lower wins when several are due at once
    # Quiet hours in server-local time (UTC+8); -1 disables. When set, the message
    # is suppressed while the server clock is inside [start, end) (wraps past midnight).
    quiet_start_hour: int = -1
    quiet_end_hour: int = -1
    # Restrict an alliance-channel message to one alliance ("" = every alliance).
    target_alliance: str = ""
    enabled: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0

    def applies_to_game(self, game: str) -> bool:
        """True if this message should be considered for ``game``."""
        return self.game_scope == SCOPE_ALL or self.game_scope == game

    def is_pre_event(self) -> bool:
        """An event message configured as a pre-event heads-up (lead > 0)."""
        return self.trigger_kind == TRIGGER_EVENT and self.lead_hours > 0

    def to_dict(self) -> dict:
        return asdict(self)
