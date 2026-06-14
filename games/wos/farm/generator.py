"""Farm-account credential generator (R5 / owner-only).

Produces readable username / password pairs and persists them to the encrypted
``farm_accounts`` table in ``pending`` state. Registration itself is
human-in-the-loop: the operator solves the beta server's image-code / slider
captcha and submits; this module never bypasses that gate — it only mints and
stores the credentials.

Names are lowercase, readable nicknames with several soft fantasy-ish shapes
within the beta form's 6-15 character envelope. Farm nicknames intentionally
use letters only. Two modes:
  - **random** (default): cryptographically-random picks.
  - **deterministic**: derived from ``seed`` + index, so the same seed reproduces
    the same batch (recoverable without trusting only the DB).

Collisions against the DB (or within a batch) are retried with a fresh pick.
"""
from __future__ import annotations

import hashlib
import secrets
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import farm_accounts_db

if TYPE_CHECKING:
    from collections.abc import Callable

# Lowercase banks kept short so every template lands inside the beta form's
# 6-15 envelope without CamelCase, digits, or obvious "three words glued
# together" names.
STEMS = (
    "raven", "moss", "ember", "lumen", "niva", "aero", "sol", "mira",
    "luna", "kora", "sora", "tavi", "rova", "elva", "nori", "kaia",
    "zara", "faye", "lyra", "vexa", "orin", "sable", "cinder", "briar",
    "velvet", "willow", "hazel", "coral", "ivy", "onyx", "opal", "pearl",
)
NOUNS = (
    "vale", "haven", "grove", "field", "brook", "meadow", "harbor", "ridge",
    "garden", "river", "hollow", "glade", "bloom", "sprout", "drift", "trail",
    "cove", "dawn", "shade", "spark", "cloud", "stone", "branch", "leaf",
)
PREFIXES = (
    "mira", "niva", "sora", "luma", "kiri", "tala", "runa", "vella",
    "aura", "zuni", "elora", "fira", "maelis", "novi", "seren", "liri",
)
SUFFIXES = (
    "ly", "ia", "ora", "elle", "wyn", "kin", "lin", "via",
    "len", "ren", "ari", "elle", "ina", "iva", "wyn", "lyn",
)
COMPOUNDS = (
    "moonlit", "softmint", "snowberry", "starling", "mistvale", "rosewood",
    "sunberry", "foxglove", "wildfern", "dewdrop", "silkroad", "bluebell",
    "mossling", "nightrain", "goldleaf", "brightbay", "oakwhisper", "rivermint",
    "cloudberry", "amberfall", "pearlmist", "lilacwind", "ivorypine", "hazelrun",
)

_PASSWORD_ALPHABET = string.ascii_letters + string.digits  # beta form: no symbols
_PASSWORD_LEN = 14
_MIN_LEN, _MAX_LEN = 6, 15
_MAX_COLLISION_RETRIES = 40


@dataclass(frozen=True)
class GeneratedAccount:
    username: str
    email: str
    password: str


class _Picker:
    """Yields ints in ``[0, n)`` — random, or a deterministic SHA-256 stream."""

    def __init__(self, seed: str | None) -> None:
        self._seed = seed
        self._counter = 0

    def __call__(self, n: int) -> int:
        if self._seed is None:
            return secrets.randbelow(n)
        digest = hashlib.sha256(f"{self._seed}:{self._counter}".encode()).digest()
        self._counter += 1
        return int.from_bytes(digest[:8], "big") % n


def _choose(words: tuple[str, ...], pick: Callable[[int], int]) -> str:
    return words[pick(len(words))]


def _pretty_username(pick: Callable[[int], int]) -> str:
    templates = (
        lambda: _choose(COMPOUNDS, pick),
        lambda: _choose(STEMS, pick) + _choose(SUFFIXES, pick),
        lambda: _choose(PREFIXES, pick) + _choose(NOUNS, pick),
        lambda: _choose(STEMS, pick) + _choose(NOUNS, pick),
        lambda: _choose(NOUNS, pick) + _choose(SUFFIXES, pick),
        lambda: _choose(PREFIXES, pick) + _choose(SUFFIXES, pick),
    )
    return templates[pick(len(templates))]()


def _password(pick: Callable[[int], int]) -> str:
    return "".join(_PASSWORD_ALPHABET[pick(len(_PASSWORD_ALPHABET))] for _ in range(_PASSWORD_LEN))


def _email(username: str, domain: str) -> str:
    return f"{username.lower()}@{domain}"


def generate(
    count: int,
    *,
    seed: str | None = None,
    email_domain: str = "farm.local",
    exists: Callable[[str], bool] | None = None,
) -> list[GeneratedAccount]:
    """Generate ``count`` unique pretty accounts (not persisted).

    ``exists`` is a collision predicate (defaults to the DB). Raises
    ``ValueError`` if a unique username can't be found within the retry budget.
    """
    if count < 0:
        msg = "count must be >= 0"
        raise ValueError(msg)
    if exists is None:
        exists = farm_accounts_db.username_exists
    pick = _Picker(seed)
    seen: set[str] = set()
    out: list[GeneratedAccount] = []
    for _ in range(count):
        for _attempt in range(_MAX_COLLISION_RETRIES):
            username = _pretty_username(pick)
            if not (_MIN_LEN <= len(username) <= _MAX_LEN):
                continue
            if username in seen or exists(username):
                continue
            seen.add(username)
            out.append(GeneratedAccount(username, _email(username, email_domain), _password(pick)))
            break
        else:
            msg = f"could not generate a unique username in {_MAX_COLLISION_RETRIES} tries"
            raise ValueError(msg)
    return out


def generate_and_store(
    count: int,
    *,
    seed: str | None = None,
    email_domain: str = "farm.local",
    game: str = "wos",
    server: str = "wos_beta",
) -> list[farm_accounts_db.FarmAccount]:
    """Generate and persist ``count`` accounts in ``pending`` state."""
    drafts = generate(count, seed=seed, email_domain=email_domain)
    return [
        farm_accounts_db.add_account(
            d.username, password=d.password, email=d.email, game=game, server=server
        )
        for d in drafts
    ]


@dataclass(frozen=True)
class ClaimResult:
    account: farm_accounts_db.FarmAccount
    requested: str
    requested_taken: bool


def add_or_generate(
    desired: str | None = None,
    *,
    seed: str | None = None,
    game: str = "wos",
    server: str = "wos_beta",
    email_domain: str = "farm.local",
) -> ClaimResult:
    """Try to claim ``desired`` username; if it's already taken (or none given),
    mint a fresh pretty one instead.

    Returns the stored account plus whether the requested name was taken — so
    the caller can tell the operator "balabol was taken, here's FreshName".
    """
    want = str(desired or "").strip()
    if want and not (_MIN_LEN <= len(want) <= _MAX_LEN and want.isalpha()):
        msg = f"username {want!r} must be {_MIN_LEN}-{_MAX_LEN} letters"
        raise ValueError(msg)

    if want and not farm_accounts_db.username_exists(want, game=game):
        acct = farm_accounts_db.add_account(
            want, password=_password(_Picker(seed)), email=_email(want, email_domain),
            game=game, server=server,
        )
        return ClaimResult(account=acct, requested=want, requested_taken=False)

    fresh = generate_and_store(1, seed=seed, game=game, server=server, email_domain=email_domain)[0]
    return ClaimResult(account=fresh, requested=want, requested_taken=bool(want))
