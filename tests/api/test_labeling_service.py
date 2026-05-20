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

    monkeypatch.setattr(labeling_mod, "copy_rolling_preview_to", _fake_copy)

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

    shot_rel = "references/temporal/emu-1_shot_promote.png"
    shot = labeling_repo / shot_rel
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"x")

    out = promote_reference(
        shot_rel,
        "main_city",
        "emu-1",
        regions=[{"name": "btn", "action": "exist", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}],
        screen_id="main_city",
    )
    assert out["ok"] is True
    assert out["ref"] == "references/main_city.png"
    assert not shot.is_file()
    assert (labeling_repo / "references/main_city.png").is_file()

    import json

    doc = json.loads((labeling_repo / "area.json").read_text(encoding="utf-8"))
    entry = next(s for s in doc["screens"] if s.get("ocr") == "references/main_city.png")
    assert entry["screen_id"] == "main_city"
    assert entry["regions"][0]["name"] == "btn"


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


def test_add_and_save_version_regions(labeling_repo: Path) -> None:
    from api.services import labeling as labeling_mod
    from api.services.labeling import add_version, get_labeling_document, save_labeling_regions

    ref_rel = "references/page.png"
    (labeling_repo / ref_rel).write_bytes(b"x")
    labeling_mod.save_labeling_regions(
        ref_rel,
        [{"name": "base", "action": "exist", "bbox": {"x": 0, "y": 0, "width": 10, "height": 10}}],
    )
    add_version(ref_rel, "v2", "heroes.norah.level >= 6")
    save_labeling_regions(
        ref_rel,
        [{"name": "v2btn", "action": "exist", "bbox": {"x": 5, "y": 5, "width": 5, "height": 5}}],
        version="v2",
    )
    doc = get_labeling_document(ref_rel, version="v2")
    assert doc["active_version"] == "v2"
    assert doc["regions"][0]["name"] == "v2btn"
    base = get_labeling_document(ref_rel)
    assert base["regions"][0]["name"] == "base"
