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
        self.image_code_visible = False
        self.slider_visible = False
        self.slider_visible_after_checks: int | None = None
        self.slider_visibility_checks = 0
        self.attrs: dict[tuple[str, str], str | None] = {}
        self.boxes: dict[str, dict[str, float]] = {}
        self.natural_sizes: dict[str, dict[str, float]] = {}
        self.mouse = _FakeMouse(self)

    async def goto(self, url: str, **_kw: object) -> None:
        self.calls.append(("goto", url))

    async def wait_for_timeout(self, ms: int) -> None:
        self.calls.append(("wait", ms))

    async def click(self, sel: str) -> None:
        self.calls.append(("click", sel))

    async def fill(self, sel: str, value: str) -> None:
        self.calls.append(("fill", sel, value))

    async def is_visible(self, sel: str) -> bool:
        if sel == register.SEL_IMAGE_CODE_CAPTCHA:
            return self.image_code_visible
        if sel == register.SEL_SLIDER_CAPTCHA:
            self.slider_visibility_checks += 1
            if self.slider_visible_after_checks is not None:
                return self.slider_visibility_checks > self.slider_visible_after_checks
            return self.slider_visible
        if sel != register.SEL_REGISTER_VIEW:
            return False
        return self.visible

    async def get_attribute(self, sel: str, name: str) -> str | None:
        self.calls.append(("get_attribute", sel, name))
        return self.attrs.get((sel, name))

    def locator(self, sel: str) -> _FakeLocator:
        return _FakeLocator(self, sel)

    async def eval_on_selector(self, sel: str, _script: str) -> dict[str, float]:
        self.calls.append(("eval_on_selector", sel))
        return self.natural_sizes.get(sel, {"width": 0.0, "height": 0.0})


class _FakeLocator:
    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    async def bounding_box(self) -> dict[str, float] | None:
        self._page.calls.append(("bounding_box", self._selector))
        return self._page.boxes.get(self._selector)


class _FakeMouse:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def move(self, x: float, y: float, **kwargs: int) -> None:
        self._page.calls.append(("mouse.move", round(x, 2), round(y, 2), kwargs))

    async def down(self) -> None:
        self._page.calls.append(("mouse.down",))

    async def up(self) -> None:
        self._page.calls.append(("mouse.up",))


def _acct() -> farm_accounts_db.FarmAccount:
    return farm_accounts_db.FarmAccount(
        game="wos", username="NoraLily", password="Ab3kZ9qLmn21"
    )


async def test_drive_fills_then_hands_off_then_detects_success() -> None:
    page = _FakePage()
    events: list[str] = []

    async def on_ready(automation: register.AutomationReport) -> None:
        events.append("human")
        assert automation["image_code"] == "skipped"
        assert automation["slider"] == "not_present"
        assert automation["slider_expected"] == "auto"
        page.visible = False  # operator solved captcha + submitted → modal closed

    ok = await register.drive_registration(page, _acct(), on_ready)
    assert ok is True

    fills = [c for c in page.calls if c[0] == "fill"]
    assert fills == [
        ("fill", register.SEL_ACCOUNT, "NoraLily"),
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

    async def on_ready(_automation: register.AutomationReport) -> None:
        pass  # operator did nothing → modal still open → not registered

    ok = await register.drive_registration(page, _acct(), on_ready)
    assert ok is False


async def test_explicit_outcome_overrides_modal_heuristic() -> None:
    # The dashboard Done/Failed button returns an authoritative bool — it must
    # win over the modal-closed heuristic (here the modal is left "open").
    page = _FakePage()

    async def said_done(_automation: register.AutomationReport) -> bool:
        return True

    async def said_failed(_automation: register.AutomationReport) -> bool:
        return False

    assert await register.drive_registration(page, _acct(), said_done) is True
    assert page.visible is True  # heuristic would have said False; outcome won
    assert await register.drive_registration(page, _acct(), said_failed) is False


async def test_drive_fills_image_code_with_gift_code_solver() -> None:
    page = _FakePage()
    page.image_code_visible = True
    page.attrs[(register.SEL_IMAGE_CODE_IMG, "src")] = "data:image/png;base64,abc"
    events: list[str] = []

    async def on_ready(automation: register.AutomationReport) -> bool:
        events.append("human")
        assert automation["image_code"] == "solved"
        assert automation["slider"] == "not_present"
        return True

    def solver(img_b64: str) -> str:
        assert img_b64 == "data:image/png;base64,abc"
        return "WXYZ"

    ok = await register.drive_registration(
        page,
        _acct(),
        on_ready,
        image_code_solver=solver,
    )
    assert ok is True
    assert ("fill", register.SEL_IMAGE_CODE_INPUT, "WXYZ") in page.calls
    assert events == ["human"]


async def test_drive_drags_slider_with_ddddocr_match() -> None:
    page = _FakePage()
    page.slider_visible = True
    page.attrs[(register.SEL_SLIDER_BG, "src")] = "data:image/png;base64,bg"
    page.attrs[(register.SEL_SLIDER_PIECE, "src")] = "data:image/png;base64,piece"
    page.boxes = {
        register.SEL_SLIDER_BG: {"x": 20, "y": 30, "width": 280, "height": 155},
        register.SEL_SLIDER_PIECE: {"x": 20, "y": 40, "width": 42, "height": 42},
        register.SEL_SLIDER_TRACK: {"x": 20, "y": 220, "width": 280, "height": 32},
        register.SEL_SLIDER_HANDLE: {"x": 20, "y": 220, "width": 32, "height": 32},
    }
    page.natural_sizes[register.SEL_SLIDER_BG] = {"width": 560, "height": 310}
    events: list[str] = []

    async def on_ready(automation: register.AutomationReport) -> bool:
        events.append("human")
        assert automation["image_code"] == "disabled"
        assert automation["slider"] == "dragged"
        return True

    def slider_solver(target_img: str | bytes, background_img: str | bytes) -> dict[str, list[int]]:
        assert target_img == "data:image/png;base64,piece"
        assert background_img == "data:image/png;base64,bg"
        return {"target": [180, 20, 222, 62]}

    ok = await register.drive_registration(
        page,
        _acct(),
        on_ready,
        solve_image_code=False,
        slider_solver=slider_solver,
    )

    assert ok is True
    assert ("mouse.down",) in page.calls
    assert ("mouse.up",) in page.calls
    # target x 180 scaled from 560px natural width to 280px rendered width = 90px,
    # plus a small calibration overshoot because the live slider was stopping short.
    assert ("mouse.move", 134.0, 236.0, {"steps": 16}) in page.calls
    assert events == ["human"]


async def test_drive_waits_for_delayed_slider() -> None:
    page = _FakePage()
    page.slider_visible_after_checks = 2
    page.attrs[(register.SEL_SLIDER_BG, "src")] = "data:image/png;base64,bg"
    page.attrs[(register.SEL_SLIDER_PIECE, "src")] = "data:image/png;base64,piece"
    page.boxes = {
        register.SEL_SLIDER_BG: {"x": 20, "y": 30, "width": 280, "height": 155},
        register.SEL_SLIDER_TRACK: {"x": 20, "y": 220, "width": 280, "height": 32},
        register.SEL_SLIDER_HANDLE: {"x": 20, "y": 220, "width": 32, "height": 32},
    }
    page.natural_sizes[register.SEL_SLIDER_BG] = {"width": 560, "height": 310}

    async def on_ready(automation: register.AutomationReport) -> bool:
        assert automation["slider"] == "dragged"
        return True

    def slider_solver(_target_img: str | bytes, _background_img: str | bytes) -> dict[str, list[int]]:
        return {"target": [180, 20, 222, 62]}

    ok = await register.drive_registration(
        page,
        _acct(),
        on_ready,
        solve_image_code=False,
        slider_solver=slider_solver,
    )

    assert ok is True
    assert page.slider_visibility_checks >= 3
    assert ("mouse.up",) in page.calls
