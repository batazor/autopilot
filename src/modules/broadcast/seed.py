"""Starter broadcast templates (English) the dashboard can insert on demand.

The catalog is SQLite-only and operator-edited, so these are *not* auto-applied —
:func:`seed_defaults` is invoked from the ``POST /api/broadcast/seed`` endpoint
(the "Add starter templates" button) and only inserts ids that don't exist yet,
so it never clobbers edits. Texts are plain English placeholders; the operator
rewrites them (any language) in the CRUD editor.
"""
from __future__ import annotations

from . import db
from .models import (
    SCOPE_ALL,
    TRIGGER_CRON,
    TRIGGER_EVENT,
    BroadcastMessage,
)

# Event slugs follow games.wos.core.calendar.schedule.event_flag():
# the in-game event name, lowercased, non-alphanumerics → "_", prefixed "event_".
STARTER_MESSAGES: tuple[BroadcastMessage, ...] = (
    BroadcastMessage(
        id="starter_daily_tasks",
        title="Daily tasks reminder",
        text="Reminder: finish your Daily Missions and claim VIP points before reset!",
        category="daily",
        game_scope=SCOPE_ALL,
        trigger_kind=TRIGGER_CRON,
        cron="0 */12 * * *",          # every 12 hours
        cooldown_minutes=0,
        priority=50,
    ),
    BroadcastMessage(
        id="starter_hero_leveling",
        title="Hero leveling tip",
        text=(
            "Tip: spend Hero EXP on your main rally captain first, and use "
            "Essence Stones from the Lucky Wheel — it's the cheapest path to 5★."
        ),
        category="tip",
        game_scope=SCOPE_ALL,
        trigger_kind=TRIGGER_CRON,
        cron="15 */8 * * *",          # every 8 hours, at minute 15
        cooldown_minutes=0,
        priority=60,
    ),
    BroadcastMessage(
        id="starter_alliance_help",
        title="Help allies reminder",
        text="Don't forget to tap Alliance Help and donate Alliance Tech for points!",
        category="daily",
        game_scope=SCOPE_ALL,
        trigger_kind=TRIGGER_CRON,
        cron="0 */6 * * *",           # every 6 hours
        cooldown_minutes=0,
        priority=55,
    ),
    BroadcastMessage(
        id="starter_foundry_battle",
        title="Foundry Battle live",
        text="⚔️ Foundry Battle is live — join in and rack up alliance points!",
        category="event",
        game_scope=SCOPE_ALL,
        trigger_kind=TRIGGER_EVENT,
        cond="event_foundry_battle == 1",
        cooldown_minutes=360,         # at most once per 6h while it stays live
        priority=10,
    ),
    BroadcastMessage(
        id="starter_bear_hunt",
        title="Bear Hunt reminder",
        text="🐻 Bear Hunt is up! Bring your strongest rally and don't miss the window.",
        category="event",
        game_scope=SCOPE_ALL,
        trigger_kind=TRIGGER_EVENT,
        cond="event_bear_hunt == 1",
        cooldown_minutes=240,
        priority=10,
    ),
)


def seed_defaults() -> list[str]:
    """Insert any starter templates not already present. Returns the ids added."""
    existing = {m.id for m in db.list_messages()}
    added: list[str] = []
    for msg in STARTER_MESSAGES:
        if msg.id in existing:
            continue
        db.upsert_message(msg)
        added.append(msg.id)
    return added
