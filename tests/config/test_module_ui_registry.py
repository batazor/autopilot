from __future__ import annotations

from pathlib import Path

from config.module_ui_registry import (
    group_module_ui_page_specs,
    iter_module_ui_page_specs,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gift_codes_module_has_no_streamlit_ui_page() -> None:
    specs = iter_module_ui_page_specs(_REPO_ROOT)
    assert not any(s.module_id == "gift_codes" for s in specs)


def test_group_module_ui_pages_excludes_gift_codes_db() -> None:
    grouped = group_module_ui_page_specs(_REPO_ROOT)
    db_pages = grouped.get("DB", [])
    assert not any(s.module_id == "gift_codes" for s in db_pages)
