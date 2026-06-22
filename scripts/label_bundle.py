#!/usr/bin/env python3
"""Export / import portable screen-label bundles (``image + labels``) headless.

Bundles let the community share one annotated screen as a single ``.alabel.zip``
(see :mod:`api.services.labeling_bundle`). This CLI is a thin presenter over the same
service functions the ``/labeling`` UI uses, for when you have no browser.

Usage::

    uv run python scripts/label_bundle.py export <ref> --scope wos:heroes -o out.zip
    uv run python scripts/label_bundle.py import out.zip --scope wos:heroes [--game wos]

``export`` writes a bundle for the screen whose reference PNG is ``<ref>`` (repo-relative,
e.g. ``games/wos/heroes/heroes/references/page.heroes.png``).

``import`` stages the bundle's PNG under the target scope's ``references/temporal/`` and
prints the staged ref + regions; it does **not** write ``area.yaml`` — open the staged ref
in ``/labeling`` to review and Save (which regenerates crops).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from api.services import labeling_bundle as bundle_svc  # noqa: E402
from api.services.game_resolver import set_current_request_game  # noqa: E402
from config.games import default_game  # noqa: E402


def _cmd_export(args: argparse.Namespace) -> int:
    set_current_request_game(args.game or default_game())
    filename, data = bundle_svc.export_screen_bundle(args.ref, scope=args.scope)
    out = Path(args.output) if args.output else (REPO / filename)
    out.write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes)")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    set_current_request_game(args.game or default_game())
    content = Path(args.bundle).read_bytes()
    result = bundle_svc.import_screen_bundle(content, scope=args.scope)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(
        f"\nstaged {len(result['regions'])} region(s) at {result['ref']!r}; "
        "open it in /labeling to review and Save.",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", default=None, help="game id (default: configured default)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="pack one screen into an .alabel.zip")
    p_exp.add_argument("ref", help="repo-relative reference PNG path")
    p_exp.add_argument("--scope", default="core", help="labeling scope (e.g. wos:heroes)")
    p_exp.add_argument("-o", "--output", default=None, help="output path (default: <basename>.alabel.zip)")
    p_exp.set_defaults(func=_cmd_export)

    p_imp = sub.add_parser("import", help="stage a bundle into temporal/ for review")
    p_imp.add_argument("bundle", help="path to an .alabel.zip bundle")
    p_imp.add_argument("--scope", default="core", help="target labeling scope")
    p_imp.set_defaults(func=_cmd_import)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (bundle_svc.BundleError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
