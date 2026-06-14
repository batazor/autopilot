"""Registration flow logic — drive order + human hand-off + result mapping.

Uses a fake Playwright page so no real browser (or beta server) is touched.
"""
from __future__ import annotations

from games.wos.farm import register

from config import farm_accounts_db


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.visible = True  # register-view modal starts open

    async def goto(self, url: str, **_kw: object) -> None:
        self.calls.append(("goto", url))

    async def wait_for_timeout(self, ms: int) -> None:
        self.calls.append(("wait", ms))

    async def click(self, sel: str) -> None:
        self.calls.append(("click", sel))

    async def fill(self, sel: str, value: str) -> None:
        self.calls.append(("fill", sel, value))

    async def is_visible(self, _sel: str) -> bool:
        return self.visible


def _acct() -> farm_accounts_db.FarmAccount:
    return farm_accounts_db.FarmAccount(
        game="wos", username="StormFox12", password="Ab3kZ9qLmn21"
    )


async def test_drive_fills_then_hands_off_then_detects_success() -> None:
    page = _FakePage()
    events: list[str] = []

    async def on_ready() -> None:
        events.append("human")
        page.visible = False  # operator solved captcha + submitted → modal closed

    ok = await register.drive_registration(page, _acct(), on_ready)
    assert ok is True

    fills = [c for c in page.calls if c[0] == "fill"]
    assert fills == [
        ("fill", register.SEL_ACCOUNT, "StormFox12"),
        ("fill", register.SEL_PASSWORD, "Ab3kZ9qLmn21"),
        ("fill", register.SEL_REPASSWORD, "Ab3kZ9qLmn21"),
    ]
    # Sign Up modal opened before fields were filled.
    assert ("click", register.SEL_OPEN_SIGNUP) in page.calls
    assert page.calls.index(("click", register.SEL_OPEN_SIGNUP)) < page.calls.index(fills[0])
    # Human hand-off happened (exactly once), after the fields were filled.
    assert events == ["human"]


async def test_drive_reports_failure_when_modal_stays_open() -> None:
    page = _FakePage()

    async def on_ready() -> None:
        pass  # operator did nothing → modal still open → not registered

    ok = await register.drive_registration(page, _acct(), on_ready)
    assert ok is False


async def test_explicit_outcome_overrides_modal_heuristic() -> None:
    # The dashboard Done/Failed button returns an authoritative bool — it must
    # win over the modal-closed heuristic (here the modal is left "open").
    page = _FakePage()

    async def said_done() -> bool:
        return True

    async def said_failed() -> bool:
        return False

    assert await register.drive_registration(page, _acct(), said_done) is True
    assert page.visible is True  # heuristic would have said False; outcome won
    assert await register.drive_registration(page, _acct(), said_failed) is False
