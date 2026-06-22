from __future__ import annotations

import io
import json
import zipfile
from typing import TYPE_CHECKING

import pytest
import yaml
from PIL import Image

if TYPE_CHECKING:
    from pathlib import Path

from config.reference_naming import TEMPORAL_SUBDIR


def _png_bytes(width: int = 720, height: int = 1280, color: tuple = (12, 34, 56)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_bundle(manifest: dict, png: bytes | None = None) -> bytes:
    base = {
        "bundle_version": 1,
        "kind": "autopilot.screen-label",
        "image": "screenshot.png",
        "regions": [],
    }
    base.update(manifest)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("label.json", json.dumps(base))
        zf.writestr("screenshot.png", png if png is not None else _png_bytes())
    return buf.getvalue()


_BBOX = {"x": 10.0, "y": 20.0, "width": 30.0, "height": 5.0,
         "rotation": 0.0, "original_width": 720, "original_height": 1280}


@pytest.fixture
def bundle_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scoped module ``wos:ads`` with one annotated 720x1280 screen."""
    import api.services.labeling as labeling_mod
    import api.services.labeling_scope as labeling_scope_mod
    from config.module_discovery import _clear_module_discovery_caches
    from layout.area_manifest import clear_area_doc_cache

    (tmp_path / "area.json").write_text('{"version": 2, "screens": []}\n', encoding="utf-8")

    ads = tmp_path / "games" / "wos" / "ads"
    (ads / "references" / TEMPORAL_SUBDIR).mkdir(parents=True)
    (ads / "module.yaml").write_text(
        "id: ads\ntitle: Ads\narea: area.yaml\nreferences: references\n",
        encoding="utf-8",
    )
    ref_rel = "games/wos/ads/references/page.png"
    (tmp_path / ref_rel).write_bytes(_png_bytes())
    bbox = {"x": 10.0, "y": 20.0, "width": 30.0, "height": 5.0,
            "rotation": 0.0, "original_width": 720, "original_height": 1280}
    (ads / "area.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "screens": [
                    {
                        "id": 1,
                        "screen_id": "ads_page",
                        "ocr": "references/page.png",
                        "regions": [{"name": "ads.btn", "action": "exist", "bbox": bbox}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(labeling_mod, "_REPO", tmp_path)
    monkeypatch.setattr(labeling_scope_mod, "_REPO", tmp_path)
    _clear_module_discovery_caches()
    clear_area_doc_cache()
    return tmp_path


def test_export_bundle_packs_image_and_manifest(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import export_screen_bundle

    filename, data = export_screen_bundle("games/wos/ads/references/page.png", scope="ads")
    assert filename == "page.alabel.zip"

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert {"label.json", "screenshot.png"} <= names
        manifest = json.loads(zf.read("label.json"))
        png = zf.read("screenshot.png")

    assert manifest["kind"] == "autopilot.screen-label"
    assert manifest["bundle_version"] == 1
    assert manifest["screen_id"] == "ads_page"
    assert manifest["basename"] == "page"
    assert manifest["image_size"] == {"width": 720, "height": 1280}
    assert manifest["regions"][0]["name"] == "ads.btn"
    with Image.open(io.BytesIO(png)) as img:
        assert img.size == (720, 1280)


def test_roundtrip_export_then_import(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import export_screen_bundle, import_screen_bundle

    _, data = export_screen_bundle("games/wos/ads/references/page.png", scope="ads")
    result = import_screen_bundle(data, scope="ads")

    assert result["ok"] is True
    assert result["screen_id"] == "ads_page"
    assert result["regions"][0]["name"] == "ads.btn"
    assert result["ref"].startswith("games/wos/ads/references/temporal/")
    assert (bundle_repo / result["ref"]).is_file()
    # Import stages for review — it must NOT touch area.yaml.
    area = yaml.safe_load(
        (bundle_repo / "games/wos/ads/area.yaml").read_text(encoding="utf-8")
    )
    assert len(area["screens"]) == 1


def test_import_rejects_bad_zip(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import BundleError, import_screen_bundle

    with pytest.raises(BundleError, match=r"valid \.zip"):
        import_screen_bundle(b"not a zip", scope="ads")


def test_import_rejects_wrong_image_size(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import BundleError, import_screen_bundle

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "label.json",
            json.dumps(
                {
                    "bundle_version": 1,
                    "kind": "autopilot.screen-label",
                    "basename": "page",
                    "screen_id": "ads_page",
                    "regions": [],
                }
            ),
        )
        zf.writestr("screenshot.png", _png_bytes(640, 480))

    with pytest.raises(BundleError, match="expected 720x1280"):
        import_screen_bundle(buf.getvalue(), scope="ads")


def test_import_detects_conflict_by_screen_id(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import import_screen_bundle

    changed_btn = {**_BBOX, "x": 50.0}  # same name, different bbox → "changed"
    bundle = _make_bundle(
        {
            "basename": "page2",  # different basename → only screen_id matches
            "screen_id": "ads_page",
            "regions": [
                {"name": "ads.btn", "action": "exist", "bbox": changed_btn},
                {"name": "ads.extra", "action": "exist", "bbox": _BBOX},
            ],
        }
    )
    out = import_screen_bundle(bundle, scope="ads")
    conflict = out["conflict"]
    assert conflict is not None
    assert conflict["matched_by"] == "screen_id"
    assert conflict["existing_ref"] == "games/wos/ads/references/page.png"
    assert conflict["diff"] == {
        "added": ["ads.extra"],
        "removed": [],
        "changed": ["ads.btn"],
        "unchanged": [],
    }


def test_import_no_conflict_when_screen_is_new(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import import_screen_bundle

    bundle = _make_bundle({"basename": "brand_new", "screen_id": "totally_new", "regions": []})
    out = import_screen_bundle(bundle, scope="ads")
    assert out["conflict"] is None


def test_apply_keep_existing_image_merges_regions(bundle_repo: Path) -> None:
    import yaml

    from api.services.labeling_bundle import apply_imported_bundle, import_screen_bundle

    page_png = bundle_repo / "games/wos/ads/references/page.png"
    before = page_png.read_bytes()
    bundle = _make_bundle(
        {"basename": "page2", "screen_id": "ads_page",
         "regions": [{"name": "ads.extra", "action": "exist", "bbox": _BBOX}]},
        png=_png_bytes(color=(200, 100, 50)),
    )
    out = import_screen_bundle(bundle, scope="ads")
    merged = [
        {"name": "ads.btn", "action": "exist", "bbox": _BBOX},   # keep existing
        {"name": "ads.extra", "action": "exist", "bbox": _BBOX}, # add incoming
    ]
    res = apply_imported_bundle(
        scope="ads",
        staged_ref=out["ref"],
        target_ref=out["conflict"]["existing_ref"],
        regions=merged,
        screen_id="ads_page",
        use_incoming_image=False,
    )
    assert res["ref"] == "games/wos/ads/references/page.png"
    # Existing PNG untouched; staged temporal removed.
    assert page_png.read_bytes() == before
    assert not (bundle_repo / out["ref"]).is_file()
    area = yaml.safe_load((bundle_repo / "games/wos/ads/area.yaml").read_text(encoding="utf-8"))
    names = {r["name"] for r in area["screens"][0]["regions"]}
    assert names == {"ads.btn", "ads.extra"}


def test_apply_use_incoming_image_overwrites_png(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import apply_imported_bundle, import_screen_bundle

    page_png = bundle_repo / "games/wos/ads/references/page.png"
    bundle = _make_bundle(
        {"basename": "page2", "screen_id": "ads_page",
         "regions": [{"name": "ads.btn", "action": "exist", "bbox": _BBOX}]},
        png=_png_bytes(color=(200, 100, 50)),
    )
    out = import_screen_bundle(bundle, scope="ads")
    apply_imported_bundle(
        scope="ads",
        staged_ref=out["ref"],
        target_ref=out["conflict"]["existing_ref"],
        regions=[{"name": "ads.btn", "action": "exist", "bbox": _BBOX}],
        screen_id="ads_page",
        use_incoming_image=True,
    )
    with Image.open(page_png) as img:
        assert img.convert("RGB").getpixel((0, 0)) == (200, 100, 50)
    assert not (bundle_repo / out["ref"]).is_file()


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers.labeling import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_http_export_then_import_bundle(bundle_repo: Path) -> None:
    with _client() as client:
        resp = client.get(
            "/api/labeling/references/games/wos/ads/references/page.png/bundle"
            "?scope=ads&game=wos"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "page.alabel.zip" in resp.headers["content-disposition"]
        data = resp.content

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert json.loads(zf.read("label.json"))["screen_id"] == "ads_page"

        imp = client.post(
            "/api/labeling/import-bundle?game=wos",
            data={"scope": "ads"},
            files={"file": ("page.alabel.zip", data, "application/zip")},
        )
        assert imp.status_code == 200
        out = imp.json()
        assert out["ok"] is True
        assert out["regions"][0]["name"] == "ads.btn"
        assert out["ref"].startswith("games/wos/ads/references/temporal/")


def test_http_import_bad_zip_returns_400(bundle_repo: Path) -> None:
    with _client() as client:
        resp = client.post(
            "/api/labeling/import-bundle?game=wos",
            data={"scope": "ads"},
            files={"file": ("x.zip", b"not a zip", "application/zip")},
        )
    assert resp.status_code == 400


def test_import_rejects_wrong_kind(bundle_repo: Path) -> None:
    from api.services.labeling_bundle import BundleError, import_screen_bundle

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("label.json", json.dumps({"bundle_version": 1, "kind": "other", "regions": []}))
        zf.writestr("screenshot.png", _png_bytes())

    with pytest.raises(BundleError, match="unexpected kind"):
        import_screen_bundle(buf.getvalue(), scope="ads")
