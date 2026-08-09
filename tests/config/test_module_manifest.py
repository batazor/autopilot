"""One parse of ``module.yaml``, one model, and a check that declarations are live.

The file had four independent loaders plus a fifth pass that opened it again
just to read ``enabled``, and its fields were then handed around as raw dicts.
That is how `area:` came to be honoured by the wiki editor and ignored by
discovery, and how two manifests kept pointing at `../../../area.json` — a file
Phase 3 removed — without anything noticing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from config.module_discovery import (
    KNOWN_MANIFEST_KEYS,
    ModuleManifest,
    clear_manifest_cache,
    load_manifest,
    load_module_yaml,
    module_meta_id,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    clear_manifest_cache()
    yield
    clear_manifest_cache()


def _write(module_dir: Path, body: dict[str, object]) -> Path:
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.yaml").write_text(yaml.dump(body), encoding="utf-8")
    return module_dir


def test_defaults_when_the_manifest_is_absent(tmp_path: Path) -> None:
    """A module dir with no manifest must still resolve, not explode."""
    manifest = load_manifest(tmp_path / "ghost")

    assert manifest.id == "ghost"
    assert manifest.enabled is True
    assert manifest.scenarios_dir.name == "scenarios"


def test_id_falls_back_to_the_directory_name(tmp_path: Path) -> None:
    mod = _write(tmp_path / "arena", {"title": "Arena"})

    assert load_manifest(mod).id == "arena"
    assert module_meta_id(mod) == "arena"


def test_malformed_yaml_does_not_hide_the_module(tmp_path: Path) -> None:
    """A parse error must not read as `enabled: false` — that would turn a typo
    into a silently absent feature."""
    mod = tmp_path / "broken"
    mod.mkdir()
    (mod / "module.yaml").write_text("id: [unclosed\n", encoding="utf-8")

    assert load_manifest(mod).enabled is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("false", False), ("no", False), ("yes", True)],
)
def test_enabled_accepts_the_hand_written_forms(
    tmp_path: Path, raw: object, expected: bool
) -> None:
    mod = _write(tmp_path / f"m{raw}", {"id": "m", "enabled": raw})

    assert load_manifest(mod).enabled is expected


def test_scenarios_dir_honours_the_declaration(tmp_path: Path) -> None:
    """The split that made this worth doing: the editor read `scenarios:` and
    the runtime hardcoded the name."""
    mod = _write(tmp_path / "custom", {"id": "custom", "scenarios": "flows"})

    assert load_manifest(mod).scenarios_dir == mod / "flows"


def test_unmodelled_keys_survive_in_raw(tmp_path: Path) -> None:
    mod = _write(tmp_path / "extra", {"id": "extra", "something_new": 7})

    assert ("something_new", 7) in load_manifest(mod).raw


def test_raw_projection_matches_the_file(tmp_path: Path) -> None:
    """`load_module_yaml` now projects from the shared parse; callers that want
    the untyped shape must see exactly what is on disk."""
    body = {"id": "proj", "title": "Proj", "wiki": False}
    mod = _write(tmp_path / "proj", body)

    assert load_module_yaml(mod) == body


def test_manifest_is_immutable(tmp_path: Path) -> None:
    mod = _write(tmp_path / "frozen", {"id": "frozen"})

    with pytest.raises(AttributeError):
        load_manifest(mod).id = "other"  # type: ignore[misc]


def test_cache_follows_edits(tmp_path: Path) -> None:
    """Keyed on (mtime_ns, size), so an edit invalidates without a manual clear."""
    mod = _write(tmp_path / "edited", {"id": "edited", "title": "before"})
    assert load_manifest(mod).title == "before"

    _write(tmp_path / "edited", {"id": "edited", "title": "after", "wiki": True})

    assert load_manifest(mod).title == "after"


def test_known_keys_cover_everything_the_repo_declares() -> None:
    """Every key any real manifest uses must be modelled or knowingly listed,
    or the unknown-key warning becomes noise people learn to ignore."""
    from config.paths import repo_root

    declared: set[str] = set()
    for path in (repo_root() / "games").rglob("module.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            declared.update(k for k in raw if isinstance(k, str))

    assert declared <= KNOWN_MANIFEST_KEYS, sorted(declared - KNOWN_MANIFEST_KEYS)


def test_model_is_slotted() -> None:
    """160 manifests live in memory for the process lifetime."""
    assert not hasattr(ModuleManifest(**_minimal()), "__dict__")


def _minimal() -> dict[str, object]:
    from pathlib import Path as _P

    return {
        "module_dir": _P(),
        "id": "x", "title": "", "description": "",
        "enabled": True, "wiki": True, "wiki_url": "",
        "references": "", "scenarios": "", "area": "", "analyze": "",
        "exec_path": "", "routes": "", "icon": "", "default_ref": "",
        "capture_interval_ms": None, "raw": (),
    }


# --- startup validation ----------------------------------------------------


def test_dead_declared_path_is_reported(tmp_path: Path, mocker) -> None:
    """The concrete debt: two manifests pointed at a file Phase 3 removed, and
    nothing noticed because `area:` has one consumer that silently defaults."""
    from config.startup_validation import _validate_module_manifests

    mod = _write(tmp_path / "games" / "wos" / "ghosty", {"id": "ghosty", "area": "../../nope.json"})
    mocker.patch("config.games.iter_module_catalogs", return_value=("wos",))
    mocker.patch("config.module_discovery.iter_module_dirs", return_value=[mod])
    issues: list[object] = []

    _validate_module_manifests(tmp_path, issues)

    assert [i for i in issues if "does not exist" in i.message]
    assert all(i.severity == "warning" for i in issues)


def test_unknown_key_is_reported_but_never_fatal(tmp_path: Path, mocker) -> None:
    """A build may legitimately meet a field it does not read yet; refusing to
    boot over that would be worse than the typo it catches."""
    from config.startup_validation import _validate_module_manifests

    mod = _write(tmp_path / "games" / "wos" / "typo", {"id": "typo", "scenariso": "x"})
    mocker.patch("config.games.iter_module_catalogs", return_value=("wos",))
    mocker.patch("config.module_discovery.iter_module_dirs", return_value=[mod])
    issues: list[object] = []

    _validate_module_manifests(tmp_path, issues)

    assert any("scenariso" in i.message for i in issues)
    assert all(i.severity == "warning" for i in issues)


def test_a_clean_manifest_reports_nothing(tmp_path: Path, mocker) -> None:
    from config.startup_validation import _validate_module_manifests

    mod = _write(tmp_path / "games" / "wos" / "clean", {"id": "clean", "title": "Clean"})
    (mod / "scenarios").mkdir()
    mocker.patch("config.games.iter_module_catalogs", return_value=("wos",))
    mocker.patch("config.module_discovery.iter_module_dirs", return_value=[mod])
    issues: list[object] = []

    _validate_module_manifests(tmp_path, issues)

    assert issues == []


def test_real_repo_has_no_dead_area_declarations() -> None:
    """Both `area: ../../../area.json` manifests are cleaned up."""
    from config.startup_validation import validate_startup_configs

    dead_area = [
        i for i in validate_startup_configs() if "`area:" in i.message
    ]

    assert dead_area == []
