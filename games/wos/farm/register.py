"""Human-in-the-loop registration for the WOS beta web client (R5 / owner-only).

Playwright opens the beta client and fills the generated username / password /
confirm fields. The signup form is gated by an image-code + slider captcha —
**the operator solves that and clicks Sign Up**; this module never solves or
bypasses it. After the operator signals "done", we read whether the modal
closed (success heuristic) and stamp the account's status in the DB.

Run (owner machine, after `uv sync --extra farm` + `playwright install chromium`):

    uv run python -m games.wos.farm.register --username balabol
    uv run python -m games.wos.farm.register --seed batch-1   # auto pretty name

The form fields were confirmed live (2026-06-14): the "Sign Up" CTA opens a
``register-view`` modal with ``input[name=account|password|repassword]``, an
"Enter image code" text input, and a ``slider-captcha`` block.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import TYPE_CHECKING, Any

from games.wos.farm import generator

from config import farm_accounts_db

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

BETA_URL = "https://h5-res.wzqqe.com/land/index.html"

SEL_OPEN_SIGNUP = "button.cta-btn--secondary"
SEL_ACCOUNT = "input[name=account]"
SEL_PASSWORD = "input[name=password]"
SEL_REPASSWORD = "input[name=repassword]"
SEL_REGISTER_VIEW = ".register-view"

_RESULT_POLL_TRIES = 20
_RESULT_POLL_INTERVAL_MS = 500


async def _registration_succeeded(page: Any) -> bool:
    """Heuristic: the ``register-view`` modal disappears once Sign Up succeeds.

    Polls briefly so a slow server response still resolves. Best-effort — the
    caller can override the status if the operator reports otherwise.
    """
    for _ in range(_RESULT_POLL_TRIES):
        if not await page.is_visible(SEL_REGISTER_VIEW):
            return True
        await page.wait_for_timeout(_RESULT_POLL_INTERVAL_MS)
    return False


async def drive_registration(
    page: Any,
    account: farm_accounts_db.FarmAccount,
    on_ready_for_human: Callable[[], Awaitable[bool | None]],
    *,
    url: str = BETA_URL,
) -> bool:
    """Fill the signup form, hand off to the human for captcha+submit, report result.

    ``on_ready_for_human`` is awaited *after* the fields are filled and must
    block until the operator has solved the captcha and clicked Sign Up. If it
    returns a bool that is the authoritative outcome (the operator told us
    Done/Failed); if it returns ``None`` we fall back to the modal-closed
    heuristic. Kept injectable so the flow is unit-testable without a browser.
    """
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.click(SEL_OPEN_SIGNUP)
    await page.wait_for_timeout(1000)
    await page.fill(SEL_ACCOUNT, account.username)
    await page.fill(SEL_PASSWORD, account.password)
    await page.fill(SEL_REPASSWORD, account.password)
    logger.info("farm: filled signup for %s — awaiting human captcha+submit", account.username)
    outcome = await on_ready_for_human()
    if outcome is not None:
        return outcome
    return await _registration_succeeded(page)


def _require_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - environment guard
        msg = (
            "playwright is not installed. Run `uv sync --extra farm` then "
            "`uv run playwright install chromium`."
        )
        raise RuntimeError(msg) from exc
    return async_playwright


def register_account(
    account: farm_accounts_db.FarmAccount,
    *,
    done: Callable[[], Awaitable[bool | None]],
    headless: bool = False,
    url: str = BETA_URL,
) -> bool:
    """Launch a (headed by default) browser and run the registration flow."""
    async_playwright = _require_playwright()

    async def _run() -> bool:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            ctx = await browser.new_context(**p.devices["iPhone 13"])
            page = await ctx.new_page()
            try:
                return await drive_registration(page, account, done, url=url)
            finally:
                await browser.close()

    return asyncio.run(_run())


async def console_done(account: farm_accounts_db.FarmAccount) -> None:
    """Block on a console ENTER while the operator solves the captcha + submits.

    Returns ``None`` → outcome falls back to the modal-closed heuristic.
    """
    prompt = (
        f"\n>>> Поля заполнены для '{account.username}'. В открытом окне реши "
        f"image-code + слайдер и нажми Sign Up, затем нажми ENTER здесь… "
    )
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input, prompt)


async def ui_done(
    account: farm_accounts_db.FarmAccount,
    *,
    poll_interval_s: float = 1.0,
    timeout_s: float = 900.0,
) -> bool | None:
    """Wait for the dashboard **Done**/**Failed** button instead of the console.

    Publishes the pending registration to Redis, then polls for the operator's
    verdict. Returns ``True`` (Done), ``False`` (Failed), or ``None`` on timeout
    (caller falls back to the modal heuristic).
    """
    from api.deps import get_redis
    from dashboard import farm_handoff

    client = get_redis()
    farm_handoff.set_pending(client, account.username)
    loop = asyncio.get_event_loop()
    deadline = asyncio.get_event_loop().time() + timeout_s
    try:
        while loop.time() < deadline:
            sig = await loop.run_in_executor(
                None, farm_handoff.read_signal, client, account.username
            )
            if sig:
                return sig == "done"
            await asyncio.sleep(poll_interval_s)
        return None
    finally:
        farm_handoff.clear_pending(client, account.username)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="farm-register",
        description="Generate a farm account and register it on the WOS beta "
        "(you solve the captcha; Playwright only fills the form).",
    )
    parser.add_argument("--username", help="desired username; falls back to a pretty name if taken")
    parser.add_argument("--seed", help="deterministic seed for the generated name/password")
    parser.add_argument("--server", default="wos_beta")
    parser.add_argument("--headless", action="store_true", help="run without a visible window (debug)")
    parser.add_argument(
        "--ui",
        action="store_true",
        help="wait for the dashboard Done/Failed button instead of console ENTER",
    )
    args = parser.parse_args(argv)

    claim = generator.add_or_generate(args.username, seed=args.seed, server=args.server)
    acct = claim.account
    if claim.requested_taken:
        print(f"'{claim.requested}' занят — использую сгенерированное имя: {acct.username}")
    print(f"Аккаунт: {acct.username}  пароль: {acct.password}  (статус: {acct.status})")
    if args.ui:
        print("Жду кнопку Done/Failed в дашборде (/farm)…")

    done = (lambda: ui_done(acct)) if args.ui else (lambda: console_done(acct))
    ok = register_account(acct, done=done, headless=args.headless)
    farm_accounts_db.set_status(
        acct.username,
        farm_accounts_db.STATUS_REGISTERED if ok else farm_accounts_db.STATUS_FAILED,
    )
    print("✅ зарегистрирован" if ok else "⚠️ модалка не закрылась — пометил failed (проверь вручную)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
