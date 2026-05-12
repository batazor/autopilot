"""One-shot YAML override for ``DslScenarioTask`` debug runs.

The Debug Scenarios UI lets the operator edit a scenario's YAML in-place and
run that edited version *without* writing to the source file on disk. The
override is keyed by ``task_id`` and stored in Redis with a short TTL so it
self-cleans even if the worker never picks up the task. The worker reads it at
the start of :meth:`DslScenarioExecuteMixin.execute` and uses the parsed doc
instead of the on-disk scenario.

Scope is intentionally narrow:

* one override per ``task_id`` (cleared when the worker consumes it);
* not preserved across cooperative preempt → resume (the resumed slice gets a
  fresh ``task_id``) — debug runs use ``priority=DEBUG_PRIORITY_DEFAULT`` which
  outranks normal work, so in practice they finish in a single slice;
* sync helper for the Streamlit UI, async helper for the worker.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

OVERRIDE_KEY_PREFIX = "wos:debug:scenario_override:"
OVERRIDE_TTL_SECONDS = 60 * 60  # 1 hour — long enough for a queued debug run.


def override_key(task_id: str) -> str:
    return f"{OVERRIDE_KEY_PREFIX}{(task_id or '').strip()}"


def store_override_sync(client: Any, task_id: str, yaml_text: str) -> None:
    """Persist the override YAML string for ``task_id`` (sync redis client).

    No-op when ``task_id`` is empty or the client write fails — the worker will
    simply fall back to the on-disk file in that case.
    """
    tid = (task_id or "").strip()
    if not tid:
        return
    body = yaml_text if isinstance(yaml_text, str) else ""
    with suppress(Exception):
        client.set(override_key(tid), body, ex=OVERRIDE_TTL_SECONDS)


async def fetch_override(client: Any, task_id: str) -> str | None:
    """Read-and-delete the override YAML for ``task_id`` (async redis client).

    Returns the YAML text when set, ``None`` when missing or on any redis
    error. Uses GETDEL semantics (``get`` + ``delete``) so a re-enqueued task
    with the same id cannot accidentally reuse a stale override.
    """
    tid = (task_id or "").strip()
    if not tid or client is None:
        return None
    key = override_key(tid)
    try:
        raw = await client.get(key)
    except Exception:
        return None
    if raw is None:
        return None
    with suppress(Exception):
        await client.delete(key)
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(raw)
