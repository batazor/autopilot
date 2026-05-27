"""``uv run issue-license`` — mint a license file for a user.

Example:
    uv run issue-license --email alice@example.com \\
        --machine-id ABCD-EFGH-IJKL-MNOP \\
        --days 30 --tier pro --features heroes,mail,alliance \\
        --out ./alice.licence.jwt

Default output path is ``./<email-slug>.licence.jwt`` so multiple
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


def _parse_features(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _slug_for_email(sub: str) -> str:
    """``alice@example.com`` → ``alice-at-example-com``."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", sub.replace("@", "-at-")).strip("-").lower()
    return slug or "user"


def _default_out_path(sub: str) -> Path:
    return Path.cwd() / f"{_slug_for_email(sub)}.licence.jwt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="issue-license",
        description="Sign a license JWT and write it as a raw licence.jwt file.",
    )
    parser.add_argument("--email", "--sub", dest="sub", required=True, help="user identifier (email)")
    parser.add_argument(
        "--machine-id",
        default=None,
        help="fingerprint from the user's UI (omit when --trial is set; defaults to '*')",
    )
    parser.add_argument(
        "--trial",
        action="store_true",
        help="issue a host-agnostic trial token (machine_id='*' — accepted on any host)",
    )
    parser.add_argument("--days", type=int, default=30, help="validity in days (default: 30, max: 365)")
    parser.add_argument(
        "--tier",
        default=None,
        help="tier label (default: 'pro' for regular, 'trial' under --trial)",
    )
    parser.add_argument("--features", default="", help="comma-separated feature flags")
    parser.add_argument(
        "--max-devices",
        type=int,
        default=None,
        help="device cap (default: 1 for regular, 2 under --trial)",
    )
    parser.add_argument(
        "--max-players-per-device",
        type=int,
        default=None,
        help="player cap per device (default: 3 for regular, 3 under --trial)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output path (default: ./<email-slug>.licence.jwt)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="emit raw JWT to stdout instead of writing a file (legacy mode)",
    )
    parser.add_argument(
        "--json", action="store_true", help="dump the JWT payload (claims) as JSON for inspection — not the token itself",
    )
    args = parser.parse_args(argv)

    machine_id = args.machine_id
    if args.trial:
        machine_id = machine_id or "*"
        tier = args.tier or "trial"
        max_devices = args.max_devices if args.max_devices is not None else 2
        max_players_per_device = (
            args.max_players_per_device if args.max_players_per_device is not None else 3
        )
    else:
        if not machine_id:
            parser.error("--machine-id is required unless --trial is set")
        tier = args.tier or "pro"
        max_devices = args.max_devices if args.max_devices is not None else 1
        max_players_per_device = (
            args.max_players_per_device if args.max_players_per_device is not None else 3
        )

    try:
        token, payload = issue_license(
            sub=args.sub,
            machine_id=machine_id,
            days=args.days,
            tier=tier,
            features=_parse_features(args.features),
            max_devices=max_devices,
            max_players_per_device=max_players_per_device,
        )
    except (LicenseError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.stdout:
        print(token)
        return 0
    if args.json:
        # Legacy ``--json`` now prints the payload claims (read-only, for inspection
        # — the token itself is authoritative). Use ``--stdout`` for the bare JWT.
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    out_path = args.out or _default_out_path(args.sub)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(token + "\n", encoding="utf-8")
    print(f"wrote license file: {out_path}")
    print(f"  issued_to:           {payload['sub']}")
    print(f"  tier:                {payload['tier']}")
    print(f"  features:            {payload['features']}")
    print(f"  machine_id:          {payload['machine_id']}")
    print(f"  max_devices:         {payload['max_devices']}")
    print(f"  max_players/device:  {payload['max_players_per_device']}")
    print(f"  expires_at (unix):   {payload['exp']}")
    print()
    print("Send this file to the user. They import it via the UI (/license)")
    print("or place it at <repo>/license-data/licence.jwt and restart the bot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
