"""ty baseline + ratchet check.

Usage:
  uv run python scripts/ty_ratchet.py            # check (CI gate)
  uv run python scripts/ty_ratchet.py --update   # write current counts as new baseline

The gate fails only if a file's diagnostic count went UP relative to the
baseline (.ty-baseline.json). Touching a file is fine; making it worse is not.
New files default to a baseline of 0, so every newly-introduced diagnostic on
new code is a regression.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / ".ty-baseline.json"

# ty diagnostics look like:
#
#   error[invalid-argument-type]: Argument is incorrect
#      --> src/foo.py:123:5            <- primary location (count this)
#       |
#   123 |  do_thing(bad)
#       |  ^^^^^^^^^^^^^
#   info: Matching overload defined here
#      --> stdlib/builtins.pyi:366:9   <- secondary info (don't count)
#
# We only count the FIRST "-->" line after an "error[...]"/"warning[...]"
# header — the rest are info/context locations that re-mention typeshed or
# upstream stubs and would inflate the per-file totals 2-3x.
_HEADER_RE = re.compile(r"^(error|warning)\[")
_LOC_RE = re.compile(r"^\s*-->\s+(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+)\s*$")


def run_ty() -> str:
    """Run ``ty check .`` from the repo root and return combined stdout/stderr."""
    proc = subprocess.run(
        ["uvx", "ty", "check", "--exit-zero", "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout + proc.stderr


def count_per_file(ty_output: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    awaiting_primary = False
    for line in ty_output.splitlines():
        if _HEADER_RE.match(line):
            awaiting_primary = True
            continue
        if not awaiting_primary:
            continue
        m = _LOC_RE.match(line)
        if not m:
            continue
        awaiting_primary = False  # already counted this diagnostic's primary
        path = m.group("path").strip()
        # Normalize to repo-relative POSIX paths so the baseline diffs
        # cleanly across operating systems / cwds.
        try:
            rel = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = path.replace("\\", "/")
        counts[rel] += 1
    return counts


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.is_file():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def write_baseline(counts: Counter[str]) -> None:
    payload = dict(sorted(counts.items()))
    BASELINE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Write current per-file diagnostic counts as the new baseline.",
    )
    args = parser.parse_args()

    output = run_ty()
    counts = count_per_file(output)

    if args.update:
        write_baseline(counts)
        total = sum(counts.values())
        print(f"ty baseline updated: {len(counts)} files, {total} diagnostics")
        return 0

    baseline = load_baseline()
    if not baseline:
        print("No baseline found. Run with --update to create one.", file=sys.stderr)
        return 2

    regressions: list[tuple[str, int, int]] = []
    for path, cur in sorted(counts.items()):
        base = baseline.get(path, 0)
        if cur > base:
            regressions.append((path, base, cur))

    improvements = sum(
        max(0, baseline.get(p, 0) - counts.get(p, 0))
        for p in set(baseline) | set(counts)
    )
    cur_total = sum(counts.values())
    base_total = sum(baseline.values())

    if regressions:
        print(f"ty ratchet: REGRESSED ({cur_total} vs baseline {base_total})", file=sys.stderr)
        for path, base, cur in regressions:
            print(f"  {path}: {base} -> {cur} (+{cur - base})", file=sys.stderr)
        print(
            "\nFix the new diagnostics, or if intentional, refresh the baseline with:\n"
            "  uv run python scripts/ty_ratchet.py --update",
            file=sys.stderr,
        )
        return 1

    if improvements:
        print(
            f"ty ratchet: OK ({cur_total} vs baseline {base_total}, "
            f"{improvements} fewer diagnostic{'s' if improvements != 1 else ''})"
        )
        print(
            "Tip: lock in the improvement by refreshing the baseline:\n"
            "  uv run python scripts/ty_ratchet.py --update"
        )
    else:
        print(f"ty ratchet: OK ({cur_total} diagnostics, matches baseline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
