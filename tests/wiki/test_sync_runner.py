"""Wiki sync runner specs."""
from __future__ import annotations

import pytest

from wiki.sync_runner import SYNC_SCRIPT_SPECS, get_sync_spec


def test_get_sync_spec_known_keys() -> None:
    for key in ("buildings", "heroes", "items", "images", "balance_sheet"):
        spec = get_sync_spec(key)
        assert spec.key == key


def test_get_sync_spec_unknown() -> None:
    with pytest.raises(KeyError, match="unknown sync script"):
        get_sync_spec("nope")


def test_all_specs_have_existing_scripts() -> None:
    from config.paths import repo_root

    repo = repo_root()
    for spec in SYNC_SCRIPT_SPECS.values():
        path = repo / spec.script_rel
        assert path.is_file(), spec.script_rel
