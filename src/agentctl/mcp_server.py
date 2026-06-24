"""botctl-mcp — the MCP-server face of :mod:`agentctl.core`.

Exposes the same reads and controls as the ``botctl`` CLI, but as native MCP
tools an agent can call directly (``bot_status``, ``bot_run``, …). Every tool is
a thin wrapper that delegates to a ``core`` function and returns its plain dict;
expected failures come back as ``{"error": "..."}`` rather than raising.

Run it over stdio::

    uv sync --extra agent     # installs the MCP SDK (only this face needs it)
    uv run botctl-mcp

Register it with Claude Code via ``.mcp.json`` at the repo root (see that file).
The ``mcp`` dependency is imported lazily in :func:`build_server`, so importing
this module (e.g. in tests) never requires the extra.
"""

from __future__ import annotations

from typing import Any

from agentctl import core
from agentctl.core import AgentctlError

SERVER_NAME = "autopilot-bot"


def _run(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Call a core function, converting expected failures into an error dict."""
    try:
        return fn(*args, **kwargs)
    except AgentctlError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # never crash the tool call on an unexpected bug
        return {"error": f"unexpected: {type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Tools — reads. Docstrings ARE the descriptions the agent sees; keep them tight.
# --------------------------------------------------------------------------- #
def bot_status() -> dict[str, Any]:
    """Fleet snapshot: per-instance status/screen/player/task/queue + counters."""
    return _run(core.status)


def bot_state(instance: str | None = None) -> dict[str, Any]:
    """Full state for one instance (status, player, screen, queue, next-due, history)."""
    return _run(core.instance_state, instance)


def bot_queue(instance: str | None = None, with_history: bool = False, limit: int = 20) -> dict[str, Any]:
    """Pending tasks + the running task (optionally recent history) for one instance."""
    return _run(core.queue, instance, with_history=with_history, limit=limit)


def bot_history(instance: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Recent task execution records (success/fail, reason, duration) for one instance."""
    return _run(core.history, instance, limit=limit)


def bot_trace(instance: str | None = None) -> dict[str, Any]:
    """Most recent scenario step-by-step trace (status/region/score/reason per step)."""
    return _run(core.trace, instance)


def bot_screenshot(instance: str | None = None, fresh: bool = False) -> dict[str, Any]:
    """Path to the current device-preview PNG (Read it to view the screen). fresh=capture now."""
    return _run(core.screenshot, instance, fresh=fresh)


def bot_why(instance: str | None = None) -> dict[str, Any]:
    """Explain the running task: scenario, who chose it (source), rank_meta, latest planner decisions."""
    return _run(core.why, instance)


def bot_planners(fid: str | None = None, instance: str | None = None) -> dict[str, Any]:
    """Live status of every planner: LIVE/DORMANT/CALC-ONLY, blind? (missing readers), last decision."""
    return _run(core.planners, fid, instance=instance)


def bot_player(fid: str, key: str | None = None) -> dict[str, Any]:
    """Per-account state from SQLite, flattened to dot-keys; optional key-prefix filter."""
    return _run(core.player, fid, key)


def bot_scenarios(grep: str | None = None, module_scope: str = "all") -> dict[str, Any]:
    """List DSL scenarios (key, name, enabled, device_level, source); grep filters by name/key."""
    return _run(core.scenarios, grep=grep, module_scope=module_scope)


def bot_devices() -> dict[str, Any]:
    """Configured devices + screenshot/input backends + ADB-online status."""
    return _run(core.devices)


def bot_logs(instance: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Tail the local worker logfile if one is configured (else a pointer to history/trace/Loki)."""
    return _run(core.logs, instance=instance, limit=limit)


# --------------------------------------------------------------------------- #
# Tools — control. These enqueue work / send commands; device taps still pass
# through the worker's click-approval gate.
# --------------------------------------------------------------------------- #
def bot_run(
    scenario: str,
    instance: str | None = None,
    player_id: str = "",
    when: float | None = None,
    priority: int = 50_000,
    replace: bool = False,
    abort_running: bool = False,
    focus: bool = False,
) -> dict[str, Any]:
    """Enqueue a scenario on an instance (default: now). player_id is required for account-level scenarios. focus=True runs ONLY this scenario (suppresses crons/overlay/identity, starts a worker if none)."""  # noqa: E501
    return _run(
        core.run_scenario,
        scenario,
        instance,
        player_id=player_id,
        when=when,
        priority=priority,
        replace=replace,
        abort_running=abort_running,
        focus=focus,
    )


def bot_focus(
    scenario: str = "",
    instance: str | None = None,
    player_id: str = "",
    clear: bool = False,
    stop_worker: bool = False,
) -> dict[str, Any]:
    """Pin an instance to run ONLY one scenario (focus mode), or clear=True to resume autopilot. With clear+stop_worker, also stop the isolated worker."""  # noqa: E501
    if clear:
        return _run(core.clear_focus, instance, stop_worker=stop_worker)
    return _run(core.set_focus, scenario, instance, player_id=player_id)


def bot_pause(instance: str | None = None) -> dict[str, Any]:
    """Pause an instance (the worker stops picking up tasks)."""
    return _run(core.pause, instance)


def bot_resume(instance: str | None = None) -> dict[str, Any]:
    """Resume a paused instance."""
    return _run(core.resume, instance)


def bot_abort(instance: str | None = None, restart: bool = False) -> dict[str, Any]:
    """Skip the in-flight task on an instance; restart=True also restarts the game."""
    return _run(core.abort, instance, restart=restart)


def bot_lifecycle(action: str) -> dict[str, Any]:
    """Local worker supervisor lifecycle: action is 'status', 'start', or 'stop'."""
    return _run(core.bot_lifecycle, action)


def bot_queue_remove(task_id: str) -> dict[str, Any]:
    """Remove a pending task by id."""
    return _run(core.queue_remove, task_id)


def bot_queue_run_now(task_id: str) -> dict[str, Any]:
    """Boost a pending task to run immediately."""
    return _run(core.queue_run_now, task_id)


def bot_queue_clear(instance: str | None = None, all_instances: bool = False) -> dict[str, Any]:
    """Clear pending tasks for one instance (default) or the whole fleet (all_instances=True)."""
    return _run(core.queue_clear, instance, all_instances=all_instances)


# The full tool set, registered in order on the server.
TOOLS = [
    bot_status,
    bot_state,
    bot_queue,
    bot_history,
    bot_trace,
    bot_screenshot,
    bot_why,
    bot_planners,
    bot_player,
    bot_scenarios,
    bot_devices,
    bot_logs,
    bot_run,
    bot_focus,
    bot_pause,
    bot_resume,
    bot_abort,
    bot_lifecycle,
    bot_queue_remove,
    bot_queue_run_now,
    bot_queue_clear,
]


def build_server() -> Any:
    """Construct a FastMCP server with every tool registered.

    Imports the MCP SDK lazily so this module imports without the ``agent``
    extra. Raises a clear :class:`AgentctlError` when the SDK is missing.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        msg = "the MCP SDK is not installed — run `uv sync --extra agent`"
        raise AgentctlError(msg) from exc

    server = FastMCP(SERVER_NAME)
    for fn in TOOLS:
        server.tool()(fn)
    return server


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``botctl-mcp``: run the stdio MCP server."""
    try:
        server = build_server()
    except AgentctlError as exc:
        import sys

        print(f"error: {exc}", file=sys.stderr)
        return 2
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
