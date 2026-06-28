"""Tests for the live-label commit engine (``api.services.labeling``).

Covers the surgical-commit invariant — sibling regions and the screen reference
PNG are left untouched, and only the changed region(s) are cropped from the
pinned frame — plus the region-dict normalizer.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image

from api.services import labeling


def test_normalize_region_defaults_and_passthrough() -> None:
    out = labeling._normalize_region_for_save(
        {
            "name": "  mail.claim  ",
            "action": "text",
            "type": "integer",
            "has_red_dot": True,
            "bbox": {"x": 10, "y": 20, "width": 5, "height": 4},
        }
    )
    assert out["name"] == "mail.claim"
    assert out["action"] == "text"
    assert out["type"] == "integer"
    assert out["has_red_dot"] is True
    assert out["threshold"] == 0.9
    # percent bbox is filled out with the 720x1280 reference + rotation default
    assert out["bbox"]["original_width"] == 720
    assert out["bbox"]["original_height"] == 1280
    assert out["bbox"]["rotation"] == 0.0


def test_normalize_region_rejects_empty_bbox() -> None:
    with pytest.raises(ValueError, match="empty bbox"):
        labeling._normalize_region_for_save(
            {"name": "x", "bbox": {"x": 1, "y": 1, "width": 0, "height": 4}}
        )


def test_commit_surgical_preserves_siblings(tmp_path, monkeypatch) -> None:
    repo = tmp_path
    refs_prefix = "games/g/m/references"
    ocr_rel = f"{refs_prefix}/screen.png"
    area_path = repo / "games/g/m/area.yaml"
    env = SimpleNamespace(
        repo_root=repo,
        ref_root=repo / "games/g/m/references",
        references_prefix=refs_prefix,
        area_path=area_path,
        ctx=SimpleNamespace(repo_root=repo, is_all=False),
    )
    doc = {
        "version": 2,
        "screens": [
            {
                "screen_id": "scr",
                "ocr": ocr_rel,
                "regions": [
                    {
                        "name": "sibling",
                        "action": "exist",
                        "bbox": {
                            "x": 1, "y": 1, "width": 5, "height": 5,
                            "rotation": 0, "original_width": 720, "original_height": 1280,
                        },
                    }
                ],
            }
        ],
    }

    monkeypatch.setattr(labeling.ls, "scope_env", lambda *_: env)
    monkeypatch.setattr(labeling, "_write_env_for_reference", lambda e, *_: e)
    monkeypatch.setattr(labeling.ls, "load_area_doc", lambda *_: doc)
    monkeypatch.setattr(labeling.ls, "entry_for_ref", lambda d, *_: (0, d["screens"][0]))
    monkeypatch.setattr(labeling, "_require_writable_area_path", lambda *_: area_path)
    monkeypatch.setattr(labeling, "_publish_area_manifest_changed", lambda: None)

    # A real pinned frame so the (real) frame read + crop math have pixels.
    pin = tmp_path / "label_pin_bs1.png"
    Image.new("RGB", (720, 1280), (10, 20, 30)).save(pin)
    monkeypatch.setattr(labeling, "_label_pin_path", lambda *_: pin)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        labeling, "_atomic_write_json", lambda path, data: captured.update(doc=data, path=path)
    )

    crop_calls: dict[str, object] = {}

    def fake_crop(pil, ref, regions, *, repo_root=None):
        crop_calls["ref"] = ref
        crop_calls["regions"] = [r["name"] for r in regions]
        crop_calls["size"] = pil.size
        return [repo / "games/g/m/references/crop/screen_new.png"]

    monkeypatch.setattr(labeling, "_crop_regions_from_image", fake_crop)

    out = labeling.commit_region_from_frame(
        instance_id="bs1",
        regions=[
            {
                "name": "new",
                "action": "text",
                "type": "integer",
                "bbox": {"x": 50, "y": 50, "width": 10, "height": 4},
            }
        ],
        ref=ocr_rel,
        screen_id="scr",
        scope="g:m",
        mode="surgical",
    )

    assert out["ok"] is True
    assert out["mode"] == "surgical"
    assert out["region_count"] == 1
    # Only the changed region is cropped — siblings are never re-cut.
    assert crop_calls["regions"] == ["new"]
    # Crop filename derives from the screen's own reference stem (repo-relative).
    assert crop_calls["ref"] == ocr_rel
    # Cropped from the pinned 720x1280 frame.
    assert crop_calls["size"] == (720, 1280)

    saved_regions = captured["doc"]["screens"][0]["regions"]  # type: ignore[index]
    assert [r["name"] for r in saved_regions] == ["sibling", "new"]
    new_region = next(r for r in saved_regions if r["name"] == "new")
    assert new_region["action"] == "text"
    assert new_region["type"] == "integer"
    assert new_region["bbox"]["original_width"] == 720
    # Sibling bbox untouched.
    sibling = next(r for r in saved_regions if r["name"] == "sibling")
    assert sibling["bbox"]["width"] == 5
