from ui.labeling_refresh_target import ocr_path_to_ref_rel, resolve_labeling_refresh_target_rel


def test_ocr_path_to_ref_rel() -> None:
    assert ocr_path_to_ref_rel("references/foo/bar.png") == "foo/bar.png"
    assert ocr_path_to_ref_rel("") is None
    assert ocr_path_to_ref_rel("other/foo.png") is None


def test_resolve_refresh_same_as_tree_when_no_version_image() -> None:
    rel, note = resolve_labeling_refresh_target_rel(
        "hero.png",
        entry_default_ref_rel="hero.png",
        active_version_ref_rel=None,
        temporal_subdir="temporal",
    )
    assert rel == "hero.png"
    assert note is None


def test_resolve_refresh_maps_to_version_png() -> None:
    rel, note = resolve_labeling_refresh_target_rel(
        "hero.png",
        entry_default_ref_rel="hero.png",
        active_version_ref_rel="hero_v2.png",
        temporal_subdir="temporal",
    )
    assert rel == "hero_v2.png"
    assert note and "hero_v2.png" in note


def test_resolve_refresh_skips_mapping_when_tree_not_default() -> None:
    rel, note = resolve_labeling_refresh_target_rel(
        "other.png",
        entry_default_ref_rel="hero.png",
        active_version_ref_rel="hero_v2.png",
        temporal_subdir="temporal",
    )
    assert rel == "other.png"
    assert note is None


def test_resolve_refresh_temporal_unchanged() -> None:
    rel, note = resolve_labeling_refresh_target_rel(
        "temporal/cap.png",
        entry_default_ref_rel="hero.png",
        active_version_ref_rel="hero_v2.png",
        temporal_subdir="temporal",
    )
    assert rel == "temporal/cap.png"
    assert note is None
