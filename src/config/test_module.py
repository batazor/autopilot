"""Per-instance "test module" mode.

When ``wos:instance:<id>:state.test_module`` is set to a module id (e.g.
``heroes_feature``), the worker reads only tasks whose owning scenario lives
in that module and runs only that module's overlay analyzer rules. Other
queued tasks stay in Redis until the operator clears the selection
(``""`` / ``all``).

Infrastructure modules listed in :data:`INFRASTRUCTURE_MODULE_IDS` are always
allowed regardless of the selection — without them the game would get stuck
on unrecognized popups and block every test. The id matches what
``modules/<dir>/module.yaml`` declares (e.g. ``modules/core/popup/`` →
``popup``).
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from config.module_registry import ALL_MODULES_KEY, normalize_module_scope

if TYPE_CHECKING:
    from pathlib import Path

    import redis
    import redis.asyncio as aioredis


INFRASTRUCTURE_MODULE_IDS: frozenset[str] = frozenset(
    {
        # Dismisses unrecognized popups so the game doesn't freeze mid-test.
        "popup",
        # Reconnect / welcome-back handlers — without these the worker
        # cannot get past a network drop or the daily welcome dialog.
        "reconnect",
        "welcome_back",
        # NB: ``who_i_am`` (identity probe) is deliberately NOT here. It is
        # expensive (navigates to chief profile + OCR) and only needed for
        # account-level work. Device-level locks like ``alliance/helper`` (a
        # plain icon tap) don't need ``active_player``, so under a single-module
        # lock the identity probe is blocked along with everything else.
    }
)

_STATE_KEY_FMT = "wos:instance:{instance_id}:state"
_STATE_FIELD = "test_module"


def _normalize(module_id: str | None) -> str:
    raw = (module_id or "").strip()
    if not raw:
        return ""
    scope = normalize_module_scope(raw)
    return "" if scope == ALL_MODULES_KEY else scope


def is_module_allowed(test_module: str | None, candidate_module_id: str | None) -> bool:
    """Whether a task/rule belonging to ``candidate_module_id`` should run.

    Empty ``test_module`` → no filter (everything passes).
    Tasks not bound to any module (``candidate_module_id`` empty) pass through
    too: framework tasks like ``who_i_am`` or overlay-derived taps aren't
    module-scoped.
    """
    tm = _normalize(test_module)
    if not tm:
        return True
    cm = (candidate_module_id or "").strip()
    if not cm:
        return True
    if cm == tm:
        return True
    return cm in INFRASTRUCTURE_MODULE_IDS


def get_instance_test_module(client: redis.Redis, instance_id: str) -> str:
    """Sync read of ``wos:instance:<id>:state.test_module``. Empty string when unset."""
    iid = (instance_id or "").strip()
    if not iid:
        return ""
    try:
        raw = client.hget(_STATE_KEY_FMT.format(instance_id=iid), _STATE_FIELD)
    except Exception:
        return ""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode()
    return str(raw or "").strip()


def set_instance_test_module(
    client: redis.Redis, instance_id: str, module_id: str | None
) -> str:
    """Persist or clear ``test_module``. Returns the normalized value written."""
    iid = (instance_id or "").strip()
    if not iid:
        msg = "instance_id required"
        raise ValueError(msg)
    value = _normalize(module_id)
    client.hset(_STATE_KEY_FMT.format(instance_id=iid), _STATE_FIELD, value)
    return value


async def get_instance_test_module_async(
    redis_async: aioredis.Redis | None, instance_id: str
) -> str:
    """Async variant for worker code paths."""
    iid = (instance_id or "").strip()
    if redis_async is None or not iid:
        return ""
    try:
        raw = await redis_async.hget(_STATE_KEY_FMT.format(instance_id=iid), _STATE_FIELD)
    except Exception:
        return ""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode()
    return str(raw or "").strip()


def clear_test_module_cache() -> None:
    """Drop the scenario-key → module-id map.

    It resolves through the scenario tree, so it goes stale exactly when that
    tree changes — and it had no clear helper at all, which is why a module
    created at runtime stayed unresolvable until the process restarted.
    """
    _module_id_for_scenario_key_cached.cache_clear()


@lru_cache(maxsize=2048)
def _module_id_for_scenario_key_cached(repo_root_s: str, scenario_key: str) -> str | None:
    """Resolve a scenario_key to the owning module's id (lru-cached)."""
    from pathlib import Path

    from dsl import template_resolver as _tmpl
    from dsl.registry import scenario_roots

    resolved = _tmpl.resolve(Path(repo_root_s), scenario_key)
    if resolved is None:
        return None
    path_resolved = resolved.path.resolve()
    for root in scenario_roots(Path(repo_root_s)):
        root_resolved = root.path.resolve()
        try:
            path_resolved.relative_to(root_resolved)
        except ValueError:
            continue
        return root.module_id
    return None


def module_id_for_scenario_key(repo_root: Path, scenario_key: str) -> str | None:
    """Owning module id for ``scenario_key`` or ``None`` for unresolved/coreless keys."""
    key = (scenario_key or "").strip()
    if not key:
        return None
    return _module_id_for_scenario_key_cached(str(repo_root.resolve()), key)


def task_payload_allowed(
    payload: dict[str, Any],
    *,
    test_module: str | None,
    repo_root: Path,
) -> bool:
    """Whether a queue payload (`pop_due`/`_collect_ranked_due` row) should run."""
    tm = _normalize(test_module)
    if not tm:
        return True
    # Cron/planner rows carry their scenario in ``task_type``; only DSL rows
    # set ``dsl_scenario``. Checking ``dsl_scenario`` alone let every cron task
    # (which has none) slip through as if it were unbound framework work — so a
    # single-module lock did nothing to the crons. Fall back to ``task_type``.
    scenario_key = str(
        payload.get("dsl_scenario") or payload.get("task_type") or ""
    ).strip()
    if not scenario_key:
        # Genuinely unbound framework work (overlay-derived taps, …).
        return True
    # Infrastructure concerns must run even under a single-module lock, or the
    # bot gets stuck on popups / reconnects / loses its identity probe. Match by
    # task-type name: who_i_am resolves to the chief_profile module, not the
    # ``who_i_am`` infra id, so a resolved-module check alone would miss it.
    base = scenario_key.replace("/", ".").split(".", 1)[0]
    if scenario_key in INFRASTRUCTURE_MODULE_IDS or base in INFRASTRUCTURE_MODULE_IDS:
        return True
    module_id = module_id_for_scenario_key(repo_root, scenario_key)
    if module_id is None:
        # A real scenario/cron we can't map to a module: under an active lock,
        # default-deny instead of leaking it through.
        return False
    return is_module_allowed(tm, module_id)
