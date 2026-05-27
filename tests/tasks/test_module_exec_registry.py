from __future__ import annotations

from pathlib import Path

from tasks.dsl_exec import DSL_EXEC_REGISTRY, build_dsl_exec_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gift_codes_handlers_loaded_via_core_registry() -> None:
    """Gift codes live in :mod:`century.gift_codes.exec` and are merged into
    the core registry directly — no per-module ``module.yaml`` discovery."""
    registry = build_dsl_exec_registry(_REPO_ROOT)
    for name in (
        "gift_code_scrape",
        "gift_code_redeem",
        "kingshot_gift_code_scrape",
        "kingshot_gift_code_redeem",
    ):
        assert name in registry, name
        assert callable(registry[name])


def test_dsl_exec_registry_singleton_has_gift_code_handlers() -> None:
    assert "gift_code_scrape" in DSL_EXEC_REGISTRY
    assert "kingshot_gift_code_scrape" in DSL_EXEC_REGISTRY
