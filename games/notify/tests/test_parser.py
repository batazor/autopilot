"""Tests for dumpsys parsing, nickname extraction, and pattern matching."""

from __future__ import annotations

import re

import pytest
from games.notify import parser

SAMPLE_DUMPSYS = """
Current Notification List:
  NotificationRecord(0xabc123 pkg=com.gof.global user=UserHandle{0} id=1 tag=null)
    uid=10123
    opPkg=com.gof.global
    extras={
      android.title=KingTazor (String)
      android.text=Construction complete: Furnace upgraded! (String)
      android.showWhen=true (Boolean)
    }
    tickerText=Construction complete (String)
  NotificationRecord(0xdef456 pkg=com.run.tower.defense user=UserHandle{0} id=2 tag=null)
    uid=10124
    extras={
      android.title=[Warlord] (String)
      android.text=Your troops have finished training. (String)
    }
  NotificationRecord(0x999 pkg=com.android.systemui user=UserHandle{0} id=3)
    extras={
      android.title=System (String)
      android.text=Battery low (String)
    }
  NotificationRecord(0x777 pkg=com.gof.global user=UserHandle{0} id=4)
    extras={
      android.title=Survivor99 (String)
      android.text=A wild bear has appeared near your city. (String)
    }
"""


def test_parse_filters_to_known_games():
    notifs = parser.parse_dumpsys(SAMPLE_DUMPSYS)
    pkgs = {n.package for n in notifs}
    assert "com.android.systemui" not in pkgs
    assert pkgs == {"com.gof.global", "com.run.tower.defense"}
    assert len(notifs) == 3


def test_parse_maps_game_and_text():
    notifs = parser.parse_dumpsys(SAMPLE_DUMPSYS)
    wos = next(n for n in notifs if n.title == "KingTazor")
    assert wos.game == "wos"
    assert "Construction complete" in wos.raw_text
    ks = next(n for n in notifs if n.game == "kingshot")
    assert "finished training" in ks.raw_text


def test_dedup_key_stable_and_distinct():
    notifs = parser.parse_dumpsys(SAMPLE_DUMPSYS)
    keys = [n.dedup_key() for n in notifs]
    assert len(set(keys)) == len(keys)  # all distinct
    # stable across re-parse
    again = parser.parse_dumpsys(SAMPLE_DUMPSYS)
    assert [n.dedup_key() for n in again] == keys


@pytest.mark.parametrize(("text", "players", "expected"), [
    ("Construction complete for KingTazor!", ["KingTazor"], "KingTazor"),
    ("[Warlord] troops trained", [], "Warlord"),
    ("Survivor99: bear appeared", [], "Survivor99"),
    ("player BigKing was attacked", [], "BigKing"),
    ("some generic text", [], "unknown"),
    # WoS in-game salutation — the storehouse notification body.
    ("Storehouse Supply ready to be claimed — Honored paradox, Storehouse supplies are ready.", [], "paradox"),
    ("Dear Frostborn! Your shield is expiring.", [], "Frostborn"),
])
def test_extract_nickname(text, players, expected):
    assert parser.extract_nickname(text, "wos", players) == expected


def test_known_player_match_wins_over_heuristic():
    # heuristic would say "System", but the known player is more reliable
    nick = parser.extract_nickname("System alert for Frostborn city", "wos", ["Frostborn"])
    assert nick == "Frostborn"


def test_clean_strips_type_suffix():
    assert parser._clean("Hello world (String)") == "Hello world"
    assert parser._clean("Plain text") == "Plain text"


def test_clean_strips_type_prefix_wrapper():
    # Real WoS device format: dumpsys renders extras as "<Type> (value)".
    assert (
        parser._clean("String (Storehouse Supply ready to be claimed)")
        == "Storehouse Supply ready to be claimed"
    )
    assert parser._clean("SpannableString (Claim them now!)") == "Claim them now!"
    # A value that itself ends in parens still unwraps to the inner content.
    assert parser._clean("String (Event (limited))") == "Event (limited)"
    # Non-wrapped values with parens are left intact.
    assert parser._clean("Welcome (Beta)") == "Welcome (Beta)"


def test_parses_real_storehouse_notification_cleanly():
    # Captured from a live device: title/text arrive wrapped as "String (...)".
    dump = (
        "  NotificationRecord(0x55 pkg=com.gof.global user=UserHandle{0} id=9)\n"
        "    extras={\n"
        "      android.title=String (Storehouse Supply ready to be claimed)\n"
        "      android.text=String (Honored paradox, Storehouse supplies are ready. Claim them now!)\n"
        "    }\n"
    )
    notifs = parser.parse_dumpsys(dump)
    assert len(notifs) == 1
    n = notifs[0]
    assert n.game == "wos"
    assert n.title == "Storehouse Supply ready to be claimed"
    assert "Storehouse supplies are ready" in n.text
    assert "String (" not in n.raw_text


def test_parses_real_troop_training_notification():
    # Captured live from BlueStacks (bs1, pkg com.xyz.gof): a "Troop Training
    # Completed" notification whose body addresses the player by salutation.
    dump = (
        "  NotificationRecord(0x42 pkg=com.xyz.gof user=UserHandle{0} id=7)\n"
        "    extras={\n"
        "      android.title=Troop Training Completed (String)\n"
        "      android.text=Honored batazor, Supreme Lancer's training is complete! (String)\n"
        "    }\n"
    )
    notifs = parser.parse_dumpsys(dump)
    assert len(notifs) == 1
    n = notifs[0]
    assert n.game == "wos"
    assert n.title == "Troop Training Completed"
    assert "Supreme Lancer" in n.text
    assert parser.extract_nickname(n.raw_text, "wos") == "batazor"


def test_matcher_recognizes_troop_training():
    # The seeded `troops_trained` regex must classify the live bs1 notification.
    m = _FakeMatcher([(1, "wos", "troops_trained",
                       r"(troops?|training).*(complete|trained|finished|ready)")])
    res = m.match(
        "Troop Training Completed — Honored batazor, Supreme Lancer's training is complete!",
        "wos",
    )
    assert res is not None
    assert res.event_type == "troops_trained"


def test_matcher_recognizes_storehouse_supply():
    m = _FakeMatcher([(1, "wos", "storehouse_supply", r"storehouse.*(ready|claim|supplies)")])
    res = m.match(
        "Storehouse Supply ready to be claimed — Honored paradox, Storehouse supplies are ready.",
        "wos",
    )
    assert res is not None
    assert res.event_type == "storehouse_supply"


class _FakeMatcher(parser.PatternMatcher):
    """PatternMatcher with patterns injected directly (no DB)."""

    def __init__(self, patterns) -> None:
        super().__init__(ttl_seconds=999)
        compiled = {}
        for pid, game, et, rx in patterns:
            compiled.setdefault(game, []).append((pid, et, "", re.compile(rx, re.IGNORECASE)))
        self._compiled = compiled
        import time
        self._loaded_at = time.monotonic()

    def _load(self):  # never hit the DB in tests
        pass


def test_matcher_returns_first_match():
    m = _FakeMatcher([
        (1, "wos", "construction_complete", r"construction.*complete"),
        (2, "wos", "troops_trained", r"troops.*trained"),
    ])
    res = m.match("Construction complete: Furnace upgraded!", "wos")
    assert res is not None
    assert res.event_type == "construction_complete"
    assert res.pattern_id == 1


def test_matcher_no_match_returns_none():
    m = _FakeMatcher([(1, "wos", "x", r"nothing")])
    assert m.match("totally different", "wos") is None
    assert m.match("nothing here", "kingshot") is None  # wrong game


def test_matcher_named_nickname_group():
    m = _FakeMatcher([(1, "wos", "attack", r"(?P<nickname>\w+) is under attack")])
    res = m.match("Frostborn is under attack", "wos")
    assert res.nickname == "Frostborn"
