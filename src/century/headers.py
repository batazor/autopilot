"""Randomized browser-like headers for Century Game API calls.

Picks a Chrome/Brave/Edge profile (with matching ``sec-ch-ua`` /
``sec-ch-ua-platform`` / User-Agent) once per call. Default usage is one profile
per ``CenturyClient`` instance so we don't flip identity mid-session, which is
itself a tell.

Ported from the Kingshot Discord bot's ``cogs/browser_headers.py``.
"""
from __future__ import annotations

import random
from typing import TypedDict


class _Platform(TypedDict):
    os: str
    sec_platform: str


class _Profile(TypedDict):
    browser: str
    versions: list[int]
    platforms: list[_Platform]


_BROWSER_PROFILES: list[_Profile] = [
    {
        "browser": "Chrome",
        "versions": list(range(124, 136)),
        "platforms": [
            {"os": "Windows NT 10.0; Win64; x64", "sec_platform": '"Windows"'},
            {"os": "Windows NT 11.0; Win64; x64", "sec_platform": '"Windows"'},
            {"os": "Macintosh; Intel Mac OS X 10_15_7", "sec_platform": '"macOS"'},
            {"os": "X11; Linux x86_64", "sec_platform": '"Linux"'},
        ],
    },
    {
        "browser": "Brave",
        "versions": list(range(132, 146)),
        "platforms": [
            {"os": "Windows NT 10.0; Win64; x64", "sec_platform": '"Windows"'},
            {"os": "Windows NT 11.0; Win64; x64", "sec_platform": '"Windows"'},
            {"os": "Macintosh; Intel Mac OS X 10_15_7", "sec_platform": '"macOS"'},
        ],
    },
    {
        "browser": "Edge",
        "versions": list(range(124, 136)),
        "platforms": [
            {"os": "Windows NT 10.0; Win64; x64", "sec_platform": '"Windows"'},
            {"os": "Windows NT 11.0; Win64; x64", "sec_platform": '"Windows"'},
            {"os": "Macintosh; Intel Mac OS X 10_15_7", "sec_platform": '"macOS"'},
        ],
    },
]


def _build_sec_ua(browser: str, version: int) -> str:
    match browser:
        case "Chrome":
            return f'"Not:A-Brand";v="99", "Google Chrome";v="{version}", "Chromium";v="{version}"'
        case "Brave":
            return f'"Not:A-Brand";v="99", "Brave";v="{version}", "Chromium";v="{version}"'
        case "Edge":
            return f'"Not A(B)rand";v="8", "Chromium";v="{version}", "Microsoft Edge";v="{version}"'
        case _:
            return ""


def build_headers(origin: str | None = None, *, rng: random.Random | None = None) -> dict[str, str]:
    """Return a dict of HTTP headers with a randomized browser identity.

    ``origin`` adds matching ``origin`` + ``referer`` (Century APIs require both).
    Pass a seeded ``rng`` for deterministic output in tests.
    """
    r = rng or random
    profile = r.choice(_BROWSER_PROFILES)
    version = r.choice(profile["versions"])
    platform = r.choice(profile["platforms"])

    user_agent = (
        f"Mozilla/5.0 ({platform['os']}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36"
    )
    sec_ch_ua = _build_sec_ua(profile["browser"], version)

    headers: dict[str, str] = {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/x-www-form-urlencoded",
        "priority": "u=1, i",
        "user-agent": user_agent,
        "sec-ch-ua": sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform["sec_platform"],
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "sec-gpc": "1",
    }
    if origin:
        headers["origin"] = origin
        headers["referer"] = f"{origin}/"
    return headers
