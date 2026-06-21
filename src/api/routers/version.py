"""Build version + new-version-available check via GitHub tags.

The bot repo is private, so its ``/releases`` endpoint isn't anonymously
readable. The public ``autopilot-page`` companion repo gets a semver tag
(``vX.Y.Z``) for each cut release, so we treat its latest tag as the current
available version and compare it against ``WOS_BUILD_VERSION`` baked into
this container at image build.

Result is cached in Redis with a long TTL so the GitHub API isn't hit on
every dashboard request (anonymous rate limit is 60/h per IP).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
from typing import Annotated, Any

import httpx
import redis
from fastapi import APIRouter, Depends

from api.deps import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/version", tags=["version"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]

# 1h cache keeps anonymous GitHub API usage to ~24 calls/day per API process.
_CACHE_TTL_S = 3600
_CACHE_KEY = "wos:version:remote"

# Override via env for forks / staging deployments.
_DEFAULT_REPO = "batazor/autopilot-page"

_HTTP_TIMEOUT_S = 5.0

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def _repo() -> str:
    return os.environ.get("WOS_VERSION_REPO", _DEFAULT_REPO)


def _current() -> dict[str, str]:
    """Build identity baked into this container at image build time."""
    return {
        "version": os.environ.get("WOS_BUILD_VERSION", "dev"),
        "revision": os.environ.get("WOS_BUILD_REVISION", ""),
    }


def _semver_tuple(tag: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(tag.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _is_newer(remote_tag: str, local_version: str) -> bool:
    """True when ``remote_tag`` is a strictly higher semver than ``local_version``.

    Falls back to plain string inequality if either side isn't a parseable
    semver — better to err on "no update" than to nag with false positives.
    """
    remote = _semver_tuple(remote_tag)
    local = _semver_tuple(local_version)
    if remote is None or local is None:
        return False
    return remote > local


def _fetch_remote() -> dict[str, Any] | None:
    """Pull the most recent semver tag from the public GitHub repo."""
    repo = _repo()
    url = f"https://api.github.com/repos/{repo}/tags"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_S) as client:
            resp = client.get(
                url,
                params={"per_page": 20},
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            tags = resp.json() or []
    except Exception as exc:
        logger.info("version: GitHub tags lookup failed: %s", exc)
        return None

    # GitHub returns tags by ref creation order; pick the highest semver to
    # ignore stray pre-release / rc tags that may not be the newest stable.
    best: tuple[tuple[int, int, int], str] | None = None
    for entry in tags:
        name = (entry.get("name") or "").strip()
        parsed = _semver_tuple(name)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, name)

    if best is None:
        return {"tag": "", "html_url": f"https://github.com/{repo}/tags"}

    return {
        "tag": best[1],
        "html_url": f"https://github.com/{repo}/releases/tag/{best[1]}",
    }


def _read_cache(client: redis.Redis) -> dict[str, Any] | None:
    try:
        raw = client.get(_CACHE_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _write_cache(client: redis.Redis, payload: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        client.setex(_CACHE_KEY, _CACHE_TTL_S, json.dumps(payload))


def _is_dev_build(current: dict[str, str]) -> bool:
    ver = current.get("version", "")
    return not ver or ver == "dev"


@router.get("")
def get_version(client: RedisDep) -> dict[str, Any]:
    current = _current()
    repo = _repo()
    now = int(time.time())

    if _is_dev_build(current):
        return {
            "current": current,
            "remote": None,
            "update_available": False,
            "repo": repo,
            "checked_at": now,
            "reason": "dev_build",
        }

    cached_remote = _read_cache(client)
    if cached_remote is not None:
        remote = cached_remote.get("remote")
        checked_at = int(cached_remote.get("checked_at", now))
    else:
        remote = _fetch_remote()
        if remote is None:
            return {
                "current": current,
                "remote": None,
                "update_available": False,
                "repo": repo,
                "checked_at": now,
                "reason": "github_unreachable",
            }
        checked_at = now
        _write_cache(client, {"remote": remote, "checked_at": now})

    update_available = _is_newer(remote.get("tag", ""), current.get("version", ""))
    return {
        "current": current,
        "remote": remote,
        "update_available": update_available,
        "repo": repo,
        "checked_at": checked_at,
        "reason": "ok",
    }
