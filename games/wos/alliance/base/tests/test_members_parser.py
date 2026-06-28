from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2  # type: ignore[import-untyped]

MODULE_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "alliance_members_parser",
    MODULE_DIR / "members_parser.py",
)
assert _spec and _spec.loader
members_parser = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = members_parser
_spec.loader.exec_module(members_parser)


def _frame(name: str):
    image = cv2.imread(str(MODULE_DIR / "references" / name))
    assert image is not None
    return image


def test_parse_members_overview_counts_and_online() -> None:
    parser = members_parser.AllianceMembersParser()
    image = _frame("alliance.members.png")

    ocr = {
        "summary.online": "Online: 15/87",
        "r5.name": "KINGLACUNI",
        "r5.power": "59.1M",
        "r5.level": "Lv. 30",
        "r5.status": "8 hour(s) ago",
        "rank_header.0.rank": "R4",
        "rank_header.0.label": "Alliance Rank 4",
        "rank_header.0.count": "2/5",
        "rank_header.1.rank": "R3",
        "rank_header.1.label": "Alliance Rank 3",
        "rank_header.1.count": "9/74",
        "rank_header.2.rank": "R2",
        "rank_header.2.label": "Alliance Rank 2",
        "rank_header.2.count": "1/1",
        "rank_header.3.rank": "R1",
        "rank_header.3.label": "Alliance Rank 1",
        "rank_header.3.count": "3/6",
        "rank_header.4.rank": "R0",
        "rank_header.4.label": "Application List",
        "rank_header.4.count": "0",
    }

    snapshot = parser.parse(image, ocr)

    assert snapshot.online_count == 15
    assert snapshot.total_count == 87
    assert snapshot.ranks[5].count == 1
    assert snapshot.ranks[4].count == 2
    assert snapshot.ranks[4].max_count == 5
    assert snapshot.ranks[3].count == 9
    assert snapshot.ranks[3].max_count == 74
    assert snapshot.ranks[2].count == 1
    assert snapshot.ranks[1].count == 3
    assert snapshot.ranks[0].count == 0
    assert [m.name for m in snapshot.members] == ["KINGLACUNI"]
    assert snapshot.members[0].online is False


def test_parse_expanded_r4_visible_members() -> None:
    parser = members_parser.AllianceMembersParser()
    image = _frame("alliance.members.r4.png")

    ocr = {
        "summary.online": "Online: 17/87",
        "r5.name": "KINGLACUNI",
        "r5.power": "59.1M",
        "r5.level": "Lv. 30",
        "r5.status": "Online",
        "rank_header.0.rank": "R4",
        "rank_header.0.label": "Alliance Rank 4",
        "rank_header.0.count": "2/5",
        "rank_header.1.rank": "R3",
        "rank_header.1.label": "Alliance Rank 3",
        "rank_header.1.count": "10/74",
        "rank_header.2.rank": "R2",
        "rank_header.2.label": "Alliance Rank 2",
        "rank_header.2.count": "1/1",
        "rank_header.3.rank": "R1",
        "rank_header.3.label": "Alliance Rank 1",
        "rank_header.3.count": "3/6",
        "rank_header.4.rank": "R0",
        "rank_header.4.label": "Application List",
        "rank_header.4.count": "0",
        "member.0.name": "Dark rep",
        "member.0.power": "80.6M",
        "member.0.level": "Lv. 30",
        "member.0.status": "Online",
        "member.1.name": "RedLady",
        "member.1.power": "48.3M",
        "member.1.level": "Lv. 30",
        "member.1.status": "Online",
        "member.2.name": "sorrybeta",
        "member.2.power": "56.5M",
        "member.2.level": "Lv. 30",
        "member.2.status": "4 hour(s) ago",
        "member.3.name": "HarleyQueen",
        "member.3.power": "42.5M",
        "member.3.level": "Lv. 30",
        "member.3.status": "16 hour(s) ago",
        "member.4.name": "KINGLACUNI FARMS",
        "member.4.power": "18.9M",
        "member.4.level": "Lv. 30",
        "member.4.status": "1 day(s) ago",
    }

    snapshot = parser.parse(image, ocr)
    visible_r4 = [m for m in snapshot.members if m.rank == 4]

    assert snapshot.online_count == 17
    assert snapshot.total_count == 87
    assert snapshot.ranks[4].expanded is True
    assert len(visible_r4) == 5
    assert [m.name for m in visible_r4] == [
        "Dark rep",
        "RedLady",
        "sorrybeta",
        "HarleyQueen",
        "KINGLACUNI FARMS",
    ]
    assert [m.online for m in visible_r4] == [True, True, False, False, False]
    assert visible_r4[0].power == 80_600_000
    assert visible_r4[0].level == 30
    assert members_parser.merge_members_by_name(visible_r4)["redlady"].name == "RedLady"


def test_parse_expanded_r3_without_r5_card() -> None:
    parser = members_parser.AllianceMembersParser()
    image = _frame("alliance.members.r3.png")

    ocr = {
        "summary.online": "Online: 22/86",
        "rank_header.0.rank": "R4",
        "rank_header.0.label": "Alliance Rank 4",
        "rank_header.0.count": "6/10",
        "rank_header.1.rank": "R3",
        "rank_header.1.label": "Alliance Rank 3",
        "rank_header.1.count": "14/73",
        "member.0.name": "batazor",
        "member.0.power": "93.2M",
        "member.0.level": "Lv. 30",
        "member.0.status": "Online",
        "member.1.name": "Ben4ik",
        "member.1.power": "79.6M",
        "member.1.level": "Lv. 30",
        "member.1.status": "Online",
        "member.2.name": "tihu",
        "member.2.power": "78.7M",
        "member.2.level": "Lv. 30",
        "member.2.status": "Online",
        "member.3.name": "Zarnyxx",
        "member.3.power": "71.5M",
        "member.3.level": "Lv. 30",
        "member.3.status": "Online",
        "member.4.name": "King",
        "member.4.power": "68.2M",
        "member.4.level": "Lv. 30",
        "member.4.status": "Online",
        "member.5.name": "Бета версия Дума",
        "member.5.power": "58M",
        "member.5.level": "Lv. 30",
        "member.5.status": "Online",
        "member.6.name": "Брат",
        "member.6.power": "54.3M",
        "member.6.level": "Lv. 30",
        "member.6.status": "Online",
        "member.7.name": "冬兵王",
        "member.7.power": "53.5M",
        "member.7.level": "Lv. 26",
        "member.7.status": "Online",
        "member.8.name": "MorganaXRexie",
        "member.8.power": "53.4M",
        "member.8.level": "Lv. 30",
        "member.8.status": "Online",
        "member.9.name": "Olzhas",
        "member.9.power": "49.8M",
        "member.9.level": "Lv. 30",
        "member.9.status": "Online",
    }

    snapshot = parser.parse(image, ocr)
    visible_r3 = [m for m in snapshot.members if m.rank == 3]

    assert snapshot.online_count == 22
    assert snapshot.total_count == 86
    assert 5 not in snapshot.ranks
    assert snapshot.ranks[4].count == 6
    assert snapshot.ranks[4].max_count == 10
    assert snapshot.ranks[3].count == 14
    assert snapshot.ranks[3].max_count == 73
    assert snapshot.ranks[3].expanded is True
    assert len(visible_r3) == 10
    assert [m.name for m in visible_r3] == [
        "batazor",
        "Ben4ik",
        "tihu",
        "Zarnyxx",
        "King",
        "Бета версия Дума",
        "Брат",
        "冬兵王",
        "MorganaXRexie",
        "Olzhas",
    ]
    assert all(m.online for m in visible_r3)
    assert visible_r3[0].power == 93_200_000
    assert visible_r3[7].level == 26


def test_parse_expanded_r3_timeout_statuses_without_headers() -> None:
    parser = members_parser.AllianceMembersParser()
    image = _frame("alliance.members.r3.timeout.png")

    ocr = {
        "summary.online": "Online: 21/87",
        "member.0.name": "ZERO511",
        "member.0.power": "282.4M",
        "member.0.level": "Lv. 30",
        "member.0.status": "4 hour(s) ago",
        "member.1.name": "Ne2pY",
        "member.1.power": "79.9M",
        "member.1.level": "Lv. 30",
        "member.1.status": "7 minute(s) ago",
        "member.2.name": "Bung_karno",
        "member.2.power": "71.3M",
        "member.2.level": "Lv. 30",
        "member.2.status": "27 minute(s) ago",
        "member.3.name": "Inzania",
        "member.3.power": "70.6M",
        "member.3.level": "Lv. 30",
        "member.3.status": "5 hour(s) ago",
        "member.4.name": "King",
        "member.4.power": "68.2M",
        "member.4.level": "Lv. 30",
        "member.4.status": "1 minute(s) ago",
        "member.5.name": "Sorry’aoli",
        "member.5.power": "68.2M",
        "member.5.level": "Lv. 30",
        "member.5.status": "10 hour(s) ago",
        "member.6.name": "伟大的",
        "member.6.power": "66.1M",
        "member.6.level": "Lv. 30",
        "member.6.status": "2 hour(s) ago",
        "member.7.name": "SorryInsit",
        "member.7.power": "65.2M",
        "member.7.level": "Lv. 30",
        "member.7.status": "14 hour(s) ago",
        "member.8.name": "SorrySolas",
        "member.8.power": "62.7M",
        "member.8.level": "Lv. 30",
        "member.8.status": "15 hour(s) ago",
        "member.9.name": "Teh Pucuk",
        "member.9.power": "62.7M",
        "member.9.level": "Lv. 30",
        "member.9.status": "42 minute(s) ago",
        "member.10.name": "OptimusPrime",
        "member.10.power": "59.9M",
        "member.10.level": "Lv. 30",
        "member.10.status": "4 minute(s) ago",
        "member.11.name": "SK 400kg Teo",
        "member.11.power": "58.7M",
        "member.11.level": "Lv. 30",
        "member.11.status": "2 hour(s) ago",
        "member.12.name": "Storm",
        "member.12.power": "58.6M",
        "member.12.level": "Lv. 30",
        "member.12.status": "54 minute(s) ago",
        "member.13.name": "Orion",
        "member.13.power": "56.9M",
        "member.13.level": "Lv. 30",
        "member.13.status": "13 hour(s) ago",
    }

    snapshot = parser.parse(image, ocr, expanded_rank_hint=3)
    visible_r3 = [m for m in snapshot.members if m.rank == 3]

    assert snapshot.online_count == 21
    assert snapshot.total_count == 87
    assert len(visible_r3) == 14
    assert [m.name for m in visible_r3[:4]] == ["ZERO511", "Ne2pY", "Bung_karno", "Inzania"]
    assert not any(m.online for m in visible_r3)
    assert visible_r3[0].last_online_text == "4 hour(s) ago"
    assert visible_r3[0].last_online_seconds == 4 * 60 * 60
    assert visible_r3[1].last_online_seconds == 7 * 60
    assert visible_r3[4].last_online_seconds == 60
    assert visible_r3[9].last_online_seconds == 42 * 60


def test_parse_last_online_tolerates_ocr_noise() -> None:
    parse = members_parser._parse_last_online
    # trailing "ago" garbled by right-edge artifacts → clean canonical + seconds
    assert parse("27 minute(s) aga}") == (False, "27 minute(s) ago", 27 * 60)
    assert parse("5 hour(s) ago| £") == (False, "5 hour(s) ago", 5 * 60 * 60)
    assert parse("1 day(s) ago") == (False, "1 day(s) ago", 24 * 60 * 60)
    assert parse("Online") == (True, "Online", 0)
    # no digit recovered → no seconds (UI shows "—"), never a fabricated number
    assert parse("hour(s) aga")[2] is None
    assert parse("| minute(s) ago")[2] is None
