"""Named handlers for DSL ``exec:`` steps (see :class:`tasks.dsl_scenario.DslScenarioTask`).

One handler module per feature; :mod:`tasks.dsl_exec.registry` assembles the
core registry and merges per-module ``exec.py`` contributions. Import the
public names from this package, not the submodules.
"""
from tasks.dsl_exec.context import DslExecContext, DslExecHandler
from tasks.dsl_exec.registry import DSL_EXEC_REGISTRY, build_dsl_exec_registry

__all__ = [
    "DSL_EXEC_REGISTRY",
    "DslExecContext",
    "DslExecHandler",
    "build_dsl_exec_registry",
]
