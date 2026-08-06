"""WOS RU («Белая мгла») gift-code source + web redeemer.

The Russian re-skin (``com.gof.globalru``) runs on a separate Century shard
that the global gift-code API doesn't recognise, so codes can neither be
scraped from the global sources nor redeemed over the public API. Codes are
**entered manually** by the operator (from the RU community) and stored under
``game="wos_ru"``.

Redemption (mechanism ported from the ``wos-lolka`` Discord bot) goes through
the RU shard's own web form — https://giftcode.echofungames.com/ — automated
with headless Playwright (:mod:`century.gift_codes.echofun`). This covers
every known RU account in one pass: local device accounts bound to the RU
package plus external accounts (``game="wos_ru"``), no emulator involved.
The in-game applier (``games/wos/core/gift_codes/exec.py``) stays as a
fallback for the device player; codes redeemed here are stamped ``SUCCESS``
so it skips them.

The form needs the player's state («Государство»), which the registry doesn't
store — it's resolved per FID via the Echofun store card
(:meth:`EchofunBrowser.lookup_player`) and cached for the run.

``poll_once`` stays a no-op: there is no automatic RU code source.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from century.gift_codes.echofun import (
    EchofunBrowser,
    EchofunError,
    EchofunUnavailable,
    PlayerNotFound,
)
from century.gift_codes.models import RedeemStatus
from century.gift_codes.wos import (
    GiftRedeemResult,
    GiftRedeemSummary,
    _jittered,
)
from config.devices import load_devices
from config.games import GAMES, WOS_RU_MODULE_CATALOG
from config.giftcodes_db import (
    list_codes,
    list_external_gamers,
    set_redemption,
    set_redemption_bulk,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_GAME_ID = "wos_ru"

# Android package(s) that mark a local account as an RU-build account —
# derived from the game registry so a mapping change is picked up for free.
_RU_PACKAGES = frozenset(
    pkg
    for pkg, catalog in GAMES["wos"].package_catalogs
    if catalog == WOS_RU_MODULE_CATALOG
)

_INTER_PLAYER_DELAY = 2.0  # seconds between players on one code
_INTER_CODE_DELAY = 5.0    # seconds between codes


async def poll_once() -> list[str]:
    # No automatic source — RU codes are added by hand via the dashboard.
    return []


def _collect_players() -> tuple[list[str], dict[str, str]]:
    """Known RU accounts: local RU-package gamers + enabled externals.

    Returns ``(ordered player ids, player id → nickname)``.
    """
    player_ids: list[str] = []
    nicknames: dict[str, str] = {}

    registry = load_devices()
    for device in registry.devices:
        for gamer in device.all_gamers():
            if (gamer.game_package or "").strip() not in _RU_PACKAGES:
                continue
            pid = gamer.player_id
            if pid not in nicknames:
                player_ids.append(pid)
                nicknames[pid] = gamer.nickname or pid

    for ext in list_external_gamers(game=_GAME_ID, enabled_only=True):
        pid = str(ext.player_id)
        if pid not in nicknames:
            player_ids.append(pid)
            nicknames[pid] = ext.nickname or pid

    return player_ids, nicknames


class WebGiftCodeRedeemer:
    """Redeem pending RU codes through the Echofun web form."""

    def __init__(self) -> None:
        # fid → state number; None marks a FID the store doesn't know
        # (no role on the RU shard) so it isn't retried within the run.
        self._states: dict[str, str | None] = {}

    async def redeem_all(
        self,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> GiftRedeemSummary:
        player_ids, nicknames = _collect_players()
        return await self._run(player_ids, nicknames, progress_cb)

    async def redeem_for_player(
        self,
        fid: str | int,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> GiftRedeemSummary:
        pid = str(fid)
        _, nicknames = _collect_players()
        return await self._run(
            [pid], {pid: nicknames.get(pid, pid)}, progress_cb
        )

    # ------------------------------------------------------------------

    async def _run(
        self,
        player_ids: list[str],
        nicknames: dict[str, str],
        progress_cb: Callable[[int, int, str], None] | None,
    ) -> GiftRedeemSummary:
        summary = GiftRedeemSummary()
        codes = [
            code
            for code in list_codes(game=_GAME_ID)
            if not code.is_effectively_expired()
            and any(code.needs_redemption(pid) for pid in player_ids)
        ]
        total_work = sum(
            1
            for code in codes
            for pid in player_ids
            if code.needs_redemption(pid)
        )
        if progress_cb is not None:
            progress_cb(0, total_work, "starting")
        if not codes or not player_ids:
            return summary

        browser = EchofunBrowser()
        try:
            await browser.start()
        except EchofunUnavailable as exc:
            logger.warning("wos_ru redeem skipped: %s", exc)
            if progress_cb is not None:
                progress_cb(0, total_work, str(exc))
            return summary

        done = 0
        try:
            for code in codes:
                stop = False
                for pid in player_ids:
                    if not code.needs_redemption(pid):
                        continue

                    status, message = await self._redeem_one(browser, pid, code.name)
                    done += 1
                    if progress_cb is not None:
                        progress_cb(
                            done, total_work, f"{code.name} → {nicknames.get(pid, pid)}"
                        )

                    if status in (RedeemStatus.CDK_EXPIRED, RedeemStatus.CDK_NOT_FOUND):
                        # Code is globally dead — stamp every known player so
                        # future runs skip the whole code instantly.
                        set_redemption_bulk(code.name, player_ids, status, game=_GAME_ID)
                        for other in player_ids:
                            code.user_for[other] = status
                            summary.add(
                                GiftRedeemResult(
                                    code=code.name,
                                    player_id=other,
                                    nickname=nicknames.get(other, other),
                                    status=status,
                                    attempted=(other == pid),
                                    api_msg=message or None,
                                )
                            )
                        stop = True
                    else:
                        set_redemption(code.name, pid, status, game=_GAME_ID)
                        code.user_for[pid] = status
                        summary.add(
                            GiftRedeemResult(
                                code=code.name,
                                player_id=pid,
                                nickname=nicknames.get(pid, pid),
                                status=status,
                                attempted=True,
                                api_msg=message or None,
                            )
                        )

                    logger.info(
                        "wos_ru redeem %s → %s (%s): %s",
                        code.name, nicknames.get(pid, pid), pid, status.value,
                    )
                    if stop:
                        break
                    await asyncio.sleep(_jittered(_INTER_PLAYER_DELAY))
                await asyncio.sleep(_jittered(_INTER_CODE_DELAY))
        finally:
            await browser.close()
        return summary

    async def _redeem_one(
        self, browser: EchofunBrowser, pid: str, code_name: str
    ) -> tuple[RedeemStatus, str]:
        state = await self._state_for(browser, pid)
        if state is None:
            return RedeemStatus.ROLE_NOT_FOUND, "player not found on the RU shard"
        if not state:
            # Store lookup failed transiently — leave the slot PENDING-equivalent
            # (FAILED retries on the next run).
            return RedeemStatus.FAILED, "state lookup failed"
        try:
            return await browser.redeem(pid, state, code_name)
        except EchofunError as exc:
            logger.warning("wos_ru redeem failed fid=%s code=%s: %s", pid, code_name, exc)
            return RedeemStatus.FAILED, str(exc)
        except Exception:
            logger.exception("wos_ru redeem crashed fid=%s code=%s", pid, code_name)
            return RedeemStatus.FAILED, "unexpected redeem error"

    async def _state_for(self, browser: EchofunBrowser, pid: str) -> str | None:
        """Player's state number, ``None`` for a dead FID, ``""`` on lookup failure."""
        if pid in self._states:
            return self._states[pid]
        try:
            player = await browser.lookup_player(pid)
        except PlayerNotFound:
            logger.warning("wos_ru: fid=%s has no role on the RU shard — skipping", pid)
            self._states[pid] = None
            return None
        except EchofunError as exc:
            logger.warning("wos_ru: state lookup failed for fid=%s: %s", pid, exc)
            return ""  # transient — not cached, retried next code/run
        state = (player.state or "").strip()
        self._states[pid] = state
        return state


async def run_gift_code_redeemer(
    bot_instance_map: dict[str, str] | None = None,  # unused, kept for compat
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GiftRedeemSummary:
    del bot_instance_map
    return await WebGiftCodeRedeemer().redeem_all(progress_cb=progress_cb)


async def run_gift_code_redeemer_for_player(
    fid: str | int,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GiftRedeemSummary:
    """Redeem all pending RU codes for a single account (dashboard "Run now")."""
    return await WebGiftCodeRedeemer().redeem_for_player(fid, progress_cb=progress_cb)
