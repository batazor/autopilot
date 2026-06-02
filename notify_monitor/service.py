"""Background monitor: poll -> parse -> classify -> publish/store.

Runs in a daemon thread. Settings (poll interval, ADB serial/path, enabled
flag) are read fresh each cycle from the DB so they hot-reload. Deduplication
uses a per-notification hash kept in a time-bounded in-memory set so the same
sticky notification isn't processed every cycle.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from . import adb_reader, db, parser
from .logging_setup import get_logger
from .publisher import RedisPublisher

log = get_logger("service")

# A notification stays "seen" for this long; after that we'd re-emit it (a sticky
# notification that lingers longer than this is rare and re-emitting is harmless).
_DEDUP_TTL = 600.0
_DEDUP_MAX = 5000


class MonitorService:
    def __init__(self, publisher: RedisPublisher | None = None) -> None:
        self.publisher = publisher or RedisPublisher()
        self.matcher = parser.PatternMatcher()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._seen: OrderedDict[str, float] = OrderedDict()
        # live status, surfaced to the UI
        self.last_poll_ts: float | None = None
        self.last_poll_human: str | None = None
        self.last_error: str | None = None
        # last distinct AdbError message, to suppress repeated warnings while a
        # device stays offline (reset once a poll succeeds or a new error type appears)
        self._last_adb_error: str | None = None
        self.last_cycle_count = 0
        self.cycles = 0
        self.running = False

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="nm-monitor", daemon=True)
        self._thread.start()
        self.running = True
        log.info("Monitor thread started")

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        log.info("Monitor stop requested")

    # --- dedup -------------------------------------------------------------

    def _is_new(self, key: str) -> bool:
        now = time.monotonic()
        # prune expired
        while self._seen:
            _oldest_key, ts = next(iter(self._seen.items()))
            if now - ts > _DEDUP_TTL or len(self._seen) > _DEDUP_MAX:
                self._seen.popitem(last=False)
            else:
                break
        if key in self._seen:
            return False
        self._seen[key] = now
        return True

    # --- main loop ---------------------------------------------------------

    def _run(self) -> None:
        self.publisher.ping()
        while not self._stop.is_set():
            interval = self._poll_interval()
            try:
                if db.get_setting("monitor_enabled", "1") == "1":
                    self.poll_once()
                else:
                    log.debug("Monitor disabled via settings; idle")
            except adb_reader.AdbError as exc:
                # Device offline / not connected is an expected, transient
                # condition (emulator down, no `adb connect`). Log a concise
                # warning once per distinct message instead of a full traceback
                # every cycle.
                msg = str(exc)
                self.last_error = msg
                if msg != self._last_adb_error:
                    self._last_adb_error = msg
                    log.warning("ADB unavailable, retrying every %ds: %s", interval, msg)
            except Exception as exc:
                self.last_error = str(exc)
                self._last_adb_error = None
                log.exception("Poll cycle failed")
            else:
                self._last_adb_error = None
            self._stop.wait(timeout=max(1, interval))

    def _poll_interval(self) -> int:
        try:
            return max(1, int(db.get_setting("poll_interval", "10") or 10))
        except (TypeError, ValueError):
            return 10

    def poll_once(self) -> dict[str, Any]:
        """One poll cycle. Returns a small summary (also used by manual trigger)."""
        adb_path = db.get_setting("adb_path", "adb") or "adb"
        serial = db.get_setting("adb_serial", "") or ""

        raw = adb_reader.dump_notifications(adb_path=adb_path, serial=serial)
        notifs = parser.parse_dumpsys(raw)

        # known active players per game for nickname resolution
        players_by_game: dict[str, list[str]] = {}
        for p in db.list_players():
            players_by_game.setdefault(p["game"], []).append(p["nickname"])

        recognized = unrecognized = skipped = 0
        cycle_keys: set[str] = set()

        for n in notifs:
            key = n.dedup_key()
            # within-cycle dedup + cross-cycle dedup
            if key in cycle_keys or not self._is_new(key):
                continue
            cycle_keys.add(key)
            recognized_one, unrec_one, skipped_one = self._handle(n, players_by_game)
            recognized += recognized_one
            unrecognized += unrec_one
            skipped += skipped_one

        self.last_poll_ts = time.time()
        self.last_poll_human = time.strftime("%Y-%m-%d %H:%M:%S")
        self.last_error = None
        self.last_cycle_count = recognized + unrecognized + skipped
        self.cycles += 1
        summary = {
            "notifications": len(notifs),
            "recognized": recognized,
            "unrecognized": unrecognized,
            "skipped": skipped,
        }
        log.info("Cycle %d: %s", self.cycles, summary)
        return summary

    def _handle(self, n: parser.Notification, players_by_game: dict[str, list[str]]) -> tuple[int, int, int]:
        """Process one notification. Returns (recognized, unrecognized, skipped)."""
        raw_text = n.raw_text
        if not raw_text:
            return (0, 0, 0)

        match = self.matcher.match(raw_text, n.game)
        if not match:
            db.add_unrecognized(n.game, raw_text)
            log.info("Unrecognized [%s]: %s", n.game, raw_text[:120])
            return (0, 1, 0)

        # nickname: pattern-provided > known players > heuristics
        nickname = match.nickname or parser.extract_nickname(
            raw_text, n.game, players_by_game.get(n.game, [])
        )

        player = db.ensure_player(nickname, n.game)
        if player and not player["active"]:
            log.debug("Skipping inactive player '%s' (%s)", nickname, n.game)
            return (0, 0, 1)

        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        db.add_event(n.game, nickname, match.event_type, raw_text, ts)
        self.publisher.publish_event(n.game, nickname, match.event_type, raw_text, ts)
        return (1, 0, 0)

    # --- status ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "cycles": self.cycles,
            "last_poll_ts": self.last_poll_ts,
            "last_poll_human": self.last_poll_human,
            "last_cycle_count": self.last_cycle_count,
            "last_error": self.last_error,
            "poll_interval": self._poll_interval(),
            "seen_cache": len(self._seen),
            "redis": self.publisher.status(),
        }
