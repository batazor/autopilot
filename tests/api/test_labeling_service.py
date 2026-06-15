from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from config.reference_naming import TEMPORAL_SUBDIR, rolling_preview_basename


@pytest.fixture
def labeling_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    refs = tmp_path / "references" / TEMPORAL_SUBDIR
    refs.mkdir(parents=True)
    instance_id = "emu-1"
    rolling = refs / f"{rolling_preview_basename(instance_id)}.png"
    rolling.write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "area.json").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")

    import api.services.labeling as labeling_mod
    import api.services.labeling_scope as labeling_scope_mod

    monkeypatch.setattr(labeling_mod, "_REPO", tmp_path)
    monkeypatch.setattr(labeling_scope_mod, "_REPO", tmp_path)
    return tmp_path


def test_list_reference_paths_includes_temporal_shots_but_not_rolling(
    labeling_repo: Path,
) -> None:
    refs = labeling_repo / "references"
    shot = refs / TEMPORAL_SUBDIR / "emu-1_shot_test.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    permanent = refs / "main_city.png"
    permanent.write_bytes(b"\x89PNG\r\n\x1a\n")

    from api.services.labeling import list_reference_paths

    rels = {r["rel"] for r in list_reference_paths(limit=50)}
    assert f"references/{TEMPORAL_SUBDIR}/emu-1_shot_test.png" in rels
    assert "references/main_city.png" in rels
    assert f"references/{TEMPORAL_SUBDIR}/emu-1_current_state.png" not in rels


def test_list_reference_paths_module_scope_includes_temporal(
    labeling_repo: Path,
) -> None:
    ads_root = labeling_repo / "games" / "wos" / "ads"
    refs = ads_root / "references"
    temporal = refs / TEMPORAL_SUBDIR
    temporal.mkdir(parents=True)
    (ads_root / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (ads_root / "area.yaml").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")
    shot = temporal / "bs1_shot_test.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")

    from api.services.labeling import list_reference_paths

    rels = {r["rel"] for r in list_reference_paths(scope="ads", limit=50)}
    assert "games/wos/ads/references/temporal/bs1_shot_test.png" in rels


def test_kingshot_labeling_scopes_and_all_refs_do_not_include_wos(
    labeling_repo: Path,
) -> None:
    from api.services.game_resolver import set_current_request_game
    from api.services.labeling import list_reference_paths
    from api.services.labeling_scope import list_labeling_scopes
    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import clear_area_doc_cache

    for game, title in (("wos", "WOS Main"), ("kingshot", "Kingshot Main")):
        mod = labeling_repo / "games" / game / "core" / "main_city"
        (mod / "references").mkdir(parents=True)
        (mod / "module.yaml").write_text(
            f"id: main_city\ntitle: {title}\narea: area.yaml\nreferences: references\n",
            encoding="utf-8",
        )
        (mod / "area.yaml").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")
        (mod / "references" / f"{game}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    _clear_module_discovery_caches()
    clear_area_doc_cache()
    set_current_request_game("kingshot")
    try:
        scope_keys = {s["key"] for s in list_labeling_scopes()}
        rels = {r["rel"] for r in list_reference_paths(scope="all", limit=50)}
    finally:
        set_current_request_game("wos")

    assert "kingshot:core/main_city" in scope_keys
    assert "wos:core/main_city" not in scope_keys
    assert "games/kingshot/core/main_city/references/kingshot.png" in rels
    assert "games/wos/core/main_city/references/wos.png" not in rels


def test_http_labeling_scopes_respect_game_query(labeling_repo: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers.labeling import router
    from config.module_discovery import _clear_module_discovery_caches

    for game in ("wos", "kingshot"):
        mod = labeling_repo / "games" / game / "core" / "main_city"
        (mod / "references").mkdir(parents=True)
        (mod / "module.yaml").write_text(
            "id: main_city\ntitle: Main City\narea: area.yaml\nreferences: references\n",
            encoding="utf-8",
        )
        (mod / "area.yaml").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")

    _clear_module_discovery_caches()
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        data = client.get("/api/labeling/scopes?game=kingshot").json()

    keys = {s["key"] for s in data["scopes"]}
    assert "kingshot:core/main_city" in keys
    assert "wos:core/main_city" not in keys


def test_all_scope_requires_exact_module_reference_path(labeling_repo: Path) -> None:
    import yaml

    from api.services.labeling import get_labeling_document
    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import clear_area_doc_cache

    common = labeling_repo / "games" / "wos" / "core" / "common"
    who_i_am = labeling_repo / "games" / "wos" / "core" / "who_i_am"
    for module_dir, module_id in ((common, "common"), (who_i_am, "who_i_am")):
        (module_dir / "references").mkdir(parents=True)
        (module_dir / "module.yaml").write_text(
            f"id: {module_id}\narea: area.yaml\nreferences: references\n",
            encoding="utf-8",
        )

    common_ref = "games/wos/core/common/references/chief_profile.png"
    who_ref = "games/wos/core/who_i_am/references/chief_profile.png"
    (labeling_repo / common_ref).write_bytes(b"common")
    (labeling_repo / who_ref).write_bytes(b"who")
    (common / "area.yaml").write_text(
        yaml.safe_dump({"version": 2, "screens": []}),
        encoding="utf-8",
    )
    (who_i_am / "area.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "screens": [
                    {
                        "screen_id": "chief_profile",
                        "ocr": "references/chief_profile.png",
                        "regions": [{"name": "player.id", "action": "text"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _clear_module_discovery_caches()
    clear_area_doc_cache()

    common_doc = get_labeling_document(common_ref, scope="all")
    assert common_doc["entry_id"] is None
    assert common_doc["regions"] == []

    who_doc = get_labeling_document(who_ref, scope="all")
    assert who_doc["screen_id"] == "chief_profile"
    assert who_doc["regions"][0]["name"] == "player.id"


def test_import_dropped_png(labeling_repo: Path) -> None:
    from api.services.labeling import import_dropped_png

    out = import_dropped_png(b"\x89PNG\r\n\x1a\nshot", "emu-1")
    assert out["ok"] is True
    assert out["ref"].startswith(f"references/{TEMPORAL_SUBDIR}/emu-1_shot_")
    assert (labeling_repo / out["ref"]).is_file()

    with pytest.raises(ValueError, match="instance_id"):
        import_dropped_png(b"x", "")


def test_capture_new_screenshot(labeling_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services import labeling as labeling_mod

    def _fake_copy(instance_id: str, target: Path) -> tuple[bool, str]:
        target.write_bytes(b"shot")
        return True, ""

    monkeypatch.setattr(labeling_mod, "capture_preview_to", _fake_copy)

    out = labeling_mod.capture_new_screenshot("emu-1")
    assert out["ok"] is True
    assert out["ref"].startswith(f"references/{TEMPORAL_SUBDIR}/emu-1_shot_")
    assert (labeling_repo / out["ref"]).is_file()


def test_discard_pending_capture(labeling_repo: Path) -> None:
    from api.services.labeling import discard_pending_capture

    shot_rel = "references/temporal/emu-1_shot_discard.png"
    shot = labeling_repo / shot_rel
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"x")

    out = discard_pending_capture(shot_rel)
    assert out["ok"] is True
    assert not shot.is_file()

    with pytest.raises(ValueError, match="temporal"):
        discard_pending_capture("references/main_city.png")

    # rolling preview guard
    rolling_rel = f"references/{TEMPORAL_SUBDIR}/emu-1_current_state.png"
    with pytest.raises(ValueError, match="rolling"):
        discard_pending_capture(rolling_rel)


def test_promote_pending_capture(labeling_repo: Path) -> None:
    from api.services.labeling import promote_reference

    ads_root = labeling_repo / "games" / "wos" / "ads"
    (ads_root / "references" / TEMPORAL_SUBDIR).mkdir(parents=True)
    (ads_root / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (ads_root / "area.yaml").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")

    shot_rel = "games/wos/ads/references/temporal/emu-1_shot_promote.png"
    shot = labeling_repo / shot_rel
    shot.write_bytes(b"x")

    out = promote_reference(
        shot_rel,
        "main_city",
        "emu-1",
        regions=[{"name": "btn", "action": "exist", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}],
        screen_id="main_city",
        scope="ads",
    )
    assert out["ok"] is True
    assert out["ref"] == "games/wos/ads/references/main_city.png"
    assert not shot.is_file()
    assert (labeling_repo / "games/wos/ads/references/main_city.png").is_file()

    import json

    doc = json.loads((ads_root / "area.yaml").read_text(encoding="utf-8"))
    entry = next(
        s for s in doc["screens"]
        if s.get("ocr") in ("references/main_city.png", "games/wos/ads/references/main_city.png")
    )
    assert entry["screen_id"] == "main_city"
    assert entry["regions"][0]["name"] == "btn"


def test_promote_pending_capture_from_all_scope_writes_module_area(
    labeling_repo: Path,
) -> None:
    from api.services.labeling import promote_reference
    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import clear_area_doc_cache

    ads_root = labeling_repo / "games" / "wos" / "ads"
    (ads_root / "references" / TEMPORAL_SUBDIR).mkdir(parents=True)
    (ads_root / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (ads_root / "area.yaml").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")
    _clear_module_discovery_caches()
    clear_area_doc_cache()

    shot_rel = "games/wos/ads/references/temporal/emu-1_shot_promote_all.png"
    shot = labeling_repo / shot_rel
    shot.write_bytes(b"x")

    out = promote_reference(
        shot_rel,
        "main_city",
        "emu-1",
        regions=[{"name": "btn", "action": "exist", "bbox": {"x": 1}}],
        screen_id="main_city",
        scope="all",
    )

    assert out["ok"] is True
    assert out["ref"] == "games/wos/ads/references/main_city.png"
    assert not shot.is_file()
    assert (labeling_repo / "games/wos/ads/references/main_city.png").is_file()

    import json

    doc = json.loads((ads_root / "area.yaml").read_text(encoding="utf-8"))
    assert doc["screens"][0]["ocr"] == "references/main_city.png"
    assert doc["screens"][0]["screen_id"] == "main_city"


def test_http_promote_missing_pending_capture_returns_404(labeling_repo: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers.labeling import router
    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import clear_area_doc_cache

    ads_root = labeling_repo / "games" / "wos" / "ads"
    (ads_root / "references" / TEMPORAL_SUBDIR).mkdir(parents=True)
    (ads_root / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (ads_root / "area.yaml").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")
    _clear_module_discovery_caches()
    clear_area_doc_cache()

    app = FastAPI()
    app.include_router(router)
    body = {
        "ref": "games/wos/ads/references/temporal/bs1_shot_20260612_202547_35beb5.png",
        "basename": "main_city",
        "instance_id": "bs1",
        "regions": [],
        "screen_id": "main_city",
    }
    with TestClient(app) as client:
        resp = client.post("/api/labeling/promote?scope=all&game=wos", json=body)

    assert resp.status_code == 404
    assert "Source missing" in resp.json()["detail"]


def test_list_screen_id_options(labeling_repo: Path) -> None:
    (labeling_repo / "area.json").write_text(
        '{"version": 2, "screens": [{"screen_id": "vip", "ocr": "references/x.png", "regions": []}]}\n',
        encoding="utf-8",
    )
    from api.services.labeling import list_screen_id_options

    opts = list_screen_id_options(scope="core", current_screen_id="custom_node")
    assert opts[0] == ""
    assert "vip" in opts
    assert "custom_node" in opts
    assert "main_city" in opts


def test_save_labeling_regions_syncs_analyze_on_bbox_rename(labeling_repo: Path) -> None:
    import yaml

    from api.services.labeling import save_labeling_regions

    mod = labeling_repo / "games" / "wos" / "ads"
    refs = mod / "references"
    refs.mkdir(parents=True)
    (mod / "analyze").mkdir(parents=True)
    (mod / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\nanalyze: analyze/analyze.yaml\n",
        encoding="utf-8",
    )
    ref_rel = "games/wos/ads/references/ads.natalia.png"
    (labeling_repo / ref_rel).write_bytes(b"x")
    bbox = {
        "x": 84.5,
        "y": 3.3,
        "width": 7.6,
        "height": 4.6,
        "rotation": 0.0,
        "original_width": 720,
        "original_height": 1280,
    }
    (mod / "area.yaml").write_text(
        yaml.dump(
            {
                "version": 2,
                "screens": [
                    {
                        "id": 1,
                        "screen_id": "ads.natalia",
                        "ocr": "references/ads.natalia.png",
                        "screen_region": "ads.natalia",
                        "regions": [
                            {"name": "ads.natalia", "action": "exist", "bbox": bbox},
                        ],
                    }
                ],
            },
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    (mod / "analyze" / "analyze.yaml").write_text(
        yaml.dump(
            {
                "overlay": [
                    {
                        "name": "ads.natalia.visible",
                        "region": "ads.natalia",
                        "action": "findIcon",
                        "pushScenario": [{"name": "ads_natalia"}],
                    }
                ]
            },
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    (mod / "scenarios").mkdir(parents=True)
    (mod / "scenarios" / "ads_natalia.yaml").write_text(
        "steps:\n  - click: ads.natalia\n",
        encoding="utf-8",
    )

    out = save_labeling_regions(
        ref_rel,
        [{"name": "ads.natalia.title", "action": "exist", "bbox": bbox}],
        scope="ads",
    )
    assert out["region_renames_synced"]
    assert out["region_renames_synced"][0]["analyze"] is True

    analyze = yaml.safe_load(
        (mod / "analyze" / "analyze.yaml").read_text(encoding="utf-8")
    )
    assert analyze["overlay"][0]["region"] == "ads.natalia.title"

    area = yaml.safe_load((mod / "area.yaml").read_text(encoding="utf-8"))
    assert area["screens"][0]["screen_region"] == "ads.natalia.title"
    assert area["screens"][0]["regions"][0]["name"] == "ads.natalia.title"

    scenario = (mod / "scenarios" / "ads_natalia.yaml").read_text(encoding="utf-8")
    assert "ads.natalia.title" in scenario
    assert "click: ads.natalia\n" not in scenario


def test_save_labeling_regions_from_all_scope_writes_module_area(
    labeling_repo: Path,
) -> None:
    import yaml

    from api.services.labeling import save_labeling_regions
    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import clear_area_doc_cache

    mod = labeling_repo / "games" / "wos" / "events" / "icefire"
    (mod / "references").mkdir(parents=True)
    (mod / "module.yaml").write_text(
        "id: icefire\ntitle: Icefire\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (mod / "area.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "screens": [
                    {
                        "screen_id": "icefire",
                        "ocr": "references/icefire.png",
                        "regions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ref_rel = "games/wos/events/icefire/references/icefire.png"
    (labeling_repo / ref_rel).write_bytes(b"x")
    _clear_module_discovery_caches()
    clear_area_doc_cache()

    out = save_labeling_regions(
        ref_rel,
        [{"name": "icefire.banner.title", "action": "exist", "bbox": {"x": 1}}],
        scope="all",
        screen_id="icefire",
    )

    assert out["ok"] is True
    area = yaml.safe_load((mod / "area.yaml").read_text(encoding="utf-8"))
    assert area["screens"][0]["ocr"] == "references/icefire.png"
    assert area["screens"][0]["regions"][0]["name"] == "icefire.banner.title"


def test_add_and_save_version_regions(labeling_repo: Path) -> None:
    from api.services import labeling as labeling_mod
    from api.services.labeling import add_version, get_labeling_document, save_labeling_regions

    ads_root = labeling_repo / "games" / "wos" / "ads"
    (ads_root / "references").mkdir(parents=True)
    (ads_root / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    (ads_root / "area.yaml").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")

    ref_rel = "games/wos/ads/references/page.png"
    (labeling_repo / ref_rel).write_bytes(b"x")
    labeling_mod.save_labeling_regions(
        ref_rel,
        [{"name": "base", "action": "exist", "bbox": {"x": 0, "y": 0, "width": 10, "height": 10}}],
        scope="ads",
    )
    add_version(ref_rel, "v2", "heroes.norah.level >= 6", scope="ads")
    save_labeling_regions(
        ref_rel,
        [{"name": "v2btn", "action": "exist", "bbox": {"x": 5, "y": 5, "width": 5, "height": 5}}],
        version="v2",
        scope="ads",
    )
    doc = get_labeling_document(ref_rel, version="v2", scope="ads")
    assert doc["active_version"] == "v2"
    assert doc["regions"][0]["name"] == "v2btn"
    base = get_labeling_document(ref_rel, scope="ads")
    assert base["regions"][0]["name"] == "base"
