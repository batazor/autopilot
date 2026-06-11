"""Static defaults and the per-game registry.

Runtime-tunable values (poll interval, ADB serial, package overrides) live in
the SQLite ``settings`` table and are read fresh on every poll cycle so they
hot-reload without a restart. The constants here are only the seed defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- paths -----------------------------------------------------------------

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PACKAGE_DIR / "data" / "notify_monitor.db"
DEFAULT_LOG_PATH = PACKAGE_DIR / "data" / "notify_monitor.log"
STATIC_DIR = PACKAGE_DIR / "static"

DB_PATH = Path(os.environ.get("NM_DB_PATH", DEFAULT_DB_PATH))
LOG_PATH = Path(os.environ.get("NM_LOG_PATH", DEFAULT_LOG_PATH))

# The bot's canonical state DB (devices + per-player gamer state). Read-only
# here: used to resolve a notification's nickname -> gamer.id (queue player_id)
# and the monitored adb serial -> device name (queue instance_id).
# Package lives at src/modules/notify/, so the repo root is three levels up.
REPO_ROOT = PACKAGE_DIR.parent.parent.parent
DEFAULT_STATE_DB_PATH = REPO_ROOT / "db" / "state" / "state.db"
STATE_DB_PATH = Path(os.environ.get("NM_STATE_DB_PATH", DEFAULT_STATE_DB_PATH))

# --- runtime defaults (seeded into the settings table on first run) --------

DEFAULT_POLL_INTERVAL = 10          # seconds
DEFAULT_ADB_SERIAL = ""             # empty -> default device (`adb` with no -s)
DEFAULT_ADB_PATH = os.environ.get("NM_ADB_PATH", "adb")
REDIS_URL = os.environ.get("NM_REDIS_URL", os.environ.get("WOS_REDIS_URL", "redis://127.0.0.1:6379/0"))


# Recognized event_type -> DSL scenario key to enqueue directly onto the bot's
# worker queue (``wos:queue:<instance>``). Only event types listed here trigger
# a scenario push; every other recognized event is stored/published as an
# informational event only. The scenario key is the YAML filename (no ext) under
# the game's module ``scenarios/`` dir.
EVENT_SCENARIOS: dict[str, dict[str, str]] = {
    "wos": {
        "intel_lighthouse": "intel_lighthouse",
    },
}

# Priority for notification-pushed scenarios (matches the DSL default band).
PUSH_SCENARIO_PRIORITY = 80_000


@dataclass(frozen=True)
class Game:
    """A monitored game and the Android packages that map to it."""

    id: str                          # short id; also the Redis channel prefix
    name: str
    packages: tuple[str, ...]        # canonical first, then aliases
    seed_patterns: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    # seed_patterns entries: (event_type, pattern_regex, description)


# Common event patterns. They are deliberately broad and case-insensitive; the
# operator refines them through the Patterns tab. Use a named group `nickname`
# to let a pattern carry its own nickname extraction.
_COMMON_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("construction_complete", r"construction.*(complete|finished|done)", "Building construction finished"),
    ("upgrade_complete", r"upgrade.*(complete|finished|done)", "Building upgrade finished"),
    ("troops_trained", r"(troops?|training).*(complete|trained|finished|ready)", "Troop training finished"),
    ("research_done", r"research.*(complete|finished|done)", "Research finished"),
    ("gathering_complete", r"(gathering|gather|march).*(complete|finished|returned|done)",
     "Resource gathering finished"),
    ("healing_complete", r"heal(ing)?.*(complete|finished|done)", "Troop healing finished"),
    ("infirmary_overflowing", r"(infirmary.*(overflow|full)|injured troops.*(died|dying|have died))",
     "Infirmary overflowing / injured troops dying"),
    ("storehouse_supply", r"storehouse.*(ready|claim|supplies)", "Storehouse supplies ready to claim"),
    ("intel_lighthouse", r"(intel|lighthouse).*(new|intel|check)", "New Intel in the Lighthouse"),
    ("attack_incoming", r"(attack|rally|incoming|under attack|enemy).*(incoming|detected|approaching|spotted)?",
     "Incoming attack / rally"),
    ("shield_expiring", r"(shield|protection).*(expir|end|run out)", "Shield about to expire"),
    ("stamina_full", r"(stamina|energy).*(full|recovered)", "Stamina recovered"),
    ("help_request", r"(help|alliance help).*(request|needed)", "Alliance help requested"),
    ("injured_healed", r"(injured.*heal|troops are healed|been healed)", "Injured troops healed"),
    ("stamina_supply", r"stamina.*(supply|ready to be claimed|feast|replenish)",
     "Limited-time stamina supply ready to claim"),
    ("scout_detected", r"(is scouting|scout(ing)?.*(your city|detected|spotted))", "Enemy scouting your city"),
    ("idle_income", r"idle income.*(max|claim|ready|explor)", "Idle exploration income ready to claim"),
    ("offline_income", r"offline\s+income.*(max|maxed|claim|ready)", "Offline income ready to claim"),
    ("pet_adventure", r"pet.*adventure.*(complete|finished|done)", "Pet adventure completed"),
    ("trek_supply", r"trek supplies?.*(ready|claim)", "Trek supplies ready to claim"),
    ("alliance_gathering_node", r"secured alliance gathering node.*(placed|gather)",
     "Secured alliance gathering node placed"),
    ("sanctuary_battle", r"(sanctuary battle|battle for fortress).*(begun|get ready|started)",
     "Kingshot sanctuary battle / fortress battle started"),
)

GAMES: dict[str, Game] = {
    "wos": Game(
        id="wos",
        name="Whiteout Survival",
        packages=("com.gof.global", "com.xyz.gof"),
        seed_patterns=_COMMON_PATTERNS,
    ),
    "kingshot": Game(
        id="kingshot",
        name="Kingshot",
        packages=("com.run.tower.defense",),
        seed_patterns=_COMMON_PATTERNS,
    ),
}


def game_for_package(pkg: str) -> str | None:
    """Reverse lookup: game id whose package set contains ``pkg``."""
    pkg = (pkg or "").strip()
    for game in GAMES.values():
        if pkg in game.packages:
            return game.id
    return None


def all_packages() -> dict[str, str]:
    """Map every known package -> game id (built from :data:`GAMES`)."""
    out: dict[str, str] = {}
    for game in GAMES.values():
        for pkg in game.packages:
            out[pkg] = game.id
    return out
