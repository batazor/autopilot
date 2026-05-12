"""Parse Hero Recruitment HUD OCR from Redis and persist player fields.

The worker stores raw OCR for ``key.silver``, ``key.gold``, ``diamond``, and
``free_recruitments_today`` on ``wos:instance:<instance_id>:state`` while
``current_screen`` is ``hero.recrutment``. This script parses integers and writes:

- ``resources.silver_keys``, ``resources.gold_keys``, ``resources.diamond``
- ``events.recruitment.free_recruitments_today``

into ``db/state.yaml`` (via :mod:`config.state_store`) and mirrored string fields on
``wos:player:<player_id>:state``.

Usage::

    uv run python cmd/sync_hero_recruitment_resources.py --instance-id <adb_instance_id>

Requires Redis URL from ``config/settings.yaml`` (same as the worker).
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
import time
from pathlib import Path

import redis

from config.loader import load_settings
from config.state_store import get_state_store


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


_INT_RE = re.compile(r"[\d]+")


def parse_hud_count(text: str) -> int | None:
    """Best-effort integer from OCR (strips separators and non-digits)."""
    s = (text or "").strip()
    if not s:
        return None
    cleaned = s.replace(",", "").replace(" ", "").replace("\u00a0", "")
    digits = "".join(_INT_RE.findall(cleaned))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def sync_from_instance(*, r: redis.Redis, instance_id: str) -> int:
    """Returns exit code (0 ok, 1 error/missing data)."""
    inst_key = f"wos:instance:{instance_id}:state"
    player_id = str(r.hget(inst_key, "active_player") or "").strip()
    if not player_id:
        print(f"No active_player on {inst_key}", file=sys.stderr)
        return 1

    def _field(name: str) -> str:
        raw = r.hget(inst_key, name)
        return str(raw) if raw is not None else ""

    silver_t = _field("key.silver")
    gold_t = _field("key.gold")
    dia_t = _field("diamond")
    free_t = _field("free_recruitments_today")

    silver_v = parse_hud_count(silver_t)
    gold_v = parse_hud_count(gold_t)
    dia_v = parse_hud_count(dia_t)
    free_v = parse_hud_count(free_t)

    if silver_v is None and gold_v is None and dia_v is None and free_v is None:
        print(
            "No parseable integers for key.silver / key.gold / diamond / "
            f"free_recruitments_today. Texts: {silver_t!r} {gold_t!r} {dia_t!r} {free_t!r}",
            file=sys.stderr,
        )
        return 1

    flat: dict[str, object] = {}
    if silver_v is not None:
        flat["resources.silver_keys"] = silver_v
    if gold_v is not None:
        flat["resources.gold_keys"] = gold_v
    if dia_v is not None:
        flat["resources.diamond"] = dia_v
    if free_v is not None:
        flat["events.recruitment.free_recruitments_today"] = free_v

    store = get_state_store().get_or_create(player_id)
    store.update_from_flat(flat)

    mapping: dict[str, str] = {k: str(v) for k, v in flat.items()}
    mapping["resources.hero_recruitment_sync_at"] = str(time.time())
    pl_key = f"wos:player:{player_id}:state"
    r.hset(pl_key, mapping=mapping)
    print(f"Updated {player_id}: {flat}")
    return 0


def main() -> None:
    _repo_root()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--instance-id",
        required=True,
        help="ADB / worker instance id (Redis ``wos:instance:<id>:state``)",
    )
    args = p.parse_args()
    settings = load_settings()
    url = settings.redis.url
    r = redis.Redis.from_url(url, decode_responses=True)
    try:
        code = sync_from_instance(r=r, instance_id=str(args.instance_id).strip())
    finally:
        with contextlib.suppress(Exception):
            r.close()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
