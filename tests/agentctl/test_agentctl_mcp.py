"""Unit tests for :mod:`agentctl.mcp_server`.

The module must import without the optional ``mcp`` SDK; only ``build_server``
needs it. We assert the tool inventory, the error-swallowing wrapper, and —
when the SDK is present — that every tool registers on the server.
"""

from __future__ import annotations

import pytest

from agentctl import mcp_server


def test_tool_inventory() -> None:
    names = [f.__name__ for f in mcp_server.TOOLS]
    assert len(names) == 19
    assert len(set(names)) == 19  # no duplicates
    for expected in ("bot_status", "bot_run", "bot_focus", "bot_pause", "bot_trace", "bot_queue_clear"):
        assert expected in names


def test_every_tool_has_a_description() -> None:
    for f in mcp_server.TOOLS:
        assert (f.__doc__ or "").strip(), f"{f.__name__} is missing a docstring/description"


def test_run_wrapper_swallows_agentctl_error() -> None:
    def boom() -> dict[str, object]:
        msg = "nope"
        raise mcp_server.AgentctlError(msg)

    assert mcp_server._run(boom) == {"error": "nope"}


def test_run_wrapper_swallows_unexpected_error() -> None:
    def boom() -> dict[str, object]:
        msg = "kaboom"
        raise RuntimeError(msg)

    out = mcp_server._run(boom)
    assert "unexpected" in out["error"]
    assert "kaboom" in out["error"]


def test_run_wrapper_passes_through_success() -> None:
    assert mcp_server._run(lambda x: {"v": x}, 7) == {"v": 7}


def test_build_server_registers_all_tools() -> None:
    pytest.importorskip("mcp")
    import asyncio

    server = mcp_server.build_server()
    assert server.name == mcp_server.SERVER_NAME
    tools = asyncio.run(server.list_tools())
    assert len(tools) == len(mcp_server.TOOLS)
