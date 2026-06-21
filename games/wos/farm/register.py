"""Human-in-the-loop registration for the WOS beta web client (R5 / owner-only).

Playwright opens the beta client and fills the generated username / password /
confirm fields. When the beta form shows an image-code text captcha, we reuse
the same ddddocr-based solver as gift-code redeem to fill the text box. Some
beta builds used a slider captcha too; that path stays as an optional fallback
and is marked ``not_present`` when the current build only has the text captcha.
The operator still handles the final browser submit. After the form is ready,
we wait for the SDK register API to return HTTP 200 with body ``code == 200``
before stamping the account as registered in the DB. The SDK's validation
failure ``HTTP 400 / code 1024`` asks the operator to retry the same account
instead of failing it immediately.

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
from contextlib import suppress
from inspect import isawaitable
from typing import TYPE_CHECKING, Any

from games.wos.farm import generator

from century.captcha import solve_captcha, solve_slider_match
from config import farm_accounts_db

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)
AutomationReport = dict[str, str]

BETA_URL = "https://h5-res.wzqqe.com/land/index.html?invite=a1ia4w09"
REGISTER_API_URL = "https://sdk-api.benhng.com/api/sdk/register"

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
_REGISTER_RESPONSE_WAIT_AFTER_DONE_MS = 45000
_REGISTER_RETRY_CODE = 1024
_REGISTER_MAX_SUBMIT_ATTEMPTS = 3


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


def _register_response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _is_register_api_response(response: Any) -> bool:
    url = str(getattr(response, "url", "") or "")
    return url.split("?", 1)[0] == REGISTER_API_URL


def _register_response_method(response: Any) -> str:
    request = getattr(response, "request", None)
    method = getattr(request, "method", "") if request is not None else ""
    if callable(method):
        with suppress(Exception):
            method = method()
    return str(method or "").upper()


async def _read_register_response_json(response: Any) -> dict[str, Any] | None:
    reader = getattr(response, "json", None)
    if not callable(reader):
        return None
    try:
        data = reader()
        if isawaitable(data):
            data = await data
    except Exception:
        logger.warning("farm: failed to read sdk register JSON response", exc_info=True)
        return None
    return data if isinstance(data, dict) else None


async def _register_response_result(response: Any) -> tuple[str, str]:
    status = _register_response_status(response)
    data = await _read_register_response_json(response)
    body_code = data.get("code") if data else None
    message = data.get("message") if data else None
    account = data.get("account") if data else None
    nickname = data.get("nickName") if data else None
    user_id = data.get("userId") if data else None
    detail = (
        f"http={status} body_code={body_code!r} message={message!r} "
        f"account={account!r} nickName={nickname!r} userId={user_id!r}"
    )
    try:
        clean_body_code = int(body_code)
    except (TypeError, ValueError):
        clean_body_code = 0
    if status == 200 and clean_body_code == 200:
        return "success", detail
    if status == 400 and clean_body_code == _REGISTER_RETRY_CODE:
        return "retry", detail
    return "failed", detail


def _start_register_response_watch(
    page: Any,
) -> tuple[asyncio.Future[str], list[str], Callable[[], None]]:
    """Watch SDK register responses before the human can click Sign Up."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()
    attempts: list[str] = []
    tasks: set[asyncio.Task[None]] = set()

    async def inspect_response(response: Any) -> None:
        result, detail = await _register_response_result(response)
        attempts.append(detail)
        logger.info("farm: sdk register response %s", detail)
        if not future.done():
            future.set_result(result)

    def on_response(response: Any) -> None:
        if not _is_register_api_response(response):
            return
        method = _register_response_method(response)
        if method and method != "POST":
            logger.debug("farm: ignoring sdk register %s response", method)
            return
        task = asyncio.create_task(inspect_response(response))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    attach = getattr(page, "on", None)
    if not callable(attach):
        logger.warning("farm: cannot watch sdk register response; page.on unavailable")
        future.set_result("failed")
        return future, attempts, lambda: None
    try:
        attach("response", on_response)
    except Exception:
        logger.warning("farm: failed to attach sdk register response watcher", exc_info=True)
        future.set_result("failed")
        return future, attempts, lambda: None

    def detach() -> None:
        remove = getattr(page, "remove_listener", None) or getattr(page, "off", None)
        if callable(remove):
            with suppress(Exception):
                remove("response", on_response)
        for task in list(tasks):
            task.cancel()

    return future, attempts, detach


async def _await_register_response_result(
    future: asyncio.Future[str],
    attempts: list[str],
    *,
    timeout_ms: int = _REGISTER_RESPONSE_WAIT_AFTER_DONE_MS,
) -> str:
    try:
        return str(
            await asyncio.wait_for(
                asyncio.shield(future),
                timeout=max(0.001, timeout_ms / 1000),
            )
        )
    except TimeoutError:
        if attempts:
            logger.warning(
                "farm: sdk register did not confirm success after submit "
                "(responses seen: %s)",
                "; ".join(attempts),
            )
        else:
            logger.warning(
                "farm: no sdk register POST response seen within %.1fs after submit",
                timeout_ms / 1000,
            )
        return "failed"


async def _wait_for_register_or_operator_failed(
    register_response: asyncio.Future[str],
    register_attempts: list[str],
    on_ready_for_human: Callable[[AutomationReport], Awaitable[bool | None]],
    automation: AutomationReport,
    *,
    timeout_ms: int,
) -> str:
    register_task = asyncio.create_task(
        _await_register_response_result(
            register_response,
            register_attempts,
            timeout_ms=timeout_ms,
        )
    )
    operator_task = asyncio.create_task(on_ready_for_human(automation))
    try:
        while True:
            wait_for = {register_task}
            if operator_task is not None:
                wait_for.add(operator_task)
            done, _pending = await asyncio.wait(
                wait_for,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if register_task in done:
                return await register_task
            if operator_task is not None and operator_task in done:
                outcome = await operator_task
                operator_task = None
                if outcome is False:
                    register_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await register_task
                    return "failed"
                logger.info(
                    "farm: operator handoff acknowledged; waiting for sdk/register"
                )
                continue
    finally:
        if operator_task is not None and not operator_task.done():
            operator_task.cancel()
            with suppress(asyncio.CancelledError):
                await operator_task
        if not register_task.done():
            register_task.cancel()
            with suppress(asyncio.CancelledError):
                await register_task


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
    register_response_timeout_ms: int = _REGISTER_RESPONSE_WAIT_AFTER_DONE_MS,
    max_submit_attempts: int = _REGISTER_MAX_SUBMIT_ATTEMPTS,
) -> bool:
    """Fill the signup form, hand off to the human for captcha+submit, report result.

    ``on_ready_for_human`` is started after the fields are filled. Success comes
    from the SDK register response, so the operator only needs to solve any
    remaining captcha and click Sign Up in the browser. A callback result of
    ``False`` still cancels the attempt manually.
    """
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.click(SEL_OPEN_SIGNUP)
    await page.wait_for_timeout(1000)
    previous_register = ""
    submit_attempts = max(1, int(max_submit_attempts or 1))
    for attempt in range(1, submit_attempts + 1):
        await page.fill(SEL_ACCOUNT, account.username)
        await page.fill(SEL_PASSWORD, account.password)
        await page.fill(SEL_REPASSWORD, account.password)
        automation: AutomationReport = {
            "stage": "awaiting_submit",
            "image_code": "disabled",
            "slider": "disabled",
            "slider_expected": "auto",
            "register_attempt": str(attempt),
            "register_max_attempts": str(submit_attempts),
        }
        if previous_register:
            automation["previous_register"] = previous_register
        if solve_image_code:
            automation["image_code"] = (
                "solved"
                if await try_solve_image_code_captcha(page, solver=image_code_solver)
                else "skipped"
            )
        if solve_slider:
            automation["slider"] = await try_solve_slider_captcha(page, solver=slider_solver)
        logger.info(
            "farm: filled signup for %s — awaiting captcha+submit "
            "(attempt %d/%d)",
            account.username,
            attempt,
            submit_attempts,
        )
        register_response, register_attempts, detach_register_watch = (
            _start_register_response_watch(page)
        )
        try:
            result = await _wait_for_register_or_operator_failed(
                register_response,
                register_attempts,
                on_ready_for_human,
                automation,
                timeout_ms=register_response_timeout_ms,
            )
        finally:
            detach_register_watch()
            if not register_response.done():
                register_response.cancel()
        if result == "success":
            return True
        previous_register = register_attempts[-1] if register_attempts else result
        if result == "retry" and attempt < submit_attempts:
            logger.info(
                "farm: sdk register requested retry for %s (attempt %d/%d)",
                account.username,
                attempt,
                submit_attempts,
            )
            await page.wait_for_timeout(1000)
            continue
        return False
    return False


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


def _is_playwright_target_closed(exc: BaseException) -> bool:
    """Whether Playwright reports a user/browser-closed page/context/browser."""
    names = {cls.__name__ for cls in type(exc).mro()}
    if "TargetClosedError" in names:
        return True
    text = str(exc)
    return (
        "Target page, context or browser has been closed" in text
        or "Browser has been closed" in text
        or "Target closed" in text
    )


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
            except Exception as exc:
                if not _is_playwright_target_closed(exc):
                    raise
                logger.warning(
                    "farm: browser/page closed before registration finished; "
                    "marking account failed for this attempt"
                )
                return False
            finally:
                with suppress(Exception):
                    await browser.close()

    return asyncio.run(_run())


async def console_done(
    account: farm_accounts_db.FarmAccount,
    automation: AutomationReport | None = None,
) -> None:
    """Block on console ENTER as an optional manual acknowledgement."""
    prompt = (
        f"\n>>> Fields are filled for '{account.username}'. In the browser, check "
        f"the captcha and click Sign Up. Waiting for sdk/register; ENTER is optional... "
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
    """Publish the pending browser handoff and watch for manual **Failed**.

    Registration success is detected from the SDK response in ``drive_registration``;
    this side channel exists so the dashboard can show the active account and
    let the operator abort with Failed.
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
        help="publish dashboard handoff and wait for sdk/register instead of console ENTER",
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
        print("Waiting for sdk/register; click Sign Up in the browser (/farm shows status)...")
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
    print(
        "registered"
        if ok
        else "sdk/register did not confirm HTTP 200 + body code 200; marked failed, please check manually"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
