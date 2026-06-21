from __future__ import annotations

from typing import Any

from century import captcha


class _FakeSlideOcr:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def slide_match(
        self,
        target_img: bytes,
        background_img: bytes,
        *,
        simple_target: bool = False,
    ) -> dict[str, list[int]]:
        self.calls.append(("slide_match", target_img, background_img, simple_target))
        return {"target": [12, 3, 44, 35]}

    def slide_comparison(
        self,
        bg_with_gap: bytes,
        full_bg: bytes,
    ) -> dict[str, list[int]]:
        self.calls.append(("slide_comparison", bg_with_gap, full_bg))
        return {"target": [18, 5]}


def test_solve_slider_match_decodes_data_urls(monkeypatch: Any) -> None:
    fake = _FakeSlideOcr()
    monkeypatch.setattr(captcha, "_get_slide_ocr", lambda: fake)

    result = captcha.solve_slider_match(
        "data:image/png;base64,QQ==",
        "data:image/png;base64,Qg==",
        simple_target=True,
    )

    assert result == {"target": [12, 3, 44, 35]}
    assert fake.calls == [("slide_match", b"A", b"B", True)]


def test_solve_slider_comparison_decodes_base64(monkeypatch: Any) -> None:
    fake = _FakeSlideOcr()
    monkeypatch.setattr(captcha, "_get_slide_ocr", lambda: fake)

    result = captcha.solve_slider_comparison("Qw==", "RA==")

    assert result == {"target": [18, 5]}
    assert fake.calls == [("slide_comparison", b"C", b"D")]
