"""Quest reader: OCR daily-mission buffer → structured DailyTasks, over the REAL
``daily_missions.yaml`` registry (so its categories are validated too)."""
from __future__ import annotations

from games.wos.core.chapter.daily_tasks import parse_daily_tasks

# The scrolled daily list as ``chapter.claim_missions`` accumulates it, with the
# leading bullets + the (done / target) the game renders. Intel is complete (5/5)
# and Upgrade is open (0/1) so both the claimable and the open-boost paths show.
_BUFFER = """\
+ Make 5 Alliance Contribution(s) (0 / 5)
+ Train 10 Infantry (0 / 10)
+ Train 10 Lancers (3 / 10)
+ Train 10 Marksmen (0 / 10)
+ Carry out 5 Intel Mission(s) (5 / 5)
+ Heal 10 injured soldiers (0 / 10)
+ Gather 50,000 Meat (0 / 50000)
+ Gather 50,000 Wood (0 / 50000)
+ Gather 10,000 Coal (0 / 10000)
+ Gather 3,000 Iron (0 / 3000)
+ Upgrade 1 building(s) (0 / 1)
+ Research 1 technology(ies) (0 / 1)
+ Gather 1 time(s) (0 / 1)
+ Fight in 1 Arena Challenge(s) (0 / 1)
+ Complete 1 challenges in The Labyrinth. (0 / 1)
"""


def _by_id(tasks):
    return {t.id: t for t in tasks}


def test_parses_every_known_mission_with_disambiguated_ids():
    ids = set(_by_id(parse_daily_tasks(_BUFFER)))
    assert {"train:infantry", "train:lancer", "train:marksman"} <= ids
    assert {"gather:meat", "gather:wood", "gather:coal", "gather:iron", "gather"} <= ids
    assert {"build", "research", "stamina", "help", "heal", "arena", "event"} <= ids


def test_extracts_rendered_progress_and_target():
    ids = _by_id(parse_daily_tasks(_BUFFER))
    assert (ids["train:lancer"].progress, ids["train:lancer"].target) == (3, 10)
    assert (ids["gather:meat"].progress, ids["gather:meat"].target) == (0, 50000)  # commas stripped
    assert (ids["help"].progress, ids["help"].target) == (0, 5)


def test_claimable_is_done_at_read():
    ids = _by_id(parse_daily_tasks(_BUFFER))
    assert ids["stamina"].done is True and ids["stamina"].claimable is True   # 5/5
    assert ids["train:infantry"].claimable is False                          # 0/10
    assert ids["build"].claimable is False                                   # 0/1


def test_categories_match_the_registry():
    ids = _by_id(parse_daily_tasks(_BUFFER))
    assert ids["build"].category == "build"
    assert ids["research"].category == "research"
    assert ids["gather:meat"].category == "gather"
    assert ids["stamina"].category == "stamina"        # intel spends stamina
    assert ids["help"].category == "help"              # alliance contribution


def test_parsed_tasks_drive_the_coordinator_daily_bias():
    """The payoff: the producer feeds the previously-unfed daily_bias."""
    from games.wos.core.coordinator import daily_bias

    bias = daily_bias(parse_daily_tasks(_BUFFER))
    assert bias.domain_boost.get("building_progression", 1.0) > 1.0   # Upgrade open
    assert bias.domain_boost.get("research", 1.0) > 1.0               # Research open
    assert bias.domain_boost.get("gather", 1.0) > 1.0                 # gathers open
    assert "stamina" in bias.claims                                  # the done Intel mission


def test_ignores_ocr_noise_lines():
    noisy = "O®\n+ Train 10 Infantry (0 / 10)\n~~ garbage ~~\n"
    assert [t.id for t in parse_daily_tasks(noisy)] == ["train:infantry"]


def test_empty_buffer_is_no_tasks():
    assert parse_daily_tasks("") == []
    assert parse_daily_tasks("   \n  \n") == []
