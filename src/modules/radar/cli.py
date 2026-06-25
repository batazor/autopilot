"""``radar`` CLI: scan / stitch.

Registered as a uv script (``uv run radar …``); see ``pyproject.toml``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from modules.radar.config import (
    DEFAULT_TARGET,
    RADAR_TARGETS,
    default_config_path,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Full kingdom map scan via a precomputed minimap tap grid.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="run the full grid scan")
    scan.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        choices=RADAR_TARGETS,
        help="which game view to scan (global_map / main_city / island). Selects the "
        "default --config and is recorded on the manifest so navigation / map tabs "
        "can filter by it. Default: global_map.",
    )
    scan.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config YAML (default: the committed config for --target)",
    )
    scan.add_argument("--out", type=Path, help="run directory (default runs/<YYYY-MM-DD>/)")
    scan.add_argument("--serial", help="override the configured ADB serial")
    scan.add_argument("--adb-bin", help="override the configured adb executable")
    scan.add_argument(
        "--resume",
        action="store_true",
        help="continue an existing run instead of starting fresh: re-anchor at "
        "the origin and re-walk, capturing only the missing cells. With --out it "
        "resumes that run; without --out it resumes the most recent run.",
    )

    st = sub.add_parser("stitch", help="assemble frames from a run directory into map_full.png")
    st.add_argument("run_dir", type=Path)
    return parser


def _latest_run_dir() -> Path | None:
    """Most recent run directory (one carrying a manifest) under the runs root."""
    from modules.radar.config import runs_root
    from modules.radar.manifest import MANIFEST_NAME

    root = runs_root()
    if not root.is_dir():
        return None
    cands = [d for d in root.iterdir() if d.is_dir() and (d / MANIFEST_NAME).is_file()]
    return max(cands, key=lambda d: d.stat().st_mtime) if cands else None


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "scan":
            from modules.radar.scanner import run_scan

            out = args.out
            if out is None and args.resume:
                out = _latest_run_dir()
                if out is None:
                    sys.stderr.write("radar: --resume found no existing run to continue\n")
                    sys.exit(1)
                sys.stdout.write(f"radar: resuming most recent run {out}\n")
            if out is None:
                out = Path("runs") / datetime.now(tz=UTC).strftime("%Y-%m-%d")
            config = args.config or default_config_path(args.target)
            run_scan(
                config, out, serial=args.serial, adb_bin=args.adb_bin, target=args.target
            )
        elif args.command == "stitch":
            from modules.radar.stitch import run_stitch

            run_stitch(args.run_dir)
    except (RuntimeError, ValueError, TypeError, FileNotFoundError) as exc:
        # Expected operational failures (stale calibration, no device, bad
        # args) — print the message without a traceback.
        sys.stderr.write(f"radar: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
