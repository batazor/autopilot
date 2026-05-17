from __future__ import annotations

from pathlib import Path

from config.module_exec_registry import load_module_exec_handlers
from tasks.dsl_exec import DSL_EXEC_REGISTRY, build_dsl_exec_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gift_codes_module_exec_handlers_loaded() -> None:
    handlers = load_module_exec_handlers(_REPO_ROOT)
    assert "gift_code_scrape" in handlers
    assert "gift_code_redeem" in handlers
    assert callable(handlers["gift_code_scrape"])
    assert callable(handlers["gift_code_redeem"])


def test_dsl_exec_registry_includes_module_handlers() -> None:
    registry = build_dsl_exec_registry(_REPO_ROOT)
    assert "gift_code_scrape" in registry
    assert registry is not DSL_EXEC_REGISTRY or "gift_code_scrape" in DSL_EXEC_REGISTRY
