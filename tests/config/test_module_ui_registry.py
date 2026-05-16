from __future__ import annotations

from pathlib import Path

from config.module_ui_registry import (
    group_module_ui_page_specs,
    iter_module_ui_page_specs,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gift_codes_module_ui_page_registered() -> None:
    specs = iter_module_ui_page_specs(_REPO_ROOT)
    gift = [s for s in specs if s.module_id == "gift_codes"]
    assert len(gift) == 1
    assert gift[0].title == "Gift codes"
    assert gift[0].nav_group == "DB"
    assert gift[0].url_path == "gift_codes"
    assert gift[0].path.is_file()
    assert gift[0].path.name == "page.py"


def test_group_module_ui_pages_db() -> None:
    grouped = group_module_ui_page_specs(_REPO_ROOT)
    assert "DB" in grouped
    assert any(s.module_id == "gift_codes" for s in grouped["DB"])
