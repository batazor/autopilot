"""Human-in-the-loop registration for the WOS beta web client (R5 / owner-only).

Playwright opens the beta client and fills the generated username / password /
confirm fields. When the beta form shows an image-code text captcha, we reuse
the same ddddocr-based solver as gift-code redeem to fill the text box. Some
beta builds used a slider captcha too; that path stays as an optional fallback
and is marked ``not_present`` when the current build only has the text captcha.
The operator still confirms the final submit handoff.
After the operator signals "done", we read whether the modal closed (success
heuristic) and stamp the account's status in the DB.

Run (owner machine, after `uv sync --extra farm` + `playwright install chromium`):

    uv run python -m games.wos.farm.register --username balabol
    uv run python -m games.wos.farm.register --seed batch-1   # auto pretty name

The form fields were confirmed live (2026-06-14): the "Sign Up" CTA opens a
``register-view`` modal with ``input[name=account|password|repassword]``,
``#imageCodeCaptchaImg`` / ``#imageCodeInput``. Older variants may also expose
a ``slider-captcha`` block with ``#sliderCaptchaBg`` / ``#sliderCaptchaPiece``.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import TYPE_CHECKING, Any

from games.wos.farm import generator

from century.captcha import solve_captcha, solve_slider_match
from config import farm_accounts_db

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)
AutomationReport = dict[str, str]

BETA_URL = "https://h5-res.wzqqe.com/land/index.html"

SEL_OPEN_SIGNUP = "button.cta-btn--secondary"
SEL_ACCOUNT = "input[name=account]"
SEL_PASSWORD = "input[name=password]"
SEL_REPASSWORD = "input[name=repassword]"
SEL_REGISTER_VIEW = ".register-view"
SEL_IMAGE_CODE_CAPTCHA = "#imageCodeCaptcha"
SEL_IMAGE_CODE_IMG = "#imageCodeCaptchaImg"
SEL_IMAGE_CODE_INPUT = "#imageCodeInput"
SEL_SLIDER_CAPTCHA = "#sliderCaptcha"
SEL_SLIDER_BG = "#sliderCaptchaBg"
SEL_SLIDER_PIECE = "#sliderCaptchaPiece"
SEL_SLIDER_TRACK = "#sliderCaptchaTrack"
SEL_SLIDER_HANDLE = "#sliderCaptchaHandle"

_RESULT_POLL_TRIES = 20
_RESULT_POLL_INTERVAL_MS = 500
_IMAGE_CODE_POLL_TRIES = 10
_IMAGE_CODE_POLL_INTERVAL_MS = 300
_SLIDER_POLL_TRIES = 10
_SLIDER_POLL_INTERVAL_MS = 300
_SLIDER_DRAG_EXTRA_PX = 8.0


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


async def try_solve_image_code_captcha(
    page: Any,
    *,
    solver: Callable[[str], str] = solve_captcha,
) -> bool:
    """Fill the visible image-code captcha using the gift-code OCR solver.

    Returns ``False`` when the beta page uses a different captcha type, has not
    loaded the image yet, or OCR fails. That keeps the existing human handoff as
    the fallback for anything more interactive than a plain image-code box.
    """
    for _ in range(_IMAGE_CODE_POLL_TRIES):
        try:
            visible = await page.is_visible(SEL_IMAGE_CODE_CAPTCHA)
        except Exception:
            logger.debug("farm: image-code captcha visibility check failed", exc_info=True)
            return False
        if not visible:
            await page.wait_for_timeout(_IMAGE_CODE_POLL_INTERVAL_MS)
            continue

        img_b64 = await page.get_attribute(SEL_IMAGE_CODE_IMG, "src")
        if not img_b64:
            await page.wait_for_timeout(_IMAGE_CODE_POLL_INTERVAL_MS)
            continue

        try:
            captcha_text = solver(img_b64)
        except Exception:
            logger.exception("farm: image-code captcha solve failed")
            return False
        if not captcha_text:
            return False
        await page.fill(SEL_IMAGE_CODE_INPUT, captcha_text)
        logger.info("farm: text captcha solved via gift-code OCR")
        return True
    return False


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _box(page: Any, selector: str) -> dict[str, float] | None:
    raw = await page.locator(selector).bounding_box()
    if not raw:
        return None
    return {
        "x": _number(raw.get("x")),
        "y": _number(raw.get("y")),
        "width": _number(raw.get("width")),
        "height": _number(raw.get("height")),
    }


async def _image_natural_size(page: Any, selector: str) -> tuple[float, float] | None:
    try:
        raw = await page.eval_on_selector(
            selector,
            "(el) => ({ width: el.naturalWidth || el.width, height: el.naturalHeight || el.height })",
        )
    except Exception:
        logger.debug("farm: cannot read image natural size for %s", selector, exc_info=True)
        return None
    if not isinstance(raw, dict):
        return None
    width = _number(raw.get("width"))
    height = _number(raw.get("height"))
    if width <= 0 or height <= 0:
        return None
    return width, height


async def try_solve_slider_captcha(
    page: Any,
    *,
    solver: Callable[[str | bytes, str | bytes], dict[str, Any]] = solve_slider_match,
) -> str:
    """Drag a visible slider captcha, or report that this beta build has none."""
    for _ in range(_SLIDER_POLL_TRIES):
        try:
            visible = await page.is_visible(SEL_SLIDER_CAPTCHA)
        except Exception:
            logger.debug("farm: slider captcha visibility check failed", exc_info=True)
            return "failed"
        if not visible:
            await page.wait_for_timeout(_SLIDER_POLL_INTERVAL_MS)
            continue

        background_img = await page.get_attribute(SEL_SLIDER_BG, "src")
        target_img = await page.get_attribute(SEL_SLIDER_PIECE, "src")
        if not background_img or not target_img:
            await page.wait_for_timeout(_SLIDER_POLL_INTERVAL_MS)
            continue

        try:
            match = solver(target_img, background_img)
            target = match.get("target")
        except Exception:
            logger.exception("farm: slider captcha solve failed")
            return "failed"
        if not isinstance(target, list | tuple) or not target:
            return "failed"

        bg_box = await _box(page, SEL_SLIDER_BG)
        track_box = await _box(page, SEL_SLIDER_TRACK)
        handle_box = await _box(page, SEL_SLIDER_HANDLE)
        natural = await _image_natural_size(page, SEL_SLIDER_BG)
        if not bg_box or not track_box or not handle_box or not natural:
            return "failed"

        scale_x = bg_box["width"] / natural[0] if natural[0] else 1.0
        target_x = _number(target[0]) * scale_x
        drag_px = max(0.0, target_x + _SLIDER_DRAG_EXTRA_PX)
        if drag_px <= 0:
            return "failed"

        start_x = handle_box["x"] + handle_box["width"] / 2
        start_y = handle_box["y"] + handle_box["height"] / 2
        max_x = track_box["x"] + track_box["width"] - handle_box["width"] / 2
        end_x = min(start_x + drag_px, max_x)
        steps = max(8, min(35, int(drag_px / 6)))

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(end_x, start_y, steps=steps)
        await page.wait_for_timeout(200)
        await page.mouse.up()
        await page.wait_for_timeout(800)
        logger.info(
            "farm: dragged slider captcha via ddddocr slide_match "
            "(target_x=%.1f drag_px=%.1f)",
            target_x,
            drag_px,
        )
        return "dragged"
    logger.info("farm: slider captcha not present; using text captcha flow")
    return "not_present"


async def drive_registration(
    page: Any,
    account: farm_accounts_db.FarmAccount,
    on_ready_for_human: Callable[[AutomationReport], Awaitable[bool | None]],
    *,
    url: str = BETA_URL,
    solve_image_code: bool = True,
    image_code_solver: Callable[[str], str] = solve_captcha,
    solve_slider: bool = True,
    slider_solver: Callable[[str | bytes, str | bytes], dict[str, Any]] = solve_slider_match,
) -> bool:
    """Fill the signup form, hand off to the human for captcha+submit, report result.

    ``on_ready_for_human`` is awaited *after* the fields are filled and must
    block until the operator has handled the remaining captcha steps and clicked
    Sign Up. If it returns a bool that is the authoritative outcome (the
    operator told us Done/Failed); if it returns ``None`` we fall back to the
    modal-closed heuristic. Kept injectable so the flow is unit-testable without
    a browser.
    """
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.click(SEL_OPEN_SIGNUP)
    await page.wait_for_timeout(1000)
    await page.fill(SEL_ACCOUNT, account.username)
    await page.fill(SEL_PASSWORD, account.password)
    await page.fill(SEL_REPASSWORD, account.password)
    automation: AutomationReport = {
        "stage": "awaiting_submit",
        "image_code": "disabled",
        "slider": "disabled",
        "slider_expected": "auto",
    }
    if solve_image_code:
        automation["image_code"] = (
            "solved"
            if await try_solve_image_code_captcha(page, solver=image_code_solver)
            else "skipped"
        )
    if solve_slider:
        automation["slider"] = await try_solve_slider_captcha(page, solver=slider_solver)
    logger.info("farm: filled signup for %s — awaiting captcha+submit", account.username)
    outcome = await on_ready_for_human(automation)
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
    done: Callable[[AutomationReport], Awaitable[bool | None]],
    headless: bool = False,
    solve_image_code: bool = True,
    solve_slider: bool = True,
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
                return await drive_registration(
                    page,
                    account,
                    done,
                    url=url,
                    solve_image_code=solve_image_code,
                    solve_slider=solve_slider,
                )
            finally:
                await browser.close()

    return asyncio.run(_run())


async def console_done(
    account: farm_accounts_db.FarmAccount,
    automation: AutomationReport | None = None,
) -> None:
    """Block on a console ENTER while the operator solves the captcha + submits.

    Returns ``None`` → outcome falls back to the modal-closed heuristic.
    """
    prompt = (
        f"\n>>> Fields are filled for '{account.username}'. In the browser, check "
        f"the captcha, click Sign Up, then press ENTER here... "
    )
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input, prompt)


async def ui_done(
    account: farm_accounts_db.FarmAccount,
    automation: AutomationReport | None = None,
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
    farm_handoff.set_pending(client, account.username, **(automation or {}))
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
        "(Playwright fills the form and tries ddddocr captcha helpers).",
    )
    parser.add_argument("--username", help="desired username; falls back to a pretty name if taken")
    parser.add_argument(
        "--existing",
        action="store_true",
        help="register an already generated farm account from the local DB",
    )
    parser.add_argument("--seed", help="deterministic seed for the generated name/password")
    parser.add_argument("--server", default="wos_beta")
    parser.add_argument("--headless", action="store_true", help="run without a visible window (debug)")
    parser.add_argument(
        "--ui",
        action="store_true",
        help="wait for the dashboard Done/Failed button instead of console ENTER",
    )
    parser.add_argument(
        "--no-captcha-ocr",
        action="store_true",
        help="do not auto-fill the image-code captcha with the gift-code OCR solver",
    )
    parser.add_argument(
        "--no-slider-solver",
        action="store_true",
        help="do not auto-drag the slider captcha with ddddocr slide_match",
    )
    args = parser.parse_args(argv)

    if args.existing:
        if not args.username:
            msg = "--existing requires --username"
            raise SystemExit(msg)
        acct = farm_accounts_db.get_account(args.username)
        if acct is None:
            msg = f"farm account not found: {args.username}"
            raise SystemExit(msg)
        print(f"Using existing farm account: {acct.username}")
    else:
        claim = generator.add_or_generate(args.username, seed=args.seed, server=args.server)
        acct = claim.account
        if claim.requested_taken:
            print(f"Requested username '{claim.requested}' is taken; using generated username: {acct.username}")
    if args.ui:
        print(f"Account: {acct.username}  password: ***  (status: {acct.status})")
        print("Waiting for the dashboard Done/Failed button (/farm)...")
    else:
        print(f"Account: {acct.username}  password: {acct.password}  (status: {acct.status})")

    done = (
        (lambda automation: ui_done(acct, automation))
        if args.ui
        else (lambda automation: console_done(acct, automation))
    )
    ok = register_account(
        acct,
        done=done,
        headless=args.headless,
        solve_image_code=not args.no_captcha_ocr,
        solve_slider=not args.no_slider_solver,
    )
    farm_accounts_db.set_status(
        acct.username,
        farm_accounts_db.STATUS_REGISTERED if ok else farm_accounts_db.STATUS_FAILED,
    )
    print("registered" if ok else "registration modal stayed open; marked failed, please check manually")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
