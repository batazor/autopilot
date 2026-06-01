"""Notification monitoring service for Whiteout Survival and Kingshot.

Polls Android notifications via ADB, parses them, matches against per-game
event patterns (loaded hot-reloadably from SQLite), publishes recognized
events to Redis, and stores unrecognized ones for later review.

Modules:
    config          -- defaults + game registry
    logging_setup   -- file + console logging
    db              -- SQLite data layer (players, patterns, events, ...)
    adb_reader      -- `adb shell dumpsys notification` reader
    parser          -- dumpsys parsing, nickname extraction, pattern matching
    publisher       -- Redis event publisher
    service         -- background polling loop
    app             -- FastAPI web UI + JSON API
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
