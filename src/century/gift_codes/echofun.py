"""Echofun web automation for the RU «Белая мгла» build (``com.gof.globalru``).

The RU shard has no public gift-code API: the Echofun sites sign their own
requests in-page, so the signing key can't be extracted and plain HTTP calls
are impossible. Instead (mechanism ported from the ``wos-lolka`` Discord bot)
we automate the sites themselves with headless Playwright:

- https://giftcode.echofungames.com/ — the RU gift-code redemption form
  («ID игрока» + «Государство» + код). The image captcha is solved with
  ddddocr (:func:`century.captcha.solve_captcha_raw`).
- https://store.echofungames.com/wos/ — player lookup by FID (nickname,
  state, furnace level). Used to resolve the «Государство» form field and to
  validate external-account FIDs on the RU shard.

Playwright is imported lazily inside :meth:`EchofunBrowser.start`, so the
engine keeps working without the browser stack; callers catch
:class:`EchofunUnavailable` and skip web redemption with a log line.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from century.captcha import solve_captcha_raw
from century.gift_codes.models import RedeemStatus

logger = logging.getLogger(__name__)

GIFTCODE_URL = "https://giftcode.echofungames.com/"
STORE_URL = "https://store.echofungames.com/wos/"

_PLAYWRIGHT_HINT = (
    "playwright is required for RU web gift-code redemption — "
    "install deps (`uv sync`) and run `uv run playwright install chromium`"
)


class EchofunError(Exception):
    """Base error for Echofun web automation."""


class EchofunUnavailable(EchofunError):
    """Playwright (or its chromium build) is not installed/launchable."""


class PlayerNotFound(EchofunError):
    """The store site does not know this FID."""


# ── Redeem-result classification ────────────────────────────────────────────
#
# The site reports outcomes as Russian dialog phrases; map them (substring,
# case-insensitive) onto the shared RedeemStatus enum. Order matters: the
# player-not-found phrase contains «не найден», so it must precede the generic
# invalid-code pattern.
_PHRASES: list[tuple[re.Pattern[str], RedeemStatus]] = [
    (re.compile(r"заберите награды из игровой почты", re.IGNORECASE), RedeemStatus.SUCCESS),
    (re.compile(r"уже получены", re.IGNORECASE), RedeemStatus.ALREADY_RECEIVED),
    (re.compile(r"можно использовать только один раз", re.IGNORECASE), RedeemStatus.ALREADY_RECEIVED),
    (re.compile(r"ID игрока не найден", re.IGNORECASE), RedeemStatus.ROLE_NOT_FOUND),
    (re.compile(r"время действия кода истекло", re.IGNORECASE), RedeemStatus.CDK_EXPIRED),
    # Global claim cap exhausted — the code is dead for everyone, same handling
    # as an expired code.
    (re.compile(r"достигнут лимит", re.IGNORECASE), RedeemStatus.CDK_EXPIRED),
    (re.compile(r"не найден|ошибка кода активации", re.IGNORECASE), RedeemStatus.CDK_NOT_FOUND),
    (re.compile(r"требования не выполнены|уровень топки", re.IGNORECASE), RedeemStatus.STOVE_LEVEL_TOO_LOW),
    (re.compile(r"сервер (загружен|занят)", re.IGNORECASE), RedeemStatus.FAILED),
]

# Captcha rejection is not a redeem outcome — the submit is retried in-place.
_CAPTCHA_FAILED_RE = re.compile(r"[Нн]еверный код.*верификац", re.IGNORECASE)

_MAX_CAPTCHA_ATTEMPTS = 3


def classify(message: str) -> RedeemStatus | None:
    """Map a site dialog phrase to a :class:`RedeemStatus` (``None`` = unknown)."""
    for pattern, status in _PHRASES:
        if pattern.search(message):
            return status
    return None


@dataclass(frozen=True)
class EchofunPlayer:
    """Player card as shown by the Echofun store after an ID login."""

    fid: str
    nickname: str
    state: str
    furnace_level: int
    avatar: str | None = None


_ID_RE = re.compile(r"ID:\s*(\d+)")
_STATE_RE = re.compile(r"State:\s*#?\s*(\w+)")
_FURNACE_RE = re.compile(r"Furnace\s*Level:\s*(\d+)")
_NOT_FOUND_RE = re.compile(r"[Cc]annot find the character")


class EchofunBrowser:
    """One headless chromium shared across lookups/redeems; calls serialize.

    Each operation runs in a fresh browser context so a previous login never
    leaks into the next one.
    """

    def __init__(self, *, headless: bool = True, timeout_ms: int = 30000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise EchofunUnavailable(_PLAYWRIGHT_HINT) from exc
        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=self.headless)
        except Exception as exc:
            await self.close()
            msg = f"{_PLAYWRIGHT_HINT} ({exc})"
            raise EchofunUnavailable(msg) from exc

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("echofun: browser close failed", exc_info=True)
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                logger.debug("echofun: playwright stop failed", exc_info=True)
            self._pw = None

    async def __aenter__(self) -> EchofunBrowser:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ── Gift-code redemption ────────────────────────────────────────────

    async def redeem(self, fid: str, state: str, code: str) -> tuple[RedeemStatus, str]:
        """Submit one (player, code) pair; returns ``(status, site message)``."""
        if self._browser is None:
            msg = "EchofunBrowser is not started — call start()"
            raise EchofunError(msg)
        async with self._lock:
            context = await self._browser.new_context()
            context.set_default_timeout(self.timeout_ms)
            page = await context.new_page()
            try:
                return await self._redeem(page, str(fid), str(state), code)
            finally:
                await context.close()

    async def _redeem(self, page: Any, fid: str, state: str, code: str) -> tuple[RedeemStatus, str]:
        await page.goto(GIFTCODE_URL, wait_until="networkidle")

        await page.get_by_placeholder("ID игрока").fill(fid)
        await page.get_by_placeholder("Государство").fill(state)
        await page.get_by_placeholder(re.compile("подарочный код", re.IGNORECASE)).fill(code)

        captcha_attempts = 0
        await page.locator(".exchange_btn").first.click()

        # Poll: solve a captcha when one appears, otherwise read the result
        # dialog. A rejected captcha re-submits in place (bounded attempts).
        for _ in range(int(self.timeout_ms / 400)):
            if await self._try_solve_captcha(page):
                continue
            msg = await self._read_dialog(page)
            if msg:
                await self._dismiss(page)
                if _CAPTCHA_FAILED_RE.search(msg):
                    captcha_attempts += 1
                    if captcha_attempts >= _MAX_CAPTCHA_ATTEMPTS:
                        return RedeemStatus.FAILED, msg
                    await page.locator(".exchange_btn").first.click()
                    continue
                status = classify(msg)
                if status is None:
                    logger.warning("echofun redeem: unrecognised site reply: %r", msg)
                    return RedeemStatus.FAILED, msg
                return status, msg
            await asyncio.sleep(0.4)
        return RedeemStatus.FAILED, "timed out waiting for the site's reply"

    async def _try_solve_captcha(self, page: Any) -> bool:
        """Solve the inline image captcha if visible. True if it acted."""
        img = page.locator("img[src^='data:image']")
        try:
            if not (await img.count()) or not (await img.first.is_visible()):
                return False
        except Exception:
            return False
        src = await img.first.get_attribute("src")
        if not src or "," not in src:
            return False
        try:
            answer = solve_captcha_raw(src)
        except Exception:
            logger.debug("echofun: captcha solve failed", exc_info=True)
            return False
        if not answer:
            return False
        # The captcha input is the visible empty field next to the image.
        cap_input = page.locator("input:visible").last
        try:
            await cap_input.fill(answer, timeout=2000)
        except Exception:
            return False
        for sel in (".confirm_btn", ".exchange_btn"):
            btn = page.locator(sel)
            try:
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=2000)
                    break
            except Exception:
                logger.debug("echofun: captcha confirm click failed (%s)", sel, exc_info=True)
        await page.wait_for_timeout(600)
        return True

    async def _read_dialog(self, page: Any) -> str | None:
        for sel in (".dialog", ".popup", "[class*=dialog]", "[class*=popup]", "[class*=modal]"):
            loc = page.locator(sel)
            try:
                if await loc.count() and await loc.first.is_visible():
                    txt = (await loc.first.inner_text()).strip()
                    if txt and (classify(txt) is not None or _CAPTCHA_FAILED_RE.search(txt)):
                        return txt
                    if txt and len(txt) < 200:
                        return txt
            except Exception:
                logger.debug("echofun: dialog read failed (%s)", sel, exc_info=True)
        return None

    async def _dismiss(self, page: Any) -> None:
        for sel in (".confirm_btn", ".close", "[class*=close]"):
            btn = page.locator(sel)
            try:
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=1500)
                    return
            except Exception:
                logger.debug("echofun: dialog dismiss failed (%s)", sel, exc_info=True)

    # ── Player lookup (store) ───────────────────────────────────────────

    async def lookup_player(self, fid: str) -> EchofunPlayer:
        """Resolve a FID via the store's ID login; raises :class:`PlayerNotFound`."""
        if self._browser is None:
            msg = "EchofunBrowser is not started — call start()"
            raise EchofunError(msg)
        async with self._lock:
            context = await self._browser.new_context()
            context.set_default_timeout(self.timeout_ms)
            page = await context.new_page()
            try:
                return await self._lookup(page, str(fid))
            finally:
                await context.close()

    async def _lookup(self, page: Any, fid: str) -> EchofunPlayer:
        await page.goto(STORE_URL, wait_until="networkidle")

        # Close the cookie banner (its .mask intercepts clicks); prefer the
        # privacy-preserving choice.
        clicked = False
        for label in ("Reject Optional", "Reject", "Accept All"):
            el = page.get_by_text(re.compile(rf"^\s*{label}\s*$", re.IGNORECASE))
            try:
                if await el.count() and await el.first.is_visible():
                    await el.first.click(timeout=2000)
                    clicked = True
                    break
            except Exception:
                logger.debug("echofun store: cookie banner click failed", exc_info=True)
        if not clicked:
            try:
                await page.evaluate(
                    "document.querySelectorAll('.cookie-container,.mask').forEach(e=>e.remove())"
                )
            except Exception:
                logger.debug("echofun store: cookie mask removal failed", exc_info=True)
        await page.wait_for_timeout(300)

        await page.get_by_text(re.compile(r"^\s*Log ?in\b", re.IGNORECASE)).first.click()

        field = page.locator(".id-login-container input[placeholder='Enter User ID']").first
        await field.click()
        await field.fill(fid)

        agree = page.locator(
            "xpath=//*[contains(@class,'checkbox-text')][contains(.,'I have read and agree')]"
            "/preceding-sibling::*[contains(@class,'checkbox')][1]"
        )
        await agree.first.click()

        await page.locator(".applogin_loginbtn.button:not(.disabled)").first.click()

        # Wait for a card with real data (the .home-user-info node also exists
        # in the "Please log in first" state) OR the not-found error.
        error = page.get_by_text(_NOT_FOUND_RE)
        for _ in range(int(self.timeout_ms / 250)):
            if await error.count() and await error.first.is_visible():
                msg = f"player {fid} not found on the RU shard"
                raise PlayerNotFound(msg)
            txt = await page.locator(".home-user-info").first.inner_text()
            if _FURNACE_RE.search(txt) or _ID_RE.search(txt):
                return await self._parse_card(page, fid)
            await asyncio.sleep(0.25)
        msg = f"timed out waiting for the player card of {fid}"
        raise EchofunError(msg)

    async def _parse_card(self, page: Any, fid: str) -> EchofunPlayer:
        card = page.locator(".home-user-info").first
        text = await card.inner_text()

        name = ""
        for line in (raw.strip() for raw in text.splitlines()):
            if line and not line.startswith(("ID:", "State", "Furnace")):
                name = line
                break

        id_m = _ID_RE.search(text)
        st_m = _STATE_RE.search(text)
        fl_m = _FURNACE_RE.search(text)

        avatar = None
        img = card.locator("img")
        if await img.count():
            avatar = await img.first.get_attribute("src")

        return EchofunPlayer(
            fid=id_m.group(1) if id_m else fid,
            nickname=name,
            state=st_m.group(1) if st_m else "",
            furnace_level=int(fl_m.group(1)) if fl_m else 0,
            avatar=avatar,
        )


async def lookup_player_once(fid: str | int, *, timeout_ms: int = 30000) -> EchofunPlayer:
    """One-shot store lookup with its own short-lived browser.

    Convenience for callers that validate a single FID (external-account
    admission); the redeemer keeps a long-lived :class:`EchofunBrowser` instead.
    """
    async with EchofunBrowser(timeout_ms=timeout_ms) as browser:
        return await browser.lookup_player(str(fid))
