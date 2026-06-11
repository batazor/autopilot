"""``radar`` CLI: scan / stitch.

Registered as a uv script (``uv run radar …``); see ``pyproject.toml``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from modules.radar.config import default_config_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Full kingdom map scan via a precomputed minimap tap grid.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="run the full grid scan")
    scan.add_argument("--config", type=Path, default=default_config_path())
    scan.add_argument("--out", type=Path, help="run directory (default runs/<YYYY-MM-DD>/)")
    scan.add_argument("--serial", help="override the configured ADB serial")
    scan.add_argument("--adb-bin", help="override the configured adb executable")

    st = sub.add_parser("stitch", help="assemble frames from a run directory into map_full.png")
    st.add_argument("run_dir", type=Path)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "scan":
            from modules.radar.scanner import run_scan

            out = args.out or Path("runs") / datetime.now(tz=UTC).strftime("%Y-%m-%d")
            run_scan(args.config, out, serial=args.serial, adb_bin=args.adb_bin)
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
