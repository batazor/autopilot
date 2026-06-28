"""Structural checks for the main_menu navigation node."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

from navigation import screen_graph
from services import bind_active_game
from tasks.dsl_exec.context import DslExecContext

MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str) -> dict:
    path = MODULE_DIR / rel
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_module_manifest_declares_main_menu() -> None:
    manifest = _load_yaml("module.yaml")
    assert manifest["id"] == "main_menu"
    assert manifest["title"] == "Main menu"


def test_edge_taps_enter_and_leave_main_menu() -> None:
    edges = _load_yaml("routes/edge_taps.yaml")["edges"]
    assert edges["main_city"]["main_menu"] == ["main_city.to.main_menu"]
    assert edges["main_menu"]["main_city"] == ["icon.page.back"]
    assert edges["main_menu"]["infantry"] == ["main_menu.to.infantry"]
    assert edges["main_menu"]["lancer"] == ["main_menu.to.lancer"]
    assert edges["main_menu"]["marksman"] == ["main_menu.to.marksman"]


def test_area_declares_training_timer_and_transition_regions() -> None:
    area = _load_yaml("area.yaml")
    main = area["screens"][0]
    assert main["screen_id"] == "main_menu"
    regions = {r["name"]: r for r in main["regions"]}
    for troop_type in ("infantry", "lancer", "marksman"):
        timer = regions[f"main_menu.training.{troop_type}.time"]
        assert timer["action"] == "text"
        assert timer["type"] == "time"
        assert timer["threshold"] <= 0.75
        status = regions[f"main_menu.training.{troop_type}.status"]
        assert status["action"] == "text"
        assert status["type"] == "string"
        assert regions[f"main_menu.to.{troop_type}"]["action"] == "click"


def test_area_declares_wilderness_march_regions() -> None:
    area = _load_yaml("area.yaml")
    wilderness = area["screens"][1]
    assert wilderness["screen_id"] == "main_menu"
    assert wilderness["ocr"] == "references/wilderness.png"
    regions = {r["name"]: r for r in wilderness["regions"]}
    assert regions["main_menu.marching.count"]["action"] == "text"
    assert regions["main_menu.marching.count"]["type"] == "string"
    for slot_no in (1, 2):
        title = regions[f"main_menu.marching.slot.{slot_no}.title"]
        timer = regions[f"main_menu.marching.slot.{slot_no}.time"]
        assert title["action"] == "text"
        assert title["type"] == "string"
        assert timer["action"] == "text"
        assert timer["type"] == "time"
    for slot_no in range(3, 7):
        status = regions[f"main_menu.marching.slot.{slot_no}.status"]
        assert status["action"] == "text"
        assert status["type"] == "string"


def test_area_declares_building_queue_reference_as_main_menu() -> None:
    area = _load_yaml("area.yaml")
    building = next(
        screen
        for screen in area["screens"]
        if screen.get("ocr") == "references/building.png"
    )
    assert building["screen_id"] == "main_menu"


def test_sync_training_status_scenario_reads_known_regions() -> None:
    scenario = _load_yaml("scenarios/sync_training_status.yaml")
    assert scenario["node"] == "main_menu"
    used = [step["ocr"] for step in scenario["steps"] if "ocr" in step]
    assert used == [
        "main_menu.training.infantry.time",
        "main_menu.training.infantry.status",
        "main_menu.training.lancer.time",
        "main_menu.training.lancer.status",
        "main_menu.training.marksman.time",
        "main_menu.training.marksman.status",
    ]
    assert scenario["steps"][-1] == {"exec": "sync_main_menu_training_status"}


def test_sync_marching_status_scenario_reads_known_regions() -> None:
    scenario = _load_yaml("scenarios/sync_marching_status.yaml")
    assert scenario["node"] == "main_menu"
    used = [step["ocr"] for step in scenario["steps"] if "ocr" in step]
    assert used == [
        "main_menu.marching.count",
        "main_menu.marching.slot.1.title",
        "main_menu.marching.slot.1.time",
        "main_menu.marching.slot.2.title",
        "main_menu.marching.slot.2.time",
        "main_menu.marching.slot.3.status",
        "main_menu.marching.slot.4.status",
        "main_menu.marching.slot.5.status",
        "main_menu.marching.slot.6.status",
    ]
    assert scenario["steps"][-1] == {"exec": "sync_main_menu_marching_status"}


def test_analyze_pushes_training_sync_when_menu_visible() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    rule = rules["main_menu.training.visible"]
    assert rule["name"] == "main_menu.training.visible"
    assert rule["region"] == "main_menu.training.infantry.status"
    assert rule["action"] == "text"
    assert rule["screens"] == ["main_menu"]
    assert rule["ttl"] == "5m"
    assert rule["steps"] == [{"push_scenario": "sync_training_status"}]


def test_analyze_pushes_marching_sync_when_wilderness_visible() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    rule = rules["main_menu.marching.visible"]
    assert rule["region"] == "main_menu.marching.count"
    assert rule["action"] == "text"
    assert rule["screens"] == ["main_menu"]
    assert rule["ttl"] == "1m"
    assert rule["steps"] == [{"push_scenario": "sync_marching_status"}]


def test_screen_graph_exposes_main_menu_node() -> None:
    bind_active_game("wos")
    screen_graph.invalidate_edge_taps_cache()
    screen_graph.invalidate_screen_verify_config()

    static, _dynamic, _graph = screen_graph.graph_for_game("wos")
    assert static[("main_city", "main_menu")] == ["main_city.to.main_menu"]
    assert static[("main_menu", "main_city")] == ["icon.page.back"]
    assert static[("main_menu", "infantry")] == ["main_menu.to.infantry"]
    assert static[("main_menu", "lancer")] == ["main_menu.to.lancer"]
    assert static[("main_menu", "marksman")] == ["main_menu.to.marksman"]
    assert screen_graph.route_taps("main_city", "main_menu") == [
        ["main_city.to.main_menu"]
    ]
    assert screen_graph.route_taps("main_menu", "infantry") == [
        ["main_menu.to.infantry"]
    ]
    assert screen_graph.screen_verify_rules("main_menu") == [
        {"from_screen": ["main_city", "main_world"]}
    ]


def test_research_center_is_a_dynamic_menu_teleport_not_chapter_task() -> None:
    """The City menu's Go button teleports onto the Research Center building POPUP
    (``main_menu -> research_center.building``, a dynamic ``main_menu_panel_row``
    resolver); the popup's Research button then opens the tech tree
    (``research_center.building -> research_center``). The generic
    ``main_city -> research_center [chapter.task]`` 1-hop must NOT be synthesized —
    it would out-compete the reliable menu teleport in BFS. Guards the
    dynamic-aware, ``.building``-aware skip in ``_load_edge_taps``."""
    bind_active_game("wos")
    screen_graph.invalidate_edge_taps_cache()
    static, dynamic, _graph = screen_graph.graph_for_game("wos")
    spec = dynamic.get(("main_menu", "research_center.building"))
    assert spec is not None and spec.get("resolver") == "main_menu_panel_row"
    assert spec.get("section") == "tech_research" and spec.get("row") == "center"
    # The popup opens the tech tree via its Research button.
    assert static.get(("research_center.building", "research_center")) == [
        "research_center.building.research"
    ]
    # The chapter.task fallback must be suppressed for the menu-backed building.
    assert ("main_city", "research_center") not in static


def _load_exec_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "main_menu_exec_test", MODULE_DIR / "exec.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_exec_sync_main_menu_training_status_updates_player_state(
    redis_async: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_exec_module()
    updates: dict[str, object] = {}

    class _PlayerStore:
        def update_from_flat(self, flat: dict[str, object]) -> None:
            updates.update(flat)

    class _StateStore:
        def get_or_create(self, player_id: str) -> _PlayerStore:
            assert player_id == "p1"
            return _PlayerStore()

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mod, "get_state_store", lambda: _StateStore())
    monkeypatch.setattr(mod, "publish_dashboard_event_throttled_async", _publish)
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:p1:state",
        mapping={
            "main_menu.training.infantry.remaining_s": "44535",
            "main_menu.training.infantry.remaining_s_text": "12:22:15",
            "main_menu.training.infantry.status": "Infantry ] 12:22:15",
            "main_menu.training.lancer.remaining_s": "44547",
            "main_menu.training.lancer.remaining_s_text": "12:22:27",
            "main_menu.training.lancer.status": "Lancer ] 12:22:27",
            "main_menu.training.marksman.status": "Marksman",
        },
    )

    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="p1",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["sync_main_menu_training_status"](ctx)

    assert ctx.result["action"] == "stored"
    assert updates["troops.infantry.state.isAvailable"] is False
    assert updates["troops.infantry.state.TextStatus"] == "12:22:15"
    assert updates["troops.infantry.state.training_remaining_s"] == 44535
    assert updates["troops.lancer.state.training_remaining_s"] == 44547
    assert updates["troops.marksman.state.isAvailable"] is True
    assert updates["troops.marksman.state.training_remaining_s"] == 0
    assert updates["troops.marksman.state.training_ends_at"] == 0.0
    assert updates["troops.infantry.state.training_ends_at"] > updates[
        "troops.infantry.state.training_checked_at"
    ]


@pytest.mark.asyncio
async def test_exec_sync_main_menu_marching_status_updates_player_state(
    redis_async: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_exec_module()
    updates: dict[str, object] = {}

    class _PlayerStore:
        def update_from_flat(self, flat: dict[str, object]) -> None:
            updates.update(flat)

    class _StateStore:
        def get_or_create(self, player_id: str) -> _PlayerStore:
            assert player_id == "p1"
            return _PlayerStore()

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mod, "get_state_store", lambda: _StateStore())
    monkeypatch.setattr(mod, "publish_dashboard_event_throttled_async", _publish)
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:p1:state",
        mapping={
            "main_menu.marching.count": "2/6",
            "main_menu.marching.count_text": "2/6",
            "main_menu.marching.slot.1.title": "Attack: Berserk Cryptid",
            "main_menu.marching.slot.1.remaining_s": "67",
            "main_menu.marching.slot.1.remaining_s_text": "00:01:07",
            "main_menu.marching.slot.2.title": "Joined Rally: [REP]634rs",
            "main_menu.marching.slot.2.remaining_s": "31",
            "main_menu.marching.slot.2.remaining_s_text": "00:00:31",
            "main_menu.marching.slot.3.status": "March Queue 3\nIdle",
            "main_menu.marching.slot.4.status": "March Queue 4\nIdle",
            "main_menu.marching.slot.5.status": "March Queue 5\nIdle",
            "main_menu.marching.slot.6.status": "March Queue 6\nIdle",
        },
    )

    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="p1",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["sync_main_menu_marching_status"](ctx)

    assert ctx.result["action"] == "stored"
    assert updates["marches.active_count"] == 2
    assert updates["marches.capacity"] == 6
    slots = updates["marches.slots"]
    assert isinstance(slots, dict)
    slot1 = slots["1"]
    slot2 = slots["2"]
    slot3 = slots["3"]
    assert slot1["status"] == "marching"
    assert slot1["label"] == "Attack: Berserk Cryptid"
    assert slot1["remaining_s"] == 67
    assert slot1["time_text"] == "00:01:07"
    assert slot2["label"] == "Joined Rally: [REP]634rs"
    assert slot2["remaining_s"] == 31
    assert slot3["status"] == "idle"
    assert slot3["remaining_s"] == 0
    assert slot1["ends_at"] > slot1["checked_at"]


def test_area_declares_research_regions() -> None:
    area = _load_yaml("area.yaml")
    main = area["screens"][0]
    regions = {r["name"]: r for r in main["regions"]}
    status = regions["main_menu.research.slot.status"]
    assert status["action"] == "text"
    assert status["type"] == "string"
    timer = regions["main_menu.research.slot.time"]
    assert timer["action"] == "text"
    assert timer["type"] == "time"
    # White-on-progress-bar glyphs need the dedicated preprocess: the default
    # pipeline misreads the day prefix ("4d" → "Ad") and silently loses 4 days.
    assert timer["preprocess"] == "bar_timer"
    assert timer["threshold"] <= 0.75
    assert regions["main_menu.to.research"]["action"] == "click"


def test_sync_research_status_scenario_reads_known_regions() -> None:
    scenario = _load_yaml("scenarios/sync_research_status.yaml")
    assert scenario["node"] == "main_menu"
    used = [step["ocr"] for step in scenario["steps"] if "ocr" in step]
    assert used == [
        "main_menu.research.slot.status",
        "main_menu.research.slot.time",
    ]
    assert scenario["steps"][-1] == {"exec": "sync_main_menu_research_status"}


def test_analyze_pushes_research_sync_when_menu_visible() -> None:
    analyze = _load_yaml("analyze/analyze.yaml")
    rules = {rule["name"]: rule for rule in analyze["overlay"]}
    rule = rules["main_menu.research.visible"]
    assert rule["region"] == "main_menu.research.slot.status"
    assert rule["action"] == "text"
    assert rule["screens"] == ["main_menu"]
    assert rule["ttl"] == "5m"
    assert rule["steps"] == [{"push_scenario": "sync_research_status"}]


@pytest.mark.asyncio
async def test_exec_sync_main_menu_research_status_updates_player_state(
    redis_async: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_exec_module()
    updates: dict[str, object] = {}

    class _PlayerStore:
        def update_from_flat(self, flat: dict[str, object]) -> None:
            updates.update(flat)

    class _StateStore:
        def get_or_create(self, player_id: str) -> _PlayerStore:
            assert player_id == "p1"
            return _PlayerStore()

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mod, "get_state_store", lambda: _StateStore())
    monkeypatch.setattr(mod, "publish_dashboard_event_throttled_async", _publish)
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:p1:state",
        mapping={
            "main_menu.research.slot.remaining_s": "388783",
            "main_menu.research.slot.remaining_s_text": "4d 11:59:43",
            "main_menu.research.slot.status": "Tool Enhancement VII",
        },
    )

    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="p1",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["sync_main_menu_research_status"](ctx)

    assert ctx.result["action"] == "stored"
    assert updates["research.center.state.isAvailable"] is False
    assert updates["research.center.state.current"] == "Tool Enhancement VII"
    assert updates["research.center.state.TextStatus"] == "4d 11:59:43"
    assert updates["research.center.state.research_remaining_s"] == 388783
    assert updates["research.center.state.research_ends_at"] > updates[
        "research.center.state.research_checked_at"
    ]


@pytest.mark.asyncio
async def test_exec_sync_main_menu_research_status_idle_slot(
    redis_async: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_exec_module()
    updates: dict[str, object] = {}

    class _PlayerStore:
        def update_from_flat(self, flat: dict[str, object]) -> None:
            updates.update(flat)

    class _StateStore:
        def get_or_create(self, player_id: str) -> _PlayerStore:
            return _PlayerStore()

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mod, "get_state_store", lambda: _StateStore())
    monkeypatch.setattr(mod, "publish_dashboard_event_throttled_async", _publish)
    # No timer OCR'd — only a residual row label: slot counts as available.
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:player:p1:state",
        mapping={"main_menu.research.slot.status": "Tech Research"},
    )

    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="p1",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["sync_main_menu_research_status"](ctx)

    assert ctx.result["action"] == "stored"
    assert updates["research.center.state.isAvailable"] is True
    assert updates["research.center.state.current"] == ""
    assert updates["research.center.state.research_remaining_s"] == 0
    assert updates["research.center.state.research_ends_at"] == 0.0


@pytest.mark.asyncio
async def test_research_regions_ocr_from_reference() -> None:
    """The labeled bboxes must read the research row off the real screenshot —
    including the day prefix, which only survives the ``bar_timer`` preprocess."""
    import cv2

    from layout.types import Region
    from services import get_ocr_client
    from tasks.dsl_scenario_helpers import _parse_hms_to_seconds

    frame = cv2.imread(str(MODULE_DIR / "references" / "training.png"))
    assert frame is not None
    h, w = frame.shape[:2]
    area = _load_yaml("area.yaml")
    regions = {r["name"]: r for r in area["screens"][0]["regions"]}

    def _region(name: str) -> Region:
        b = regions[name]["bbox"]
        return Region(
            int(b["x"] / 100 * w),
            int(b["y"] / 100 * h),
            int(b["width"] / 100 * w),
            int(b["height"] / 100 * h),
        )

    ocr = get_ocr_client()
    status = await ocr.ocr_region(
        frame, _region("main_menu.research.slot.status")
    )
    assert "Tool Enhancement" in status.text

    timer = await ocr.ocr_region(
        frame, _region("main_menu.research.slot.time"), preprocess="bar_timer"
    )
    assert _parse_hms_to_seconds(timer.text) == 4 * 86400 + 11 * 3600 + 59 * 60 + 43


# --- City-panel scanner ------------------------------------------------------

_SCAN_EXPECTATIONS = {
    "building.png": [
        ("building_queue", "queue_1", "idle"),
        ("building_queue", "queue_2", "idle"),
        ("training", "infantry", "completed"),
        ("training", "lancer", "completed"),
        ("training", "marksman", "completed"),
        ("tech_research", "center", "idle"),
    ],
    "research.png": [
        ("tech_research", "center", "idle"),
        ("tech_research", "war_academy", "locked"),
        ("expert", "learn_skills", "idle"),
        ("alliance_contribution", "alliance_contribution", "claimable"),
        ("recruit_heroes", "advanced", "free"),
    ],
    "my_rewards.png": [
        ("my_rewards", "online_rewards", "completed"),
        ("pet_adventure", "pet_adventure", "completed"),
        ("life_essence", "tree_of_life", "claimable"),
        ("labyrinth", "gear_forge", "claimable"),
        ("trek", "tundra_trek", "claimable"),
    ],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", sorted(_SCAN_EXPECTATIONS))
async def test_scan_panel_rows_classifies_reference(reference: str) -> None:
    import cv2

    from services import get_ocr_client

    mod = _load_exec_module()
    frame = cv2.imread(str(MODULE_DIR / "references" / reference))
    assert frame is not None
    rows = await mod._scan_panel_rows(frame, ocr=get_ocr_client())
    got = [(r["section"], r["row"], r["kind"]) for r in rows]
    assert got == _SCAN_EXPECTATIONS[reference]


@pytest.mark.asyncio
async def test_scan_panel_rows_training_reads_day_prefixed_research_timer() -> None:
    """training.png: in-progress research maps to the center slot and the
    ``bar_timer`` retry must keep the "4d" day prefix (388783s, not 43183s)."""
    import cv2

    from services import get_ocr_client

    mod = _load_exec_module()
    frame = cv2.imread(str(MODULE_DIR / "references" / "training.png"))
    rows = await mod._scan_panel_rows(frame, ocr=get_ocr_client())
    research = next(r for r in rows if r["section"] == "tech_research")
    assert research["row"] == "center"
    assert research["kind"] == "in_progress"
    assert research["remaining_s"] == 4 * 86400 + 11 * 3600 + 59 * 60 + 43
    troops = [(r["row"], r["kind"]) for r in rows if r["section"] == "training"]
    assert troops == [
        ("infantry", "in_progress"),
        ("lancer", "in_progress"),
        ("marksman", "in_progress"),
    ]


def test_panel_state_updates_canonical_paths() -> None:
    mod = _load_exec_module()
    now = 1_000.0
    rows = [
        {
            "section": "building_queue",
            "row": "queue_2",
            "title": "Building Queue 2",
            "status_text": "01:00:00",
            "kind": "in_progress",
            "remaining_s": 3600,
            "button": "blue",
            "red_dot": False,
            "cy": 400,
        },
        {
            "section": "training",
            "row": "infantry",
            "title": "Infantry",
            "status_text": "Completed",
            "kind": "completed",
            "remaining_s": 0,
            "button": "green",
            "red_dot": True,
            "cy": 470,
        },
        {
            "section": "tech_research",
            "row": "war_academy",
            "title": "War Academy Research",
            "status_text": "Not yet built",
            "kind": "locked",
            "remaining_s": 0,
            "button": "",
            "red_dot": False,
            "cy": 540,
        },
    ]
    updates = mod._panel_state_updates(rows, now)
    assert updates["buildings.queue.2.state.remaining_s"] == 3600
    assert updates["buildings.queue.2.state.isIdle"] is False
    assert updates["buildings.queue.2.state.ends_at"] == now + 3600
    assert updates["troops.infantry.state.isReady"] is True
    assert updates["troops.infantry.state.isAvailable"] is True
    assert updates["research.war_academy.state.isLocked"] is True
    assert updates["research.war_academy.state.isAvailable"] is False
    assert updates["main_menu.panel.training.infantry.isClaimable"] is True
    assert updates["main_menu.panel.training.infantry.has_red_dot"] is True


@pytest.mark.asyncio
async def test_exec_scan_panel_pushes_accept_for_completed_troops(
    redis_async: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """building.png shows all three troops Completed → three accept pushes."""
    import json

    import cv2

    mod = _load_exec_module()
    frame = cv2.imread(str(MODULE_DIR / "references" / "building.png"))

    class _FakeActions:
        def capture_screen_bgr(self, _instance_id: str):
            return frame

    updates: dict[str, object] = {}

    class _PlayerStore:
        def update_from_flat(self, flat: dict[str, object]) -> None:
            updates.update(flat)

    class _StateStore:
        def get_or_create(self, player_id: str) -> _PlayerStore:
            return _PlayerStore()

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    # building.png shows idle build queues, completed troops AND an idle Research
    # Center row. Every panel push self-gates on the target scenario's `enabled`
    # flag; pin it True for determinism so the full per-row dispatch set fires.
    import dsl.dsl_schema as schema

    monkeypatch.setattr(mod.dsl_runtime, "bot_actions", lambda: _FakeActions())
    monkeypatch.setattr(schema, "dsl_scenario_yaml_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(mod, "get_state_store", lambda: _StateStore())
    monkeypatch.setattr(mod, "publish_dashboard_event_throttled_async", _publish)

    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="p1",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["scan_main_menu_panel"](ctx)

    assert ctx.result["action"] == "stored"
    assert sorted(ctx.result["pushed"]) == [
        "accept_troops_infantry",
        "accept_troops_lancer",
        "accept_troops_marksman",
        "building_queue_1_empty",
        "building_queue_2_empty",
        "start_idle_research",
    ]
    assert updates["troops.infantry.state.isReady"] is True
    assert updates["buildings.queue.1.state.isIdle"] is True
    assert updates["buildings.queue.2.state.isIdle"] is True

    queued = [
        json.loads(raw)["task_type"]
        for raw in await redis_async.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    ]
    assert sorted(queued) == [
        "accept_troops_infantry",
        "accept_troops_lancer",
        "accept_troops_marksman",
        "building_queue_1_empty",
        "building_queue_2_empty",
        "start_idle_research",
    ]


@pytest.mark.asyncio
async def test_exec_scan_panel_pushes_train_for_idle_troops(
    redis_async: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three camps Idle → three troops.<type>.train pushes (no 30m cron wait)."""
    import json

    mod = _load_exec_module()

    rows = [
        {
            "section": "training",
            "row": troop,
            "title": troop.capitalize(),
            "status_text": "Idle",
            "kind": "idle",
            "remaining_s": 0,
            "button": "blue",
            "red_dot": False,
            "cy": 400 + idx * 60,
        }
        for idx, troop in enumerate(("infantry", "lancer", "marksman"))
    ]

    async def _fake_scan(_image: object, *, ocr: object) -> list[dict[str, object]]:
        return rows

    class _FakeActions:
        def capture_screen_bgr(self, _instance_id: str) -> object:
            return object()

    class _PlayerStore:
        def update_from_flat(self, _flat: dict[str, object]) -> None:
            return None

    class _StateStore:
        def get_or_create(self, _player_id: str) -> _PlayerStore:
            return _PlayerStore()

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    import dsl.dsl_schema as schema

    monkeypatch.setattr(mod.dsl_runtime, "bot_actions", lambda: _FakeActions())
    monkeypatch.setattr(mod, "_scan_panel_rows", _fake_scan)
    monkeypatch.setattr(schema, "dsl_scenario_yaml_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(mod, "get_state_store", lambda: _StateStore())
    monkeypatch.setattr(mod, "publish_dashboard_event_throttled_async", _publish)

    ctx = DslExecContext(
        redis_client=redis_async,
        player_id="p1",
        instance_id="bs1",
        args={},
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["scan_main_menu_panel"](ctx)

    assert ctx.result["action"] == "stored"
    assert sorted(ctx.result["pushed"]) == [
        "troops.infantry.train",
        "troops.lancer.train",
        "troops.marksman.train",
    ]
    queued = [
        json.loads(raw)["task_type"]
        for raw in await redis_async.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    ]
    assert sorted(queued) == [
        "troops.infantry.train",
        "troops.lancer.train",
        "troops.marksman.train",
    ]


_DISPATCH_CASES = [
    ("training", "completed", "infantry", "accept_troops_infantry"),
    ("training", "idle", "lancer", "troops.lancer.train"),
    ("building_queue", "idle", "queue_1", "building_queue_1_empty"),
    ("building_queue", "idle", "queue_2", "building_queue_2_empty"),
    ("tech_research", "idle", "center", "start_idle_research"),
    ("tech_research", "idle", "war_academy", "start_idle_war_academy"),
    ("alliance_contribution", "claimable", "alliance_contribution", "alliance.tech.contribute"),
    ("recruit_heroes", "free", "advanced", "free_recruitments_today"),
    ("pet_adventure", "completed", "pet_adventure", "journey_of_light"),
    ("labyrinth", "claimable", "gear_forge", "event.labyrinth.gear_forge"),
    ("labyrinth", "claimable", "gaia_heart", "event.labyrinth.gaia_heart"),
    ("trek", "claimable", "tundra_trek", "event.tundra_trek"),
    ("my_rewards", "claimable", "online_rewards", "claim_online_rewards"),
    ("life_essence", "claimable", "tree_of_life", "claim_life_essence"),
    ("expert", "idle", "learn_skills", "learn_skills"),
    ("childrens_day", "claimable", "childrens_day", "event.childrens_day"),
    ("popularity_king", "claimable", "popularity_king_competition", "event.popularity_king_competition"),
    ("rose_defense", "claimable", "rose_defense_battle", "event.rose_defense_battle"),
    ("honey_language_mall", "claimable", "honey_language_mall", "event.honey_language_mall"),
    ("honeymoon_trip", "claimable", "honeymoon_trip", "event.honeymoon_trip"),
]


@pytest.mark.parametrize(("section", "kind", "row", "scenario"), _DISPATCH_CASES)
def test_dispatch_table_resolves_every_actionable_row(
    section: str, kind: str, row: str, scenario: str
) -> None:
    """Every panel row's actionable (section, kind) resolves to its OWN scenario."""
    mod = _load_exec_module()
    rule = mod._dispatch_rule_for(section, kind, row)
    assert rule is not None, f"no dispatch rule for {section}/{kind}/{row}"
    assert mod._resolve_dispatch_scenario(rule, row) == scenario


@pytest.mark.parametrize(
    ("section", "kind", "row"),
    [
        ("training", "in_progress", "infantry"),   # mid-training → no push
        ("tech_research", "locked", "war_academy"),  # not built → no push
        ("building_queue", "in_progress", "queue_1"),  # building → no push
        ("alliance_contribution", "in_progress", "alliance_contribution"),
    ],
)
def test_dispatch_table_ignores_non_actionable_kinds(section: str, kind: str, row: str) -> None:
    """Rows that aren't pending action (in_progress/locked) match no dispatch rule."""
    mod = _load_exec_module()
    assert mod._dispatch_rule_for(section, kind, row) is None


@pytest.mark.asyncio
async def test_exec_scan_panel_research_idle_push_gated_on_dispatch_enabled(
    redis_async: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle Research Center row → start_idle_research, but only when that
    dispatcher is enabled (it ships disabled until the tech-tree is labeled)."""
    import dsl.dsl_schema as schema

    mod = _load_exec_module()

    rows = [
        {
            "section": "tech_research",
            "row": "center",
            "title": "Center Research",
            "status_text": "Idle",
            "kind": "idle",
            "remaining_s": 0,
            "button": "blue",
            "red_dot": False,
            "cy": 600,
        }
    ]

    async def _fake_scan(_image: object, *, ocr: object) -> list[dict[str, object]]:
        return rows

    class _FakeActions:
        def capture_screen_bgr(self, _instance_id: str) -> object:
            return object()

    class _PlayerStore:
        def update_from_flat(self, _flat: dict[str, object]) -> None:
            return None

    class _StateStore:
        def get_or_create(self, _player_id: str) -> _PlayerStore:
            return _PlayerStore()

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mod.dsl_runtime, "bot_actions", lambda: _FakeActions())
    monkeypatch.setattr(mod, "_scan_panel_rows", _fake_scan)
    monkeypatch.setattr(mod, "get_state_store", lambda: _StateStore())
    monkeypatch.setattr(mod, "publish_dashboard_event_throttled_async", _publish)

    # 1. dispatcher disabled (default) → no research push
    monkeypatch.setattr(schema, "dsl_scenario_yaml_enabled", lambda *_a, **_k: False)
    ctx = DslExecContext(
        redis_client=redis_async, player_id="p1", instance_id="bs1", args={}, result={}
    )
    await mod.DSL_EXEC_HANDLERS["scan_main_menu_panel"](ctx)
    assert ctx.result["pushed"] == []

    # 2. dispatcher enabled → push start_idle_research
    monkeypatch.setattr(schema, "dsl_scenario_yaml_enabled", lambda *_a, **_k: True)
    ctx2 = DslExecContext(
        redis_client=redis_async, player_id="p1", instance_id="bs1", args={}, result={}
    )
    await mod.DSL_EXEC_HANDLERS["scan_main_menu_panel"](ctx2)
    assert ctx2.result["pushed"] == ["start_idle_research"]


@pytest.mark.asyncio
async def test_exec_tap_main_menu_panel_row_taps_matching_row_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import numpy as np

    mod = _load_exec_module()
    taps: list[tuple[str, object, str]] = []

    class _FakeActions:
        def __init__(self) -> None:
            self.swipes: list[str] = []

        def swipe_direction(
            self,
            _instance_id: str,
            *,
            direction: str,
            delta: int,
            duration_ms: int,
        ) -> bool:
            self.swipes.append(f"{direction}:{delta}:{duration_ms}")
            return True

        def capture_screen_bgr(self, _instance_id: str):
            return np.zeros((1280, 720, 3), dtype=np.uint8)

        def tap(self, instance_id: str, point: object, *, approval_region: str) -> bool:
            taps.append((instance_id, point, approval_region))
            return True

    async def _fake_scan_panel_rows(
        _image_bgr: object,
        *,
        ocr: object,
        with_status: bool = True,
    ) -> list[dict[str, object]]:
        _ = (ocr, with_status)
        return [
            {
                "section": "trek",
                "row": "tundra_trek",
                "button": "green",
                "cy": 620,
            }
        ]

    fake_actions = _FakeActions()
    monkeypatch.setattr(mod.dsl_runtime, "bot_actions", lambda: fake_actions)
    monkeypatch.setattr(mod.dsl_runtime, "ocr_client", lambda: object())
    monkeypatch.setattr(mod, "_scan_panel_rows", _fake_scan_panel_rows)
    monkeypatch.setattr(mod.asyncio, "sleep", lambda _delay: _noop_async())

    ctx = DslExecContext(
        redis_client=None,
        player_id="p1",
        instance_id="bs1",
        args={
            "section": "trek",
            "row": "tundra_trek",
            "approval_region": "main_menu.panel.trek.tundra_trek",
        },
        result={},
    )
    await mod.DSL_EXEC_HANDLERS["tap_main_menu_panel_row"](ctx)

    assert ctx.result == {
        "action": "tapped",
        "section": "trek",
        "row": "tundra_trek",
        "sweep": 0,
    }
    assert taps[0][0] == "bs1"
    assert taps[0][2] == "main_menu.panel.trek.tundra_trek"


async def _noop_async() -> None:
    return None
