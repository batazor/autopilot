"""Date-anchored seasonal calendar: load, active-on, upcoming."""
from __future__ import annotations

from games.wos.core.calendar.seasonal import (
    SeasonalEvent,
    Window,
    active_categories,
    events_active_on,
    load_seasonal_events,
    upcoming,
)


def _cat():
    return {
        "halloween": SeasonalEvent("halloween", "Halloween", "festival",
                                   (Window(10, 11, 17),)),
        "tundra_games": SeasonalEvent("tundra_games", "Tundra Games", "activity",
                                      (Window(8, 1, 8),)),
        "new_year": SeasonalEvent("new_year", "New Year", "festival",
                                  (Window(12, 20, 31), Window(1, 1, 10))),
    }


def test_active_on_within_and_outside_window():
    cat = _cat()
    assert {e.id for e in events_active_on(cat, 10, 14)} == {"halloween"}
    assert events_active_on(cat, 10, 20) == []          # after Halloween window


def test_multi_window_event_active_in_both_halves():
    cat = _cat()
    assert cat["new_year"].active_on(12, 25)
    assert cat["new_year"].active_on(1, 5)
    assert not cat["new_year"].active_on(6, 1)


def test_upcoming_finds_soon_events_not_active_ones():
    cat = _cat()
    # On Oct 5, Halloween (starts Oct 11) is within 14 days; Tundra Games is not.
    soon = upcoming(cat, 10, 5, within_days=14)
    assert [e.id for e, _ in soon] == ["halloween"]
    assert soon[0][1] == 6                              # ~6 days until Oct 11


def test_upcoming_excludes_currently_active():
    cat = _cat()
    assert upcoming(cat, 10, 14, within_days=30) == []  # Halloween active now → not "upcoming"


def test_active_categories_flags_activity_events():
    cat = _cat()
    assert active_categories(cat, 8, 4) == {"activity"}   # Tundra Games live
    assert active_categories(cat, 10, 14) == {"festival"}


def test_real_catalog_loads():
    cat = load_seasonal_events()
    assert len(cat) >= 14
    assert "halloween" in cat and "tundra_games" in cat
    assert cat["tundra_games"].category == "activity"
    assert cat["ramadan"].approximate is True            # movable feast flagged
    assert cat["new_year"].active_on(1, 3)               # New Year covers early Jan
