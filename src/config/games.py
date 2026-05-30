"""Game registry, per-game directory layout, and canonical reference paths.

Phases 0–1 of the multi-game migration (see ``docs/multi-game-migration.md``).

This module is the single source of truth for:
- The set of known games (``GAMES``) — populated fully in Phase 2.
- The on-disk root that holds each game's modules (``modules_root_for``).
- The default game id used by call sites that have no instance context.
- The canonical shape of repo-relative reference paths
  (``split_repo_relative`` / ``is_module_reference``).

During Phases 0–2 the directory layout is unchanged: every game's modules still
live at ``<repo>/modules``. ``modules_root_for`` returns that path for any
``game`` argument so call sites can migrate to the helper API before Phase 3
physically moves the tree to ``<repo>/games/<game>/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.paths import repo_root as default_repo_root

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

GAMES_DIR_NAME = "games"
DEFAULT_GAME = "wos"


@dataclass(frozen=True)
class GameSpec:
    """Static metadata for one supported game.

    ``package`` is the canonical Android application id. ``package_aliases``
    are additional ids accepted as the same game, for example beta builds.
    ``launcher_activity`` is reserved for games where the default LAUNCHER
    intent doesn't open the right activity.
    """

    id: str
    label: str
    package: str
    package_aliases: tuple[str, ...] = ()
    launcher_activity: str | None = None

    @property
    def packages(self) -> tuple[str, ...]:
        """Canonical Android package followed by accepted aliases."""
        return (self.package, *self.package_aliases)


GAMES: dict[str, GameSpec] = {
    "wos": GameSpec(
        id="wos",
        label="Whiteout Survival",
        package="com.gof.global",
        package_aliases=("com.xyz.gof",),
    ),
    "kingshot": GameSpec(
        id="kingshot",
        label="Kingshot",
        package="com.run.tower.defense",
    ),
}


def spec_for_game(game: str) -> GameSpec:
    """Look up the :class:`GameSpec` for ``game``; raise ``KeyError`` if unknown."""
    g = (game or "").strip()
    if g not in GAMES:
        msg = f"unknown game id: {game!r} (known: {sorted(GAMES)})"
        raise KeyError(msg)
    return GAMES[g]


def package_for_game(game: str) -> str:
    """Android package id for ``game``."""
    return spec_for_game(game).package


def packages_for_game(game: str) -> tuple[str, ...]:
    """Android package ids that should be treated as ``game``.

    The first entry is the canonical package used as the default launch target;
    later entries are accepted aliases such as beta builds.
    """
    return spec_for_game(game).packages


def game_ids_for_packages(packages: Collection[str]) -> list[str]:
    """Known game ids represented by ``packages``, in registry order."""
    installed = {pkg.strip() for pkg in packages if pkg.strip()}
    return [
        gid
        for gid, spec in GAMES.items()
        if any(pkg in installed for pkg in spec.packages)
    ]


def matching_packages_for_game(game: str, packages: Collection[str]) -> tuple[str, ...]:
    """Installed package ids for ``game``, preserving registry package order."""
    installed = {pkg.strip() for pkg in packages if pkg.strip()}
    return tuple(pkg for pkg in packages_for_game(game) if pkg in installed)


def game_for_package(pkg: str) -> str | None:
    """Reverse lookup: game id whose package matches, or ``None``."""
    needle = (pkg or "").strip()
    if not needle:
        return None
    for spec in GAMES.values():
        if needle in spec.packages:
            return spec.id
    return None


def is_known_game(game: str) -> bool:
    """True if ``game`` is in the registry."""
    return (game or "").strip() in GAMES


def default_game() -> str:
    """Game id used when no instance/profile context is available."""
    return DEFAULT_GAME


def iter_games(repo_root: Path | None = None) -> tuple[str, ...]:
    """All known game ids, in registry order.

    ``repo_root`` is accepted for future filesystem-driven discovery but unused
    today — the registry is the source of truth.
    """
    _ = repo_root
    return tuple(GAMES.keys())


def games_root(repo_root: Path | None = None) -> Path:
    """Repo path that will hold ``<game>/`` subtrees after Phase 3.

    Today this resolves to ``<repo>/games``. The directory does not exist yet —
    callers should not assume it does. Use :func:`modules_root_for` to get the
    actual modules root, which currently points at ``<repo>/modules``.
    """
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    return root / GAMES_DIR_NAME


def modules_root_for(game: str, repo_root: Path | None = None) -> Path:
    """Directory that holds ``<game>``'s feature modules.

    Phase 3: returns ``<repo>/games/<game>``. The legacy ``<repo>/modules`` tree
    no longer exists — every game's modules live under their own root.
    """
    g = (game or DEFAULT_GAME).strip()
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    return root / GAMES_DIR_NAME / g


# Repo-relative path shape after Phase 3: ``games/<game>/<module-path>/...``.
# The historical ``modules/`` prefix is gone. Code that needs to construct or
# parse the prefix uses :func:`modules_path_prefix` so future moves stay local.
def modules_path_prefix(game: str | None = None) -> str:
    """Repo-relative prefix for ``game``'s module tree, e.g. ``games/wos``."""
    g = (game or DEFAULT_GAME).strip()
    return f"{GAMES_DIR_NAME}/{g}"


# Phase 0–2 callers still treat ``MODULES_DIR_NAME`` as the literal first
# segment of repo-relative paths. After Phase 3 there is no single such
# segment — the first two segments are ``games/<game>``. We keep the symbol
# as the default-game prefix so legacy call sites stay terse, and provide
# :func:`modules_path_prefix` for callers that need to pass an explicit game.
MODULES_DIR_NAME = modules_path_prefix(DEFAULT_GAME)


def is_module_reference(path: str) -> bool:
    """True iff ``path`` is a repo-relative reference under any game's modules tree.

    Recognizes the Phase 3+ shape ``games/<known-game>/<module>/<rest>``.
    Accepts both ``\\`` and ``/`` separators. Empty / parent-up paths are rejected.
    """
    raw = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not raw or raw.startswith("../") or "/../" in raw or raw == "..":
        return False
    parts = raw.split("/")
    if len(parts) < 3 or parts[0] != GAMES_DIR_NAME:
        return False
    return is_known_game(parts[1])


_MODULE_INTERNAL_DIRS = frozenset(
    {
        "references", "analyze", "scenarios", "routes", "tests",
        "wiki", "screen_verify.yaml", "area.yaml", "area.yml", "area.json",
        "module.yaml", "__init__.py", "__pycache__", "db",
    }
)


def split_repo_relative(path: str) -> tuple[str, str] | None:
    """Split a repo-relative reference path into ``(module_id, rest)``.

    ``games/wos/core/heroes/references/x.png`` →
    ``("core/heroes", "references/x.png")``. Returns ``None`` when the path
    isn't under any known game's modules tree or when the module-id segment
    is missing.

    The game id is dropped from the result — use :func:`split_game_module`
    when you need the game segment explicitly.
    """
    split = split_game_module(path)
    if split is None:
        return None
    _, module_id, rest = split
    return (module_id, rest)


def split_game_module(path: str) -> tuple[str, str, str] | None:
    """Split a repo-relative reference path into ``(game, module_id, rest)``.

    ``games/wos/core/heroes/references/x.png`` →
    ``("wos", "core/heroes", "references/x.png")``. Returns ``None`` for paths
    outside the modules tree.
    """
    raw = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        return None
    parts = raw.split("/")
    if len(parts) < 3 or parts[0] != GAMES_DIR_NAME:
        return None
    game = parts[1]
    if not is_known_game(game):
        return None
    tail_parts = parts[2:]
    for i, segment in enumerate(tail_parts):
        if segment in _MODULE_INTERNAL_DIRS:
            if i == 0:
                return None  # ``games/wos/references/...`` — no module id
            return (game, "/".join(tail_parts[:i]), "/".join(tail_parts[i:]))
    # No internal-dir marker — treat the whole tail as module id, with no rest.
    return (game, "/".join(tail_parts), "") if tail_parts else None
