#!/usr/bin/env python3
"""Standalone Dreamscape Memory runner — the quest without the bot around it.

Runs the module's ``dreamscape_memory_solve_loop`` exec handler directly against
one device: no worker, no scheduler, no queue, no Redis, no click-approval. The
solver itself is already self-contained — every Redis touchpoint in ``exec.py``
is ``None``-guarded and the clicked-word memory is in-process — so this script
only supplies what the worker normally would:

1. an instance to act on — a registered device (``--inst``, or the single
   configured one), or a raw ADB serial (``--serial``, synthesized on the fly);
2. localized OCR/overlay — the foreground package is probed and the game +
   module catalog bound before the first read, so the RU «Белая мгла» build
   gets ``rus`` OCR and the Russian item vocabulary, same as a worker boot;
3. taps that actually land — click-approval defaults ON with no Redis key to
   say otherwise, so a headless run must opt out explicitly (the same
   ``require_approval=False`` contract headless reader drives use).

Navigate to the level yourself (the scene picker / recall road screen); the
solver takes over once the word pills are on screen — it detects the room from
the words, so no scene needs to be selected. ``--scene`` force-activates one
for the rare round OCR can't identify (persists in scenes.db, like the UI).

    uv run python games/wos/events/dreamscape_memory/tools/solve.py            # single device
    uv run python games/wos/events/dreamscape_memory/tools/solve.py bs5 --mode multiplayer
    uv run python games/wos/events/dreamscape_memory/tools/solve.py --serial 127.0.0.1:5665

The device must be free: scrcpy is a single holder, so stop the worker (or run
``--backend adb``, slower capture but shareable) if one is attached.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import logging
import subprocess
import sys

logger = logging.getLogger("dreamscape_solve")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "instance",
        nargs="?",
        default="",
        help="registered instance id (optional when exactly one is configured)",
    )
    parser.add_argument(
        "--serial",
        default="",
        help="raw ADB serial instead of a registered instance (e.g. 127.0.0.1:5665)",
    )
    parser.add_argument(
        "--backend",
        default="",
        choices=["", "adb", "scrcpy"],
        help="capture/input backend for --serial (default: scrcpy)",
    )
    parser.add_argument("--mode", default="solo", choices=["solo", "multiplayer"])
    parser.add_argument("--ttl", default="5m", help="run budget (e.g. 5m, 90s)")
    parser.add_argument("--wait", default="300ms", help="pause between loop ticks")
    parser.add_argument("--tap-delay", default="0ms", help="pause between taps")
    parser.add_argument("--max-iterations", type=int, default=3000)
    parser.add_argument(
        "--scene",
        default="",
        help="force-activate a scene slug before running (persists in scenes.db)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args()


def _resolve_instance(args: argparse.Namespace):  # noqa: ANN202 — Settings is loaded lazily
    """``(settings, instance_id)`` — registered device or synthesized serial."""
    from config.loader import InstanceConfig, set_settings
    from services import get_settings  # auto-loads for standalone scripts

    settings = get_settings()
    if args.serial:
        inst = InstanceConfig(
            instance_id="standalone",
            bluestacks_window_title=args.serial,
            screenshot_backend=args.backend,
            input_backend=args.backend,
        )
        settings = dataclasses.replace(settings, instances=[inst])
        # Everything downstream (BotActions, OCR, area lookups) reads the
        # global settings — swap them so the synthesized device is *the* device.
        set_settings(settings)
        return settings, inst.instance_id
    ids = [i.instance_id for i in settings.instances]
    if args.instance:
        if args.instance not in ids:
            sys.exit(f"unknown instance {args.instance!r}; registered: {', '.join(ids) or '(none)'}")
        return settings, args.instance
    if len(ids) == 1:
        return settings, ids[0]
    sys.exit(f"pick an instance: {', '.join(ids) or '(none registered; use --serial)'}")


def _adb_connect(settings, serial: str) -> None:  # noqa: ANN001
    """Best-effort ``adb connect`` for tcp serials (BlueStacks drops the link)."""
    if ":" not in serial:
        return
    from adb.screencap import resolve_adb_executable

    adb_bin = resolve_adb_executable(settings.worker.adb_executable) or "adb"
    with contextlib.suppress(Exception):
        subprocess.run(
            [adb_bin, "connect", serial], capture_output=True, timeout=10, check=False
        )


def _bind_running_build(instance_id: str, serial: str) -> None:
    """Localize OCR + overlays to the build on the device (RU «Белая мгла» etc.).

    Mirrors the worker's boot-time package probe; best-effort — a probe failure
    leaves the default catalog.
    """
    try:
        from adb.controller import AdbController
        from config.games import default_game, game_for_package, module_catalog_for_package
        from services import bind_active_game, bind_active_module_catalog

        activity = AdbController(
            instance_id, serial, input_backend="adb"
        ).current_foreground_activity()
        pkg = activity.split("/", 1)[0].strip() if activity else ""
        game = game_for_package(pkg) or default_game()
        bind_active_game(game)
        if pkg:
            catalog = module_catalog_for_package(game, pkg)
            bind_active_module_catalog(catalog)
            logger.info("build: package=%s game=%s catalog=%s", pkg, game, catalog or "(default)")
    except Exception:
        logger.warning("could not probe the foreground package; using default catalog")


def _install_headless_actions(settings) -> None:  # noqa: ANN001
    """Route ``dsl_runtime.bot_actions()`` to a no-approval BotActions.

    Click-approval is Redis-backed and defaults ON when the key is missing — a
    standalone run has no Redis and no approver, so its taps must not gate.
    Installed via the documented factory seam on ``tasks.dsl_scenario``.
    """
    from adb import BotActions
    from tasks import dsl_scenario

    class _HeadlessActions(BotActions):
        def tap(self, instance_id, point, **kwargs):  # noqa: ANN001, ANN003, ANN202
            kwargs["require_approval"] = False
            return super().tap(instance_id, point, **kwargs)

    actions = _HeadlessActions(settings)
    dsl_scenario.BotActions = lambda: actions  # type: ignore[assignment]


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet the chatty infra so the solver's own narration stays readable.
    if not args.verbose:
        for noisy in ("adb", "ocr", "PIL", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    from config.paths import ensure_repo_on_sys_path

    ensure_repo_on_sys_path()

    settings, instance_id = _resolve_instance(args)
    serial = next(
        i.bluestacks_window_title for i in settings.instances if i.instance_id == instance_id
    )
    _adb_connect(settings, serial)
    _bind_running_build(instance_id, serial)
    _install_headless_actions(settings)

    if args.scene:
        from config import dreamscape_db

        if not dreamscape_db.set_active(args.scene):
            sys.exit(f"unknown scene {args.scene!r} — see scenes.db / the Guides tab")
        logger.info("active scene forced to %s", args.scene)

    from games.wos.events.dreamscape_memory.exec import DSL_EXEC_HANDLERS

    from tasks.dsl_exec.context import DslExecContext

    ctx = DslExecContext(
        redis_client=None,
        player_id="",
        instance_id=instance_id,
        args={
            "mode": args.mode,
            "ttl": args.ttl,
            "wait": args.wait,
            "tap_delay": args.tap_delay,
            "max_iterations": args.max_iterations,
        },
    )
    logger.info(
        "solving on %s (%s) mode=%s ttl=%s — navigate to the level; Ctrl-C stops",
        instance_id,
        serial,
        args.mode,
        args.ttl,
    )
    try:
        asyncio.run(DSL_EXEC_HANDLERS["dreamscape_memory_solve_loop"](ctx))
    except KeyboardInterrupt:
        logger.info("interrupted")
    except RuntimeError as exc:
        # Device offline / scrcpy start failure — the message says it all;
        # a traceback would only bury it.
        sys.exit(f"aborted: {exc}")
    finally:
        from adb.scrcpy import close_all_scrcpy_clients

        with contextlib.suppress(Exception):
            close_all_scrcpy_clients()

    print(json.dumps(ctx.result, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if ctx.result.get("ok", True) else 1)


if __name__ == "__main__":
    main()
