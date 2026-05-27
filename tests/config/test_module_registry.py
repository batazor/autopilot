from __future__ import annotations

from pathlib import Path

import yaml

from config.module_registry import (
    CORE_MODULE_KEY,
    collect_reference_rels_from_doc,
    get_wiki_module,
    list_wiki_modules,
    ocr_path_belongs_to_context,
)


def test_list_wiki_modules_returns_feature_modules(tmp_path: Path) -> None:
    mod = tmp_path / "modules" / "vip"
    (mod / "references").mkdir(parents=True)
    (mod / "module.yaml").write_text(
        yaml.safe_dump({"id": "vip", "title": "VIP", "references": "references"}),
        encoding="utf-8",
    )

    ctxs = list_wiki_modules(tmp_path)
    keys = [c.storage_key for c in ctxs]
    assert "vip" in keys
    assert CORE_MODULE_KEY not in keys  # Core scope is no longer enumerated
    vip = get_wiki_module(tmp_path, "vip")
    assert vip.references_dir.resolve() == (mod / "references").resolve()
    assert vip.area_path.resolve() == (mod / "area.yaml").resolve()


def test_module_local_references_prefix(tmp_path: Path) -> None:
    mod = tmp_path / "modules" / "vip"
    (mod / "references").mkdir(parents=True)
    (mod / "module.yaml").write_text(
        yaml.safe_dump({"id": "vip", "title": "VIP", "references": "references"}),
        encoding="utf-8",
    )
    vip = get_wiki_module(tmp_path, "vip")
    assert vip.references_prefix == "modules/vip/references"


def test_nested_module_context_uses_storage_key_and_area_yaml(tmp_path: Path) -> None:
    mod = tmp_path / "modules" / "events" / "trials"
    (mod / "references").mkdir(parents=True)
    (mod / "area.yaml").write_text("screens: []\n", encoding="utf-8")
    (mod / "module.yaml").write_text(
        yaml.safe_dump({"id": "trials", "title": "Trials", "references": "references"}),
        encoding="utf-8",
    )

    trials = get_wiki_module(tmp_path, "events/trials")

    assert trials.module_id == "trials"
    assert trials.storage_key == "events/trials"
    assert trials.references_prefix == "modules/events/trials/references"
    assert trials.area_path.resolve() == (mod / "area.yaml").resolve()
    assert get_wiki_module(tmp_path, "trials").storage_key == "events/trials"


def test_core_module_defaults_to_local_area_and_references(tmp_path: Path) -> None:
    mod = tmp_path / "modules" / "core" / "chief_profile"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text(
        yaml.safe_dump({"id": "chief_profile", "title": "Chief profile"}),
        encoding="utf-8",
    )

    ctx = get_wiki_module(tmp_path, "chief_profile")

    assert ctx.area_path.resolve() == (mod / "area.yaml").resolve()
    assert ctx.references_dir.resolve() == (mod / "references").resolve()
    assert ctx.references_prefix == "modules/core/chief_profile/references"


def test_module_default_ref_from_manifest(tmp_path: Path) -> None:
    mod = tmp_path / "modules" / "core" / "who_i_am"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "who_i_am",
                "title": "Who am I",
                "references": "../../../references",
                "default_ref": "chief_profile.png",
            }
        ),
        encoding="utf-8",
    )

    ctx = get_wiki_module(tmp_path, "who_i_am")

    assert ctx.default_ref == "chief_profile.png"


def test_ocr_path_belongs_to_context() -> None:
    from config.module_registry import WikiModuleContext

    core = WikiModuleContext(
        module_id=None,
        title="Core",
        repo_root=Path("/repo"),
        module_dir=None,
        references_dir=Path("/repo/references"),
        references_prefix="references",
        area_path=Path("/repo/area.json"),
    )
    mod = WikiModuleContext(
        module_id="vip",
        title="VIP",
        repo_root=Path("/repo"),
        module_dir=Path("/repo/modules/vip"),
        references_dir=Path("/repo/modules/vip/references"),
        references_prefix="modules/vip/references",
        area_path=Path("/repo/modules/vip/area.yaml"),
    )
    assert ocr_path_belongs_to_context("references/main.png", core)
    assert not ocr_path_belongs_to_context("modules/vip/references/x.png", core)
    assert ocr_path_belongs_to_context("modules/vip/references/x.png", mod)


def test_collect_reference_rels_from_doc() -> None:
    from config.module_registry import WikiModuleContext

    ctx = WikiModuleContext(
        module_id="vip",
        title="VIP",
        repo_root=Path("/repo"),
        module_dir=Path("/repo/modules/vip"),
        references_dir=Path("/repo/modules/vip/references"),
        references_prefix="modules/vip/references",
        area_path=Path("/repo/modules/vip/area.yaml"),
    )
    doc = {
        "screens": [
            {"ocr": "modules/vip/references/page.vip.png"},
            {"ocr": "references/other.png"},
        ]
    }
    refs = collect_reference_rels_from_doc(doc, ctx)
    assert refs == {"page.vip.png"}
