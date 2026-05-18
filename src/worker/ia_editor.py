"""`wos-ia-editor` entry point: approval UI without embedded bot workers."""

from __future__ import annotations

import os
import signal
import sys
import urllib.error
import urllib.request
from typing import Any

from config.paths import repo_root

_DEFAULT_UI_PORT = "8502"
_STOP_SIGNAL_COUNT = 0


def _streamlit_already_running(port: int, host: str = "127.0.0.1") -> bool:
    url = f"http://{host}:{port}/_stcore/health"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return int(getattr(resp, "status", 0) or 0) == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _wipe_queue_state_for_clean_testing() -> None:
    """Drop stale queue/running/throttle state on IA Editor startup.

    Cron-scheduled tasks (squad_fight, claim_exploration_rewards, …) accumulate
    in ``wos:queue:<inst>`` across sessions and can win pop or block preempt
    against the user's testing target (lower ``effective_priority`` gap than
    ``PREEMPT_MARGIN`` keeps the running cron from yielding). IA Editor is for
    interactive scenario testing — start each session with an empty queue and
    no in-flight task. Per-player state and operator toggles (analyzer scope,
    click_approval enabled) are preserved.
    """
    import logging

    import redis as _redis_sync

    from config.loader import load_settings

    logger = logging.getLogger(__name__)
    try:
        settings = load_settings()
        client = _redis_sync.Redis.from_url(settings.redis.url, decode_responses=True)
        client.ping()
    except Exception:
        logger.warning("IA Editor startup wipe: cannot reach Redis; skipping", exc_info=True)
        return

    # Keep ``current_task_player`` — operator wants the active-player binding
    # to survive a restart so player-bound scenarios still resolve their
    # ``${player_id}`` after the wipe.
    task_state_fields = (
        "current_scenario",
        "current_task_id",
        "current_task_type",
        "current_task_started_at",
        "current_task_region",
        "queue_blocked_reason",
        "nav_target",
        "nav_error",
        "state",
    )
    for inst in settings.instances:
        iid = inst.instance_id
        keys_to_del = [
            f"wos:queue:{iid}",
            f"wos:queue:running:{iid}",
            f"wos:ui:click_approval:current:{iid}",
        ]
        try:
            client.delete(*keys_to_del)
            client.hdel(f"wos:instance:{iid}:state", *task_state_fields)
        except Exception:
            logger.debug("IA Editor startup wipe: failed for %s", iid, exc_info=True)
            continue

    # Push throttles can also leak across sessions and silently suppress fresh
    # pushes. SCAN + DEL is cheap (single-digit keys typically).
    try:
        for pattern in ("wos:player:*:push_ttl:*", "wos:instance:*:push_ttl:*"):
            cursor = 0
            while True:
                cursor, batch = client.scan(cursor=cursor, match=pattern, count=200)
                if batch:
                    client.delete(*batch)
                if cursor == 0:
                    break
    except Exception:
        logger.debug("IA Editor startup wipe: push_ttl scan failed", exc_info=True)

    logger.info(
        "IA Editor startup: wiped queue/running/approvals + task state for %d instance(s)",
        len(settings.instances),
    )


def main() -> None:
    repo = repo_root()
    os.chdir(repo)
    port = os.environ.get("WOS_IA_EDITOR_PORT", _DEFAULT_UI_PORT)
    port_int = int(port)
    if (
        os.environ.get("WOS_FORCE_RESTART", "").strip().lower()
        not in ("1", "true", "yes", "on")
        and _streamlit_already_running(port_int)
    ):
        print(
            f"WOS IA editor already running at http://127.0.0.1:{port_int} "
            "(reuse that browser tab; set WOS_FORCE_RESTART=1 to start another).",
            flush=True,
        )
        return

    root = str(repo)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("STREAMLIT_SERVER_PROMPT", "false")
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    env["WOS_DISABLE_EMBEDDED_BOT"] = "1"
    sep = os.pathsep
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (sep + prev if prev else "")
    env["WOS_STREAMLIT_PORT"] = str(port_int)
    os.environ.update(env)
    if root not in sys.path:
        sys.path.insert(0, root)

    from config.runtime_bootstrap import bootstrap_runtime_observability
    from config.startup_validation import assert_startup_configs_valid

    bootstrap_runtime_observability("ia-editor")
    assert_startup_configs_valid(repo)
    _wipe_queue_state_for_clean_testing()
    from ui.ia_preview_service import ensure_ia_preview_refresher

    ensure_ia_preview_refresher()
    try:
        from streamlit.web import bootstrap
    except ImportError as exc:
        msg = "Streamlit is required: run `uv sync` (see README), then `uv run wos-ia-editor`."
        raise SystemExit(
            msg
        ) from exc

    def _set_up_signal_handler(server: Any) -> None:
        def signal_handler(signal_number: int, stack_frame: Any) -> None:
            del stack_frame
            global _STOP_SIGNAL_COUNT
            _STOP_SIGNAL_COUNT += 1
            if _STOP_SIGNAL_COUNT > 1:
                os._exit(128 + int(signal_number))
            server.stop()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        if sys.platform == "win32":
            signal.signal(signal.SIGBREAK, signal_handler)  # type: ignore[attr-defined]
        else:
            signal.signal(signal.SIGQUIT, signal_handler)

    # Streamlit installs its own SIGINT handler inside ``bootstrap.run``;
    # ours never gets a chance if we wait until after that returns (it's
    # blocking). Mirror the ``wos`` launcher (worker/launch.py:100) and
    # swap Streamlit's internal install hook before starting the server —
    # Streamlit ends up registering OUR handler, so the 2nd Ctrl+C
    # reliably ``os._exit`` instead of looping on "Stopping…".
    bootstrap._set_up_signal_handler = _set_up_signal_handler  # type: ignore[attr-defined]  # ty: ignore[invalid-assignment]
    app = repo / "src" / "ui" / "ia_editor_app.py"
    bootstrap.run(
        str(app),
        False,
        [],
        flag_options={"server.port": port_int},
        stop_immediately_for_testing=False,
    )


if __name__ == "__main__":
    main()
