"""An overlay region only earns its place if it changes something.

Region lookup is first-wins with overlay screens prepended, so an overlay
declares only what it overrides — the rest of the screen still resolves from
base. A declaration that changes nothing is therefore not a no-op: it is a
snapshot of the base values that stops tracking them, on the build nobody is
looking at.

The trap this file mostly exists to hold shut: **the override is often not in
the region dict.** Both RU regions whose dicts match base verbatim
(``exit_confirm.body``, ``chapter.title``) are real overrides — same geometry on
purpose, different template crop at the same relative path. A check comparing
dicts alone flags them, and acting on it would delete what makes RU screen
detection work. So "identical" has to mean geometry *and* crop.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from config.startup_validation import _validate_overlay_region_drift

if TYPE_CHECKING:
    from pathlib import Path

_BBOX = {"x": 10.0, "y": 20.0, "width": 5.0, "height": 3.0}


def _area(path: Path, region: dict[str, Any], *, ref: str = "references/screen.png") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "screens": [{"screen_id": "s", "ocr": ref, "regions": [region]}],
            }
        ),
        encoding="utf-8",
    )


def _crop(module_dir: Path, region_name: str, data: bytes, *, ref_stem: str = "screen") -> None:
    crop = module_dir / "references" / "crop" / f"{ref_stem}_{region_name}.png"
    crop.parent.mkdir(parents=True, exist_ok=True)
    crop.write_bytes(data)


def _tree(
    tmp_path: Path,
    *,
    base_region: dict[str, Any],
    overlay_region: dict[str, Any],
    base_crop: bytes | None = None,
    overlay_crop: bytes | None = None,
) -> None:
    (tmp_path / "games" / "wos").mkdir(parents=True)
    base_dir = tmp_path / "games" / "wos" / "core" / "widget"
    ov_dir = tmp_path / "games" / "wos" / "ru" / "core" / "widget"
    _area(base_dir / "area.yaml", base_region)
    _area(ov_dir / "area.yaml", overlay_region)
    if base_crop is not None:
        _crop(base_dir, base_region["name"], base_crop)
    if overlay_crop is not None:
        _crop(ov_dir, overlay_region["name"], overlay_crop)


def _messages(tmp_path: Path) -> str:
    issues: list[Any] = []
    _validate_overlay_region_drift(tmp_path, issues)
    assert all(i.severity == "warning" for i in issues), "must never block a boot"
    return "\n".join(i.message for i in issues)


def test_same_geometry_and_same_crop_is_reported(tmp_path: Path) -> None:
    """The actual drift bomb: nothing differs, so nothing is overridden."""
    region = {"name": "w.title", "action": "exist", "bbox": dict(_BBOX)}
    _tree(
        tmp_path,
        base_region=region,
        overlay_region=dict(region),
        base_crop=b"PNG-identical",
        overlay_crop=b"PNG-identical",
    )

    assert "'w.title' has the same geometry AND the same crop" in _messages(tmp_path)


def test_same_geometry_with_a_different_crop_is_left_alone(tmp_path: Path) -> None:
    """The live RU case. Same bbox on purpose; the RU build ships its own crop
    at the same relative path. Flagging this is how you break RU detection."""
    region = {"name": "w.title", "action": "exist", "bbox": dict(_BBOX)}
    _tree(
        tmp_path,
        base_region=region,
        overlay_region=dict(region),
        base_crop=b"PNG-english",
        overlay_crop=b"PNG-russian-longer",
    )

    assert _messages(tmp_path) == ""


def test_neither_side_having_a_crop_still_counts_as_identical(tmp_path: Path) -> None:
    """No crop on either side means the template comes from the reference at
    the bbox — so identical dicts really do mean identical behaviour."""
    region = {"name": "w.title", "action": "exist", "bbox": dict(_BBOX)}
    _tree(tmp_path, base_region=region, overlay_region=dict(region))

    assert "overrides nothing" in _messages(tmp_path)


def test_a_crop_only_the_overlay_ships_is_an_override(tmp_path: Path) -> None:
    """Base falls back to the reference; the overlay pins an explicit tile."""
    region = {"name": "w.title", "action": "exist", "bbox": dict(_BBOX)}
    _tree(tmp_path, base_region=region, overlay_region=dict(region), overlay_crop=b"PNG-ru")

    assert _messages(tmp_path) == ""


def test_differing_geometry_is_the_mechanism_working(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        base_region={"name": "w.title", "action": "exist", "bbox": dict(_BBOX)},
        overlay_region={"name": "w.title", "action": "exist", "bbox": {**_BBOX, "y": 84.7}},
    )

    assert _messages(tmp_path) == ""


def test_a_differing_threshold_alone_is_an_override(tmp_path: Path) -> None:
    """RU's ``chapter.new`` loosens the threshold to 0.8 and keeps everything
    else — a one-key difference is still a difference."""
    _tree(
        tmp_path,
        base_region={"name": "w.title", "action": "exist", "threshold": 0.9, "bbox": dict(_BBOX)},
        overlay_region={"name": "w.title", "action": "exist", "threshold": 0.8, "bbox": dict(_BBOX)},
    )

    assert _messages(tmp_path) == ""


def test_a_comment_only_difference_does_not_hide_a_drift_bomb(tmp_path: Path) -> None:
    """``_comment`` is authoring prose. Letting it count as a difference would
    make the check trivially defeatable by the one thing every overlay has."""
    region = {"name": "w.title", "action": "exist", "bbox": dict(_BBOX)}
    _tree(
        tmp_path,
        base_region=region,
        overlay_region={**region, "_comment": "RU build, verified on bs5"},
    )

    assert "overrides nothing" in _messages(tmp_path)


def test_an_overlay_only_region_is_not_reported(tmp_path: Path) -> None:
    """``upgrade_button_top`` is RU-only and ``building.upgrade.yaml`` relies on
    it resolving to nothing on EN. Reporting it trains people to ignore this."""
    _tree(
        tmp_path,
        base_region={"name": "w.other", "action": "exist", "bbox": dict(_BBOX)},
        overlay_region={"name": "w.ru_only", "action": "exist", "bbox": dict(_BBOX)},
    )

    assert _messages(tmp_path) == ""


def test_two_base_modules_are_never_compared(tmp_path: Path) -> None:
    """This check is about the overlay relationship, not duplicate names —
    ``_validate_region_name_uniqueness`` owns those."""
    region = {"name": "w.title", "action": "exist", "bbox": dict(_BBOX)}
    (tmp_path / "games" / "wos").mkdir(parents=True)
    _area(tmp_path / "games" / "wos" / "core" / "a" / "area.yaml", region)
    _area(tmp_path / "games" / "wos" / "core" / "b" / "area.yaml", dict(region))

    assert _messages(tmp_path) == ""


def test_the_real_repo_is_clean_and_reports_only_warnings() -> None:
    """It finds nothing today, and that is the finding: every RU region either
    differs from base or ships its own crop."""
    from config.paths import repo_root

    issues: list[Any] = []
    _validate_overlay_region_drift(repo_root(), issues)

    assert issues == [], [i.message for i in issues]
