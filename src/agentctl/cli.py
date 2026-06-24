"""botctl — the command-line face of :mod:`agentctl.core`.

One subcommand per core function. Human-readable tables by default; ``--json``
on any command emits the raw core payload for machine/agent consumption.

    uv run botctl status
    uv run botctl state bs1
    uv run botctl queue bs1 --history
    uv run botctl trace bs1
    uv run botctl run check_main_city --inst bs1 --player 401227964
    uv run botctl pause bs1
    uv run botctl bot status

Reads are side-effect free; control commands enqueue work or send
pause/resume/abort/restart (device taps still pass through click-approval).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from agentctl import core
from agentctl.core import AgentctlError

if TYPE_CHECKING:
    from collections.abc import Callable


# --------------------------------------------------------------------------- #
# Tiny formatting helpers (no third-party deps)
# --------------------------------------------------------------------------- #
def _cell(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if v % 1 else str(int(v))
    return str(v)


def _table(rows: list[dict[str, Any]], cols: list[tuple[str, str]]) -> str:
    """Render ``rows`` as an aligned text table. ``cols`` is ``[(key, header)]``."""
    if not rows:
        return "(none)"
    headers = [h for _, h in cols]
    body = [[_cell(r.get(k)) for k, _ in cols] for r in rows]
    widths = [
        max(len(headers[i]), max((len(row[i]) for row in body), default=0))
        for i in range(len(cols))
    ]
    def line(parts: list[str]) -> str:
        return "  ".join(p.ljust(widths[i]) for i, p in enumerate(parts))

    out = [line(headers), line(["-" * w for w in widths])]
    out.extend(line(row) for row in body)
    return "\n".join(out)


def _kv(d: dict[str, Any], keys: list[tuple[str, str]]) -> str:
    """Render selected ``(key, label)`` pairs as ``label: value`` lines."""
    width = max((len(label) for _, label in keys), default=0)
    return "\n".join(f"{label.rjust(width)}: {_cell(d.get(k))}" for k, label in keys)


def _ts(v: Any) -> str:
    """Format a unix timestamp as local HH:MM:SS, passthrough on failure."""
    import datetime

    try:
        dt = datetime.datetime.fromtimestamp(float(v), datetime.UTC).astimezone()
        return dt.strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return _cell(v)


# --------------------------------------------------------------------------- #
# Per-command renderers (data dict -> printed text)
# --------------------------------------------------------------------------- #
def _render_status(d: dict[str, Any]) -> str:
    m = d.get("metrics", {})
    head = (
        f"instances={m.get('instances')}  live={m.get('live_workers')}  "
        f"busy={m.get('busy')}  paused={m.get('paused')}  "
        f"queue={m.get('queue')}  locks={m.get('locks')}"
    )
    fleet = d.get("fleet", [])
    for r in fleet:
        r["_paused"] = r.get("paused")
        r["_focus"] = r.get("focus") or "—"
    table = _table(
        fleet,
        [
            ("instance_id", "INSTANCE"),
            ("status", "STATUS"),
            ("node", "SCREEN"),
            ("active_player", "PLAYER"),
            ("task", "TASK"),
            ("_paused", "PAUSED"),
            ("_focus", "FOCUS"),
            ("alert", "ALERT"),
        ],
    )
    return f"{head}\n\n{table}"


def _render_state(d: dict[str, Any]) -> str:
    summary = _kv(
        d,
        [
            ("instance_id", "instance"),
            ("status", "status"),
            ("paused", "paused"),
            ("active_player", "player"),
            ("node", "screen"),
            ("task", "task"),
            ("queue_size", "queue"),
            ("nav_error", "nav_error"),
            ("test_module", "test_module"),
        ],
    )
    nxt = d.get("next_due")
    if nxt:
        summary += f"\n    next_due: {nxt.get('task_type')} @ {_ts(nxt.get('scheduled_at'))}"
    return summary


def _render_queue(d: dict[str, Any]) -> str:
    parts = [f"instance {d['instance_id']}  queue_size={d['queue_size']}"]
    running = d.get("running")
    if running:
        rtxt = (
            f"{running.get('task_type')} (player {running.get('player_id') or '—'}) "
            f"since {_ts(running.get('started_at'))}"
        )
    else:
        rtxt = "—"
    parts.append("running: " + rtxt)
    pend = []
    for r in d.get("pending", []):
        scn = (r.get("payload") or {}).get("dsl_scenario") or r.get("task_type")
        pend.append({**r, "_when": _ts(r.get("scheduled_at")), "_scn": scn})
    parts.append("\npending:")
    parts.append(
        _table(
            pend,
            [
                ("_when", "WHEN"),
                ("_scn", "SCENARIO"),
                ("player_id", "PLAYER"),
                ("priority", "PRIO"),
                ("task_id", "TASK_ID"),
            ],
        )
    )
    if "history" in d:
        parts.append("\nhistory:")
        parts.append(_render_history({"history": d["history"]}))
    return "\n".join(parts)


def _render_history(d: dict[str, Any]) -> str:
    rows = [
        {
            **h,
            "_when": _ts(h.get("started_at")),
            "_ok": h.get("success"),
            "_scn": h.get("scenario") or h.get("task_type"),
            "_detail": h.get("reason") or h.get("error") or "",
            "_dur": h.get("duration_s"),
        }
        for h in d.get("history", [])
    ]
    return _table(
        rows,
        [
            ("_when", "WHEN"),
            ("_ok", "OK"),
            ("_scn", "SCENARIO"),
            ("player_id", "PLAYER"),
            ("_dur", "DUR_S"),
            ("_detail", "DETAIL"),
        ],
    )


def _render_trace(d: dict[str, Any]) -> str:
    steps = d.get("steps", [])
    head = f"scenario={d.get('scenario') or '—'}  source={d.get('source')}  steps={len(steps)}"
    if not steps:
        return head + "\n(no trace available — run a scenario first)"
    rows = [
        {
            "i": s.get("i"),
            "status": s.get("status"),
            "summary": s.get("summary"),
            "region": s.get("region"),
            "score": s.get("match_score"),
            "reason": s.get("reason") or s.get("match_detail") or s.get("ocr_status") or "",
        }
        for s in steps
    ]
    return head + "\n" + _table(
        rows,
        [
            ("i", "#"),
            ("status", "STATUS"),
            ("summary", "STEP"),
            ("region", "REGION"),
            ("score", "SCORE"),
            ("reason", "REASON"),
        ],
    )


def _render_screenshot(d: dict[str, Any]) -> str:
    lines = [f"path: {d['path']}"]
    if d.get("exists"):
        lines.append(f"age: {d.get('age_s')}s   (Read this PNG to view the screen)")
    if d.get("captured"):
        lines.append("captured: fresh ADB screencap")
    if d.get("error"):
        lines.append(f"note: {d['error']}")
    return "\n".join(lines)


def _render_player(d: dict[str, Any]) -> str:
    flat = d.get("state", {})
    if not flat:
        return f"player {d['player_id']}: (no state, or no keys match the filter)"
    width = min(max((len(k) for k in flat), default=0), 50)
    lines = [f"player {d['player_id']}  ({len(flat)} keys)"]
    lines.extend(f"  {k.ljust(width)} = {_cell(v)}" for k, v in sorted(flat.items()))
    return "\n".join(lines)


def _render_scenarios(d: dict[str, Any]) -> str:
    return f"{d['count']} scenarios\n" + _table(
        d.get("scenarios", []),
        [("key", "KEY"), ("enabled", "ENABLED"), ("device_level", "DEVLVL"), ("steps", "STEPS"), ("source", "SOURCE")],
    )


def _render_devices(d: dict[str, Any]) -> str:
    note = "" if d.get("adb_online_known") else "  (adb unavailable — online unknown)"
    return _table(
        d.get("devices", []),
        [
            ("name", "NAME"),
            ("adb_serial", "SERIAL"),
            ("online", "ONLINE"),
            ("screenshot_backend", "SCREEN"),
            ("input_backend", "INPUT"),
        ],
    ) + note


def _render_logs(d: dict[str, Any]) -> str:
    if d.get("logfile") is None:
        return d.get("hint", "(no logs)")
    return f"# {d['logfile']}\n" + "\n".join(d.get("lines", []))


def _render_planners(d: dict[str, Any]) -> str:
    fid = d.get("fid") or "—"
    head = f"planners  (player {fid}, {d.get('fid_source')})  count={d.get('count')}"
    rows = []
    for p in d.get("planners", []):
        ld = p.get("last_decision") or {}
        blind = p.get("blind")
        rows.append(
            {
                **p,
                "_inputs": "—" if blind is None else ("BLIND" if blind else "ok"),
                "_last": (f"{ld.get('action')}: {ld.get('reason')}" if ld else "—"),
            }
        )
    table = _table(
        rows,
        [
            ("name", "PLANNER"),
            ("status", "STATUS"),
            ("_inputs", "INPUTS"),
            ("wired", "WIRED"),
            ("_last", "LAST DECISION"),
            ("note", "NOTE"),
        ],
    )
    return head + "\n" + table


def _render_why(d: dict[str, Any]) -> str:
    if d.get("running"):
        head = "▶ выполняется сейчас"
    elif d.get("from_history"):
        head = "⏹ ничего не выполняется — объясняю последнюю задачу"
    elif d.get("task_id"):
        head = "⏸ задача зафиксирована, но воркер не активен"
    else:
        head = "— на этом инстансе ещё не было задач"
    lines = [
        head,
        _kv(
            d,
            [
                ("instance_id", "instance"),
                ("running", "running"),
                ("scenario", "scenario"),
                ("player_id", "player"),
                ("priority", "priority"),
                ("task_id", "task_id"),
                ("region", "region"),
            ],
        )
    ]
    src = d.get("source") or {}
    lines.append(f"     source: {src.get('label')}  [{src.get('code')}]")
    if d.get("started_at"):
        lines.append(f"    started: {_ts(d.get('started_at'))}")
    rm = d.get("rank_meta")
    if rm:
        lines.append("  rank_meta: " + ", ".join(f"{k}={_cell(v)}" for k, v in rm.items()))
    else:
        lines.append("  rank_meta: — (worker не записал обоснование ранжирования)")
    decs = {k: v for k, v in (d.get("decisions") or {}).items() if v}
    if decs:
        lines.append(f"\nрешения планировщиков (player {d.get('decisions_player') or '—'}):")
        for dom, dec in decs.items():
            lines.append(
                f"  {dom.ljust(9)} {_cell(dec.get('action'))}  {_cell(dec.get('reason'))}"
                f"  → {dec.get('target') or '—'} @ {_ts(dec.get('ts'))}"
            )
    return "\n".join(lines)


def _render_drive(d: dict[str, Any]) -> str:
    head = (
        f"drive {d.get('scenario')} on {d.get('instance_id')}  "
        f"ok={_cell(d.get('ok'))}  completed={_cell(d.get('completed'))}  "
        f"({_cell(d.get('duration_s'))}s)"
    )
    if d.get("reason"):
        head += f"  reason={d['reason']}"
    if d.get("approval_bypassed"):
        head += "  [approval bypassed]"
    parts = [head, _render_trace({"scenario": d.get("scenario"), "source": "drive", "steps": d.get("steps", [])})]
    diff = d.get("state_diff") or {}
    if diff:
        parts.append("\nstate changes:")
        parts.extend(
            f"  {k}: {_cell(v.get('before'))} → {_cell(v.get('after'))}" for k, v in sorted(diff.items())
        )
    else:
        parts.append("\nstate changes: (none)")
    return "\n".join(parts)


def _render_ok(d: dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Command wiring
# --------------------------------------------------------------------------- #
def _emit(data: Any, renderer: Callable[[dict], str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(renderer(data) if isinstance(data, dict) else json.dumps(data, default=str))


def _add_inst(p: argparse.ArgumentParser) -> None:
    p.add_argument("instance", nargs="?", help="instance id (default: the only one, if unambiguous)")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit raw JSON")

    parser = argparse.ArgumentParser(prog="botctl", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="cmd", required=True)

    # reads
    sub.add_parser("status", parents=[common], help="fleet snapshot")

    p = sub.add_parser("state", parents=[common], help="full state for one instance")
    _add_inst(p)

    p = sub.add_parser("queue", parents=[common], help="pending + running for one instance")
    _add_inst(p)
    p.add_argument("--history", action="store_true", help="include recent history")
    p.add_argument("-n", "--limit", type=int, default=20)

    p = sub.add_parser("history", parents=[common], help="recent execution history")
    _add_inst(p)
    p.add_argument("-n", "--limit", type=int, default=20)

    p = sub.add_parser("trace", parents=[common], help="last scenario step trace")
    _add_inst(p)

    p = sub.add_parser("why", parents=[common], help="why the running task was chosen")
    _add_inst(p)

    p = sub.add_parser("planners", parents=[common], help="live status of every planner")
    p.add_argument("fid", nargs="?", help="player id (default: active player)")
    p.add_argument("--inst", dest="instance", help="instance to resolve the active player from")

    p = sub.add_parser("screenshot", parents=[common], help="path to current device preview PNG")
    _add_inst(p)
    p.add_argument("--fresh", action="store_true", help="capture a new ADB screencap first")

    p = sub.add_parser("player", parents=[common], help="per-account SQLite state (flattened)")
    p.add_argument("fid", help="player id")
    p.add_argument("key", nargs="?", help="optional dot-key filter (prefix match)")

    p = sub.add_parser("scenarios", parents=[common], help="list DSL scenarios")
    p.add_argument("--grep", help="filter by key/name substring")
    p.add_argument("--module", default="all", help="module scope (default: all)")

    sub.add_parser("devices", parents=[common], help="devices + backends + adb-online")

    p = sub.add_parser("logs", parents=[common], help="tail local worker logfile if present")
    p.add_argument("--inst", dest="instance", help="filter lines for this instance")
    p.add_argument("-n", "--limit", type=int, default=200)

    # control
    p = sub.add_parser("run", parents=[common], help="enqueue a scenario now")
    p.add_argument("scenario", help="scenario key")
    p.add_argument("--inst", dest="instance", help="instance id")
    p.add_argument("--player", default="", help="player id (required for account-level scenarios)")
    p.add_argument("--at", type=float, default=None, help="unix timestamp (default: now)")
    p.add_argument("--priority", type=int, default=50_000)
    p.add_argument("--replace", action="store_true", help="replace existing pending copies")
    p.add_argument("--abort-running", action="store_true", help="abort the in-flight task first")
    p.add_argument(
        "--focus",
        action="store_true",
        help="run ONLY this scenario (suppress crons/overlay/identity; start a worker if none)",
    )

    p = sub.add_parser(
        "drive", parents=[common],
        help="run ONE scenario on a device synchronously (in-process): step trace + state diff",
    )
    p.add_argument("scenario", help="scenario key")
    p.add_argument("--inst", dest="instance", help="instance id")
    p.add_argument("--player", default="", help="player id (account-level scenarios)")
    p.add_argument(
        "--no-approval", dest="approval", action="store_false",
        help="bypass click-approval for this run (taps fire without operator)",
    )
    p.add_argument("--timeout", type=float, default=180.0, help="abort after N seconds")

    p = sub.add_parser("focus", parents=[common], help="pin/clear focus mode for an instance")
    _add_inst(p)
    p.add_argument("--scenario", default="", help="scenario key to pin (omit with --clear)")
    p.add_argument("--player", default="", help="player id (account-level scenarios)")
    p.add_argument("--clear", action="store_true", help="clear focus → resume autopilot")
    p.add_argument(
        "--stop-worker",
        action="store_true",
        help="with --clear, also stop the isolated worker",
    )

    for name, helptext in (("pause", "pause an instance"), ("resume", "resume an instance")):
        p = sub.add_parser(name, parents=[common], help=helptext)
        _add_inst(p)

    p = sub.add_parser("abort", parents=[common], help="skip the in-flight task")
    _add_inst(p)
    p.add_argument("--restart", action="store_true", help="also restart the game")

    p = sub.add_parser("bot", parents=[common], help="local worker lifecycle")
    p.add_argument("action", choices=["status", "start", "stop"])

    p = sub.add_parser("queue-remove", parents=[common], help="remove a pending task")
    p.add_argument("task_id")

    p = sub.add_parser("queue-run-now", parents=[common], help="boost a pending task to run now")
    p.add_argument("task_id")

    p = sub.add_parser("queue-clear", parents=[common], help="clear pending tasks for an instance")
    _add_inst(p)
    p.add_argument("--all", dest="all_instances", action="store_true", help="clear the whole fleet")

    return parser


def _dispatch(args: argparse.Namespace) -> tuple[Any, Callable[[dict], str]]:
    cmd = args.cmd
    if cmd == "status":
        return core.status(), _render_status
    if cmd == "state":
        return core.instance_state(args.instance), _render_state
    if cmd == "queue":
        return core.queue(args.instance, with_history=args.history, limit=args.limit), _render_queue
    if cmd == "history":
        return core.history(args.instance, limit=args.limit), _render_history
    if cmd == "trace":
        return core.trace(args.instance), _render_trace
    if cmd == "why":
        return core.why(args.instance), _render_why
    if cmd == "planners":
        return core.planners(args.fid, instance=args.instance), _render_planners
    if cmd == "screenshot":
        return core.screenshot(args.instance, fresh=args.fresh), _render_screenshot
    if cmd == "player":
        return core.player(args.fid, args.key), _render_player
    if cmd == "scenarios":
        return core.scenarios(grep=args.grep, module_scope=args.module), _render_scenarios
    if cmd == "devices":
        return core.devices(), _render_devices
    if cmd == "logs":
        return core.logs(instance=args.instance, limit=args.limit), _render_logs
    if cmd == "run":
        return (
            core.run_scenario(
                args.scenario,
                args.instance,
                player_id=args.player,
                when=args.at,
                priority=args.priority,
                replace=args.replace,
                abort_running=args.abort_running,
                focus=args.focus,
            ),
            _render_ok,
        )
    if cmd == "drive":
        return (
            core.drive(
                args.scenario, args.instance,
                player_id=args.player, approval=args.approval, timeout=args.timeout,
            ),
            _render_drive,
        )
    if cmd == "focus":
        if args.clear:
            return (
                core.clear_focus(args.instance, stop_worker=args.stop_worker),
                _render_ok,
            )
        return (
            core.set_focus(args.scenario, args.instance, player_id=args.player),
            _render_ok,
        )
    if cmd == "pause":
        return core.pause(args.instance), _render_ok
    if cmd == "resume":
        return core.resume(args.instance), _render_ok
    if cmd == "abort":
        return core.abort(args.instance, restart=args.restart), _render_ok
    if cmd == "bot":
        return core.bot_lifecycle(args.action), _render_ok
    if cmd == "queue-remove":
        return core.queue_remove(args.task_id), _render_ok
    if cmd == "queue-run-now":
        return core.queue_run_now(args.task_id), _render_ok
    if cmd == "queue-clear":
        return core.queue_clear(args.instance, all_instances=args.all_instances), _render_ok
    msg = f"unknown command {cmd!r}"
    raise AgentctlError(msg)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data, renderer = _dispatch(args)
    except AgentctlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _emit(data, renderer, getattr(args, "json", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
