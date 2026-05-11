"""Debug command: print top-N due-queue candidates with rank breakdown.

ADR 0001 §"Debug / operator tools": ``why did Y run before X?`` — emits the
same effective_priority breakdown ``pop_due`` would have used, without
popping. Safe to run against a live system.

Usage:
    uv run python -m scripts.queue_explain <instance_id> [--screen <name>] [-n 10]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _add_repo_to_path() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


async def _explain(instance_id: str, current_screen: str, n: int) -> int:
    import redis.asyncio as aioredis

    from scheduler.queue import RedisQueue

    url = os.environ.get("WOS_REDIS_URL", "redis://127.0.0.1:6379/0")
    client = aioredis.from_url(url, decode_responses=True)
    try:
        q = RedisQueue(client)
        rows = await q.explain_top_n(instance_id, current_screen=current_screen, n=n)
    finally:
        await client.aclose()

    if not rows:
        print(f"(no due candidates for instance={instance_id} screen={current_screen!r})")
        return 0

    print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    _add_repo_to_path()
    p = argparse.ArgumentParser(description="Print top-N due-queue rank breakdown.")
    p.add_argument("instance_id", help="e.g. bs1")
    p.add_argument("--screen", default="", help="current_screen (default: read from redis state)")
    p.add_argument("-n", type=int, default=10, help="how many candidates (default 10)")
    args = p.parse_args(argv)

    screen = args.screen
    if not screen:
        import redis as _r

        url = os.environ.get("WOS_REDIS_URL", "redis://127.0.0.1:6379/0")
        try:
            rc = _r.Redis.from_url(url, decode_responses=True)
            screen = rc.hget(f"wos:instance:{args.instance_id}:state", "current_screen") or ""
            rc.close()
        except Exception:
            screen = ""

    return asyncio.run(_explain(args.instance_id, screen, args.n))


if __name__ == "__main__":
    raise SystemExit(main())
