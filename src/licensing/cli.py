"""``uv run issue-license`` — mint a license file for a user.

Example:
    uv run issue-license --email alice@example.com \\
        --machine-id ABCD-EFGH-IJKL-MNOP \\
        --days 30 --tier pro --features heroes,mail,alliance \\
        --out ./alice.wos-license.json

Default output path is ``./<email-slug>.wos-license.json`` so multiple
issued files don't overwrite each other.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from licensing.issue import issue_license
from licensing.models import LicenseError
from licensing.storage import build_envelope, envelope_bytes


def _parse_features(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _slug_for_email(sub: str) -> str:
    """``alice@example.com`` → ``alice-at-example-com``."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", sub.replace("@", "-at-")).strip("-").lower()
    return slug or "user"


def _default_out_path(sub: str) -> Path:
    return Path.cwd() / f"{_slug_for_email(sub)}.wos-license.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="issue-license",
        description="Sign a license JWT and write it as a wos-license.json envelope.",
    )
    parser.add_argument("--email", "--sub", dest="sub", required=True, help="user identifier (email)")
    parser.add_argument("--machine-id", required=True, help="fingerprint from the user's UI")
    parser.add_argument("--days", type=int, default=30, help="validity in days (default: 30, max: 365)")
    parser.add_argument("--tier", default="pro", help="tier label (default: pro)")
    parser.add_argument("--features", default="", help="comma-separated feature flags")
    parser.add_argument("--max-devices", type=int, default=1, help="device cap (default: 1)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output path (default: ./<email-slug>.wos-license.json)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="emit raw JWT to stdout instead of writing a file (legacy mode)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit envelope JSON to stdout (skips file write)",
    )
    args = parser.parse_args(argv)

    try:
        token, payload = issue_license(
            sub=args.sub,
            machine_id=args.machine_id,
            days=args.days,
            tier=args.tier,
            features=_parse_features(args.features),
            max_devices=args.max_devices,
        )
    except (LicenseError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    envelope = build_envelope(token, payload)

    if args.stdout:
        print(token)
        return 0
    if args.json:
        print(json.dumps(envelope, indent=2))
        return 0

    out_path = args.out or _default_out_path(args.sub)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(envelope_bytes(envelope))
    print(f"wrote license file: {out_path}")
    print(f"  issued_to:  {envelope['issued_to']}")
    print(f"  tier:       {envelope['tier']}")
    print(f"  features:   {envelope['features']}")
    print(f"  machine_id: {envelope['machine_id']}")
    print(f"  expires_at: {envelope['expires_at']}")
    print()
    print("Send this file to the user. They import it via the UI (/license)")
    print("or place it at <repo>/license-data/wos-license.json and restart the bot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
