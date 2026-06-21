"""agentctl — a thin, text-first control & observability layer for the bot.

Two faces over one core:

* ``agentctl.cli``  — the ``botctl`` command line (human tables + ``--json``).
* ``agentctl.mcp_server`` — a stdio MCP server exposing the same reads/controls
  as native tools for an agent.

Both import :mod:`agentctl.core`, which is the single source of truth. ``core``
returns plain JSON-serialisable data and never prints — formatting lives in the
faces. Everything is an aggregation over functions that already exist in the
dashboard / API / worker layers; this package adds presentation, not behaviour.
"""

from agentctl.core import AgentctlError

__all__ = ["AgentctlError"]
