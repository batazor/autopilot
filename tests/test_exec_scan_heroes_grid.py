"""End-to-end tests for ``_exec_scan_heroes_grid``.

Runs the DSL exec handler against the two heroes-screen fixtures with
stubbed ADB capture / OCR and asserts the player state-store snapshot
plus the Redis position-hash both reflect what was parsed.

Covered behaviours:
* full snapshot — every visible hero ends up in ``heroes.entries.<id>``
  with the right unlocked / red-dot / upgrade flags, and Redis carries a
  fresh ``hero → r{ri}c{ci}`` map (parametrized over both fixtures);
* re-sort — when the player re-orders the grid, the Redis position hash
  is fully replaced so a stale ``r1c1`` from an earlier scan can't keep
  routing taps after the hero has moved;
* merge — a pre-existing ``level`` field on a locked entry survives the
  rewrite (so a past ``sync_hero_unit`` result isn't blown away);
* unlock — stale ``shards_*`` fields on a now-unlocked card are dropped.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
import pytest

import tasks.dsl_exec as dsl_exec
from layout.types import Region

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Per-hero parse results for each fixture, mirroring
# ``test_hero_grid_search_fixture``. ``page_heroes.png`` shows bahiti +
# sergey unlocked side-by-side; ``page_heroes_3_unlocked.png`` is the
# same roster after a re-sort that pulled bahiti / molly / sergey to row
# 0 with red-dots on every card.
_FRAME_2_UNLOCKED: dict[str, dict] = {
    "bahiti":      {"cell": (0, 0), "available": True,  "red_dot": True,  "upgrade": True},
    "sergey":      {"cell": (0, 1), "available": True,  "red_dot": True,  "upgrade": True},
    "jeronimo":    {"cell": (0, 2), "available": False, "red_dot": False, "upgrade": False},
    "natalia":     {"cell": (0, 3), "available": False, "red_dot": False, "upgrade": False},
    "zinman":      {"cell": (1, 0), "available": False, "red_dot": False, "upgrade": False},
    "molly":       {"cell": (1, 1), "available": False, "red_dot": False, "upgrade": False},
    "ling_xue":    {"cell": (1, 2), "available": False, "red_dot": False, "upgrade": False},
    "lumak_bokan": {"cell": (1, 3), "available": False, "red_dot": False, "upgrade": False},
    "jasser":      {"cell": (2, 0), "available": False, "red_dot": False, "upgrade": False},
    "seo_yoon":    {"cell": (2, 1), "available": False, "red_dot": False, "upgrade": False},
    "gina":        {"cell": (2, 2), "available": False, "red_dot": False, "upgrade": False},
    "jessie":      {"cell": (2, 3), "available": False, "red_dot": False, "upgrade": False},
}
_FRAME_3_UNLOCKED: dict[str, dict] = {
    "bahiti":      {"cell": (0, 0), "available": True,  "red_dot": True,  "upgrade": False},
    "molly":       {"cell": (0, 1), "available": True,  "red_dot": True,  "upgrade": False},
    "sergey":      {"cell": (0, 2), "available": True,  "red_dot": True,  "upgrade": True},
    "jeronimo":    {"cell": (0, 3), "available": False, "red_dot": False, "upgrade": False},
    "natalia":     {"cell": (1, 0), "available": False, "red_dot": False, "upgrade": False},
    "zinman":      {"cell": (1, 1), "available": False, "red_dot": False, "upgrade": False},
    "ling_xue":    {"cell": (1, 2), "available": False, "red_dot": False, "upgrade": False},
    "lumak_bokan": {"cell": (1, 3), "available": False, "red_dot": False, "upgrade": False},
    "jasser":      {"cell": (2, 0), "available": False, "red_dot": False, "upgrade": False},
    "seo_yoon":    {"cell": (2, 1), "available": False, "red_dot": False, "upgrade": False},
    "gina":        {"cell": (2, 2), "available": False, "red_dot": False, "upgrade": False},
    "jessie":      {"cell": (2, 3), "available": False, "red_dot": False, "upgrade": False},
}
_FRAMES: dict[str, tuple[str, dict[str, dict]]] = {
    "frame_2_unlocked": ("page_heroes.png", _FRAME_2_UNLOCKED),
    "frame_3_unlocked": ("page_heroes_3_unlocked.png", _FRAME_3_UNLOCKED),
}


def _load_frame(label: str) -> np.ndarray:
    fname, _ = _FRAMES[label]
    path = _FIXTURES_DIR / fname
    assert path.is_file(), f"missing fixture: {path}"
    frame = cv2.imread(str(path))
    assert frame is not None, f"cannot decode {path}"
    return frame


class _FakeActions:
    """Stubbed ``BotActions`` that hands back the fixture frame on capture."""

    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.captures = 0

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        self.captures += 1
        return self.frame


class _FakeStore:
    """Captures the flat-dict write so tests can inspect what was persisted.

    ``snapshot()`` returns whatever ``existing`` was seeded with — that's
    how the handler discovers previous fields (``level`` from a past
    ``sync_hero_unit``, stale ``shards_*`` on a now-unlocked card, …).
    """

    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self._entries: dict[str, Any] = dict(existing or {})
        self.captured_player_id: str | None = None
        self.captured_flat: dict[str, Any] | None = None

    def get_or_create(self, player_id: str, nickname: str = "") -> "_FakeStore":
        self.captured_player_id = player_id
        return self

    def snapshot(self) -> Any:
        return SimpleNamespace(
            heroes=SimpleNamespace(entries=dict(self._entries))
        )

    def update_from_flat(self, flat: dict[str, Any]) -> None:
        self.captured_flat = dict(flat)


def _make_ocr_stub(*, level_text: str = "Lv. 5", shard_text: str = "3/10"):
    """Return an ``OcrClient`` stub whose ``ocr_regions`` answers per region id.

    The handler tags locked-cell regions with ``hero_shards_<id>`` and
    unlocked-cell regions with ``hero_level_<id>``. We reuse the same text
    for every hero of a kind — individual values are validated in the
    parsing unit tests; here we only care that the handler maps OCR
    results back onto the right ``heroes.entries.<id>`` field.
    """

    class _StubOcrClient:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def ocr_regions(
            self,
            image: np.ndarray,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            **_kwargs: Any,
        ) -> list[Any]:
            ids = list(region_ids or [])
            self.calls.append(ids)
            out: list[Any] = []
            for rid in ids:
                if rid.startswith("hero_shards_"):
                    text = shard_text
                elif rid.startswith("hero_level_"):
                    text = level_text
                else:
                    text = ""
                out.append(SimpleNamespace(region_id=rid, text=text, confidence=0.95))
            return out

    return _StubOcrClient


@pytest.mark.asyncio
@pytest.mark.parametrize("frame_label", list(_FRAMES))
async def test_scan_heroes_grid_persists_full_snapshot_and_positions(
    monkeypatch: Any,
    redis_async: Any,
    frame_label: str,
) -> None:
    """Happy path on each fixture: 12 heroes land in state.yaml + Redis."""
    expected = _FRAMES[frame_label][1]
    actions = _FakeActions(_load_frame(frame_label))
    store = _FakeStore()
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: store)
    monkeypatch.setattr(dsl_exec, "OcrClient", _make_ocr_stub(level_text="Lv. 7", shard_text="4/10"))

    await dsl_exec.DSL_EXEC_REGISTRY["scan_heroes_grid"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="player_42",
            instance_id="bs1",
        )
    )

    # --- state.yaml side ------------------------------------------------
    assert actions.captures == 1
    assert store.captured_player_id == "player_42"
    flat = store.captured_flat
    assert flat is not None
    keys = set(flat)
    expected_keys = {f"heroes.entries.{hid}" for hid in expected}
    assert keys == expected_keys, f"[{frame_label}] key drift: {keys ^ expected_keys}"

    for hid, exp in expected.items():
        entry = flat[f"heroes.entries.{hid}"]
        assert entry["available"] is exp["available"], (frame_label, hid, entry)
        assert entry["red_dot"] is exp["red_dot"], (frame_label, hid, entry)
        assert entry["isUpgradeAvailable"] is exp["upgrade"], (frame_label, hid, entry)
        assert isinstance(entry["last_seen_at"], float)
        assert isinstance(entry["last_match_score"], float)
        assert isinstance(entry.get("name"), str) and entry["name"]
        if exp["available"]:
            assert entry["level"] == 7
            assert "shards_current" not in entry
            assert "shards_required" not in entry
        else:
            assert entry["shards_current"] == 4
            assert entry["shards_required"] == 10

    # --- Redis side -----------------------------------------------------
    pos_key = "wos:instance:bs1:hero_grid_positions"
    positions = await redis_async.hgetall(pos_key)
    assert set(positions) == set(expected)
    for hid, exp in expected.items():
        ri, ci = exp["cell"]
        assert positions[hid] == f"r{ri}c{ci}", (frame_label, hid, positions[hid])
    assert 0 < await redis_async.ttl(pos_key) <= dsl_exec._HERO_GRID_POSITIONS_TTL_SECONDS


@pytest.mark.asyncio
async def test_scan_heroes_grid_three_unlocked_with_red_dots_persisted(
    monkeypatch: Any,
    redis_async: Any,
) -> None:
    """Frame-specific assertion for ``page_heroes_3_unlocked.png``.

    Three heroes are unlocked simultaneously, each carrying a red-dot
    notification. The handler must persist all three with
    ``available=True`` + ``red_dot=True`` + parsed ``level`` (no stale
    shard counters) — that's the signal downstream ``sync_hero_unit``
    scheduling relies on to decide which hero card to open next.
    """
    actions = _FakeActions(_load_frame("frame_3_unlocked"))
    store = _FakeStore()
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: store)
    monkeypatch.setattr(dsl_exec, "OcrClient", _make_ocr_stub(level_text="Lv. 11"))

    await dsl_exec.DSL_EXEC_REGISTRY["scan_heroes_grid"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="player_42",
            instance_id="bs1",
        )
    )

    flat = store.captured_flat
    unlocked_with_dot = {
        hid for hid, entry in flat.items()
        if entry["available"] is True and entry["red_dot"] is True
    }
    expected = {"heroes.entries.bahiti", "heroes.entries.molly", "heroes.entries.sergey"}
    assert unlocked_with_dot == expected, unlocked_with_dot

    for key in expected:
        entry = flat[key]
        assert entry["level"] == 11
        assert "shards_current" not in entry
        assert "shards_required" not in entry

    # Only sergey shows the green upgrade arrow on this frame.
    assert flat["heroes.entries.sergey"]["isUpgradeAvailable"] is True
    assert flat["heroes.entries.bahiti"]["isUpgradeAvailable"] is False
    assert flat["heroes.entries.molly"]["isUpgradeAvailable"] is False


@pytest.mark.asyncio
async def test_scan_heroes_grid_resort_replaces_position_hash(
    monkeypatch: Any,
    redis_async: Any,
) -> None:
    """Re-sort must fully rewrite ``hero_grid_positions``.

    Seeds Redis with the positions ``frame_2_unlocked`` would produce (molly
    at ``r1c1``, sergey at ``r0c1``, …), then runs a scan against
    ``frame_3_unlocked`` where the same heroes occupy different cells.
    A regression that ``hset``-merges instead of ``delete``+``hset`` would
    leave the stale ``r1c1`` mapping for molly in place and route the next
    ``heroes → page.heroes.molly`` tap to an empty cell.
    """
    pos_key = "wos:instance:bs1:hero_grid_positions"
    stale_mapping = {
        hid: f"r{exp['cell'][0]}c{exp['cell'][1]}"
        for hid, exp in _FRAME_2_UNLOCKED.items()
    }
    await redis_async.hset(pos_key, mapping=stale_mapping)

    actions = _FakeActions(_load_frame("frame_3_unlocked"))
    store = _FakeStore()
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: store)
    monkeypatch.setattr(dsl_exec, "OcrClient", _make_ocr_stub())

    await dsl_exec.DSL_EXEC_REGISTRY["scan_heroes_grid"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="player_42",
            instance_id="bs1",
        )
    )

    fresh = await redis_async.hgetall(pos_key)
    assert set(fresh) == set(_FRAME_3_UNLOCKED)
    for hid, exp in _FRAME_3_UNLOCKED.items():
        ri, ci = exp["cell"]
        assert fresh[hid] == f"r{ri}c{ci}", (hid, fresh[hid])
    # Heroes whose cell actually moved must reflect the new position.
    assert fresh["molly"] == "r0c1" and stale_mapping["molly"] == "r1c1"
    assert fresh["sergey"] == "r0c2" and stale_mapping["sergey"] == "r0c1"


@pytest.mark.asyncio
async def test_scan_heroes_grid_preserves_existing_level_on_locked_card(
    monkeypatch: Any,
    redis_async: Any,
) -> None:
    """A ``level`` field written by a prior ``sync_hero_unit`` survives.

    ``jeronimo`` is locked on the fixture (so the grid scan can't OCR a
    level for him), but a past visit to his profile may have recorded
    one. The handler must read, merge, and write — not blow away — that
    field.
    """
    actions = _FakeActions(_load_frame("frame_2_unlocked"))
    store = _FakeStore(existing={
        "jeronimo": {"name": "Jeronimo", "level": 9, "stars": 6},
    })
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: store)
    monkeypatch.setattr(dsl_exec, "OcrClient", _make_ocr_stub())

    await dsl_exec.DSL_EXEC_REGISTRY["scan_heroes_grid"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="player_42",
            instance_id="bs1",
        )
    )

    entry = store.captured_flat["heroes.entries.jeronimo"]
    assert entry["level"] == 9
    assert entry["stars"] == 6
    assert entry["available"] is False
    # Shard fields freshly populated by this scan.
    assert entry["shards_current"] == 3
    assert entry["shards_required"] == 10


@pytest.mark.asyncio
async def test_scan_heroes_grid_clears_stale_shard_counts_when_unlocked(
    monkeypatch: Any,
    redis_async: Any,
) -> None:
    """Stale ``shards_*`` from a past locked-state snapshot must be cleared
    once the card flips to unlocked, so callers don't see "needs 9/10"
    next to a playable hero."""
    actions = _FakeActions(_load_frame("frame_2_unlocked"))
    store = _FakeStore(existing={
        "bahiti": {"shards_current": 9, "shards_required": 10, "stars": 4},
    })
    monkeypatch.setattr(dsl_exec, "BotActions", lambda: actions)
    monkeypatch.setattr(dsl_exec, "get_state_store", lambda: store)
    monkeypatch.setattr(dsl_exec, "OcrClient", _make_ocr_stub(level_text="Lv. 12"))

    await dsl_exec.DSL_EXEC_REGISTRY["scan_heroes_grid"](
        dsl_exec.DslExecContext(
            redis_client=redis_async,
            player_id="player_42",
            instance_id="bs1",
        )
    )

    entry = store.captured_flat["heroes.entries.bahiti"]
    assert entry["available"] is True
    assert entry["level"] == 12
    assert "shards_current" not in entry
    assert "shards_required" not in entry
    # Unrelated pre-existing fields are not touched by the merge.
    assert entry["stars"] == 4
