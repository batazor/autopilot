"""Gift code redemption + auto-discovery CLI.

Usage:
    # Redeem all pending codes for all players:
    uv run cmd/gift_code.py

    # Also scrape wosrewards.com for new codes before redeeming:
    uv run cmd/gift_code.py --scrape

    # Only scrape (don't redeem):
    uv run cmd/gift_code.py --scrape-only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from gift.redeemer import run_gift_code_redeemer
from gift.scraper import poll_once


async def _main(args: argparse.Namespace) -> None:
    codes_path = Path(args.codes)
    devices_path = Path(args.devices)

    if args.scrape or args.scrape_only:
        new = await poll_once(codes_path)
        if new:
            print(f"Found {len(new)} new code(s): {', '.join(new)}")
        else:
            print("No new codes found on wosrewards.com")

    if not args.scrape_only:
        if not codes_path.exists():
            raise FileNotFoundError(f"Gift codes file not found: {codes_path}")
        if not devices_path.exists():
            raise FileNotFoundError(f"Devices file not found: {devices_path}")
        await run_gift_code_redeemer(codes_path, devices_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Redeem WOS gift codes via Century API")
    parser.add_argument("--codes", default="db/giftCodes.yaml")
    parser.add_argument("--devices", default="db/devices.yaml")
    parser.add_argument("--scrape", action="store_true", help="Scrape wosrewards.com before redeeming")
    parser.add_argument("--scrape-only", action="store_true", help="Only scrape, don't redeem")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
