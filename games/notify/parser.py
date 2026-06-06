"""Parse `dumpsys notification` output and classify notifications.

The dumpsys format varies across Android versions, so the parser is tolerant:
it walks the text record-by-record (each `NotificationRecord(... pkg=X ...)`
starts a record) and pulls the title/text/ticker extras with line regexes.

Public surface:
    parse_dumpsys(text)             -> list[Notification]
    PatternMatcher (hot-reloadable) -> match() against active DB patterns
    extract_nickname(...)           -> best-effort nickname
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config, db
from .logging_setup import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable
    from re import Pattern

log = get_logger("parser")

# A new record starts here; capture the package id.
_RECORD_RE = re.compile(r"NotificationRecord\([^)]*\bpkg=([A-Za-z0-9_.]+)")
# extras values look like `android.title=Foo` or `android.title=Foo (String)`.
_EXTRA_RES = {
    "title": re.compile(r"android\.title=(.*)"),
    "text": re.compile(r"android\.text=(.*)"),
    "ticker": re.compile(r"\btickerText=(.*)"),
    "bigtext": re.compile(r"android\.bigText=(.*)"),
    "subtext": re.compile(r"android\.subText=(.*)"),
}
# dumpsys renders extras with their Java type. Two shapes show up across
# Android builds / redaction modes:
#   "Storehouse Supply ready (String)"   -> trailing type annotation
#   "String (Storehouse Supply ready)"   -> leading "<Type> (value)" wrapper
_TYPE_NAMES = "String|CharSequence|SpannableString|SpannableStringBuilder|Spanned"
_TYPE_SUFFIX_RE = re.compile(rf"\s*\((?:{_TYPE_NAMES})\)\s*$")
_TYPE_PREFIX_RE = re.compile(rf"^(?:{_TYPE_NAMES})\s*\((.*)\)$", re.DOTALL)

# Heuristic nickname extractors, tried in order when no DB pattern names one.
# The salutation form comes first: WoS addresses the player by name in the
# body, e.g. "Honored paradox, Storehouse supplies are ready." → "paradox".
_NICK_HEURISTICS = [
    # "Honored <name>," / "Dear <name>!" greeting (Century in-game salutation).
    re.compile(r"\b(?:Honored|Dear|Greetings,?)\s+([A-Za-z0-9_\-.]{1,32})\s*[,!.]", re.IGNORECASE),
    re.compile(r"^\s*\[([^\]\r\n]{1,32})\]"),          # [Nickname] ...
    re.compile(r"^\s*([A-Za-z0-9_\-.]{2,32})\s*[:,]"), # Nickname: ... / Nickname, ...
    re.compile(r"\bplayer\s+([A-Za-z0-9_\-.]{2,32})", re.IGNORECASE),
]


@dataclass
class Notification:
    package: str
    game: str
    title: str = ""
    text: str = ""
    ticker: str = ""
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def raw_text(self) -> str:
        """Human-readable combined text used for matching + storage."""
        parts = [p for p in (self.title, self.text or self.extras.get("bigtext", ""),
                             self.ticker) if p]
        # de-dup while preserving order
        seen, out = set(), []
        for p in parts:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return " — ".join(out) if out else (self.ticker or "")

    def dedup_key(self) -> str:
        h = hashlib.sha1(f"{self.package}|{self.raw_text}".encode("utf-8", "ignore"))
        return h.hexdigest()


def _clean(value: str) -> str:
    v = value.strip()
    m = _TYPE_PREFIX_RE.match(v)
    if m:
        v = m.group(1).strip()
    return _TYPE_SUFFIX_RE.sub("", v).strip()


def _split_records(text: str) -> Iterable[tuple[str, list[str]]]:
    """Yield (package, lines) per NotificationRecord block."""
    current_pkg: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _RECORD_RE.search(line)
        if m:
            if current_pkg is not None:
                yield current_pkg, buf
            current_pkg = m.group(1)
            buf = [line]
        elif current_pkg is not None:
            buf.append(line)
    if current_pkg is not None:
        yield current_pkg, buf


def parse_dumpsys(text: str) -> list[Notification]:
    """Extract notifications for known game packages from dumpsys output."""
    pkg_map = config.all_packages()
    notifs: list[Notification] = []
    for pkg, lines in _split_records(text):
        game = pkg_map.get(pkg)
        if not game:
            continue
        extras: dict[str, str] = {}
        for line in lines:
            for key, rex in _EXTRA_RES.items():
                if key in extras:
                    continue
                m = rex.search(line)
                if m:
                    val = _clean(m.group(1))
                    if val and val.lower() not in ("null", "none"):
                        extras[key] = val
        if not extras:
            continue
        notifs.append(
            Notification(
                package=pkg,
                game=game,
                title=extras.get("title", ""),
                text=extras.get("text", ""),
                ticker=extras.get("ticker", ""),
                extras=extras,
            )
        )
    return notifs


def extract_nickname(raw_text: str, game: str, known_players: Iterable[str] | None = None) -> str:
    """Best-effort nickname extraction.

    1. If a monitored player's nickname appears in the text, use it (most
       reliable, since players are curated in the UI).
    2. Otherwise apply leading-bracket / leading-token heuristics.
    3. Fall back to ``"unknown"``.
    """
    text = raw_text or ""
    low = text.lower()
    # 1. known players (longest first so "BigKing" wins over "King")
    for nick in sorted(known_players or [], key=len, reverse=True):
        if nick and nick.lower() in low:
            return nick
    # 2. heuristics
    for rex in _NICK_HEURISTICS:
        m = rex.search(text)
        if m:
            return m.group(1).strip()
    return "unknown"


@dataclass
class MatchResult:
    event_type: str
    pattern_id: int
    pattern_regex: str
    nickname: str | None = None  # if the pattern declared a `nickname` group
    scenario: str = ""           # DSL scenario key to push, "" = none


class PatternMatcher:
    """Compiles active patterns from the DB and matches text against them.

    Patterns are cached and refreshed when the DB changes or after a short TTL,
    so edits made in the UI take effect within seconds — no restart needed.
    """

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._compiled: dict[str, list[tuple[int, str, str, Pattern[str]]]] = {}
        self._loaded_at = 0.0

    def _load(self) -> None:
        compiled: dict[str, list[tuple[int, str, str, Pattern[str]]]] = {}
        for row in db.list_patterns(active_only=True):
            try:
                rex = re.compile(row["pattern_regex"], re.IGNORECASE)
            except re.error as exc:
                log.warning("Skipping invalid pattern id=%s: %s", row["id"], exc)
                continue
            compiled.setdefault(row["game"], []).append(
                (row["id"], row["event_type"], (row.get("scenario") or "").strip(), rex)
            )
        self._compiled = compiled
        self._loaded_at = time.monotonic()
        log.debug("Loaded %d active pattern groups", len(compiled))

    def refresh(self) -> None:
        """Force a reload on the next match (used after UI edits)."""
        with self._lock:
            self._loaded_at = 0.0

    def _ensure_fresh(self) -> None:
        if time.monotonic() - self._loaded_at >= self._ttl:
            with self._lock:
                if time.monotonic() - self._loaded_at >= self._ttl:
                    self._load()

    def match(self, raw_text: str, game: str) -> MatchResult | None:
        self._ensure_fresh()
        for pattern_id, event_type, scenario, rex in self._compiled.get(game, []):
            m = rex.search(raw_text)
            if m:
                nick = None
                if "nickname" in rex.groupindex:
                    nick = (m.group("nickname") or "").strip() or None
                return MatchResult(event_type, pattern_id, rex.pattern, nick, scenario)
        return None
