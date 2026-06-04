from __future__ import annotations

import numpy as np
import pytest

from config.loader import get_settings
from layout.types import Region
from ocr.client import OcrClient, OCRResult


@pytest.fixture(autouse=True)
def _isolate_cache() -> None:
    OcrClient.clear_cache()


def test_parse_tesseract_tsv_groups_words_by_line() -> None:
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95\tWhiteout",
            "5\t1\t1\t1\t1\t2\t11\t0\t10\t10\t85\tSurvival",
            "5\t1\t1\t1\t2\t1\t0\t12\t10\t10\t90\tBot",
            "5\t1\t1\t1\t2\t2\t11\t12\t10\t10\t-1\tignored",
        ]
    )

    text, confidence = OcrClient._parse_tesseract_tsv(tsv)

    assert text == "Whiteout Survival\nBot"
    assert confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_client_sends_clamped_crop_to_local_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_shapes: list[tuple[int, ...]] = []

    async def _fake_ocr_crop(
        self: OcrClient,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> OCRResult:
        seen_shapes.append(tuple(crop.shape))
        return OCRResult(region_id=region_id, text="ok", confidence=1.0)

    monkeypatch.setattr(OcrClient, "_ocr_crop", _fake_ocr_crop)
    image = np.zeros((40, 50, 3), dtype=np.uint8)

    result = await OcrClient(get_settings()).ocr_region(
        image,
        Region(x=45, y=35, w=20, h=20),
        region_id="edge",
    )

    assert result.text == "ok"
    assert seen_shapes == [(5, 5, 3)]


def test_fast_line_uses_single_line_tesseract_psm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cmd: list[list[str]] = []

    monkeypatch.setattr("ocr.client.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def _fake_run(cmd, **kwargs):
        captured_cmd.append(list(cmd))

        class _Proc:
            returncode = 0
            # bytes, not str: production runs ``subprocess.run`` without
            # ``text=True`` and decodes with errors="replace" (see _run_tesseract).
            stdout = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
            stderr = b""

        return _Proc()

    monkeypatch.setattr("ocr.client.subprocess.run", _fake_run)
    crop = np.zeros((10, 20, 3), dtype=np.uint8)

    OcrClient(get_settings())._run_tesseract(crop, preprocess="fast_line")

    assert captured_cmd
    assert captured_cmd[0][captured_cmd[0].index("--psm") + 1] == "7"
    assert captured_cmd[0][captured_cmd[0].index("-l") + 1] == "eng"
    # ``fast_line`` is shared with timers (``12:34:56``) — no digit whitelist,
    # the ``:`` separators must survive.
    assert "tessedit_char_whitelist" not in " ".join(captured_cmd[0])


def test_fast_digits_uses_psm7_and_digit_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cmd: list[list[str]] = []

    monkeypatch.setattr("ocr.client.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def _fake_run(cmd, **kwargs):
        captured_cmd.append(list(cmd))

        class _Proc:
            returncode = 0
            stdout = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
            stderr = b""

        return _Proc()

    monkeypatch.setattr("ocr.client.subprocess.run", _fake_run)
    crop = np.zeros((10, 20, 3), dtype=np.uint8)

    OcrClient(get_settings())._run_tesseract(crop, preprocess="fast_digits")

    cmd = captured_cmd[0]
    # Single line (PSM 7) like ``fast_line``, but digit-whitelisted so an
    # ambiguous glyph resolves to a digit instead of e.g. ``&`` — which would
    # be stripped downstream and silently shorten a player id.
    assert cmd[cmd.index("--psm") + 1] == "7"
    wl_idx = cmd.index("-c") + 1
    assert cmd[wl_idx] == "tessedit_char_whitelist=0123456789"


def test_enhance_uses_single_word_tesseract_psm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cmd: list[list[str]] = []

    monkeypatch.setattr("ocr.client.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def _fake_run(cmd, **kwargs):
        captured_cmd.append(list(cmd))

        class _Proc:
            returncode = 0
            # bytes, not str: production runs ``subprocess.run`` without
            # ``text=True`` and decodes with errors="replace" (see _run_tesseract).
            stdout = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
            stderr = b""

        return _Proc()

    monkeypatch.setattr("ocr.client.subprocess.run", _fake_run)
    crop = np.zeros((10, 20, 3), dtype=np.uint8)

    OcrClient(get_settings())._run_tesseract(crop, preprocess="enhance")

    assert captured_cmd[0][captured_cmd[0].index("--psm") + 1] == "8"
    assert "tessedit_char_whitelist" not in " ".join(captured_cmd[0])


def test_enhance_line_uses_single_line_tesseract_psm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cmd: list[list[str]] = []

    monkeypatch.setattr("ocr.client.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def _fake_run(cmd, **kwargs):
        captured_cmd.append(list(cmd))

        class _Proc:
            returncode = 0
            stdout = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
            stderr = b""

        return _Proc()

    monkeypatch.setattr("ocr.client.subprocess.run", _fake_run)
    crop = np.zeros((10, 20, 3), dtype=np.uint8)

    OcrClient(get_settings())._run_tesseract(crop, preprocess="enhance_line")

    assert captured_cmd[0][captured_cmd[0].index("--psm") + 1] == "7"
    assert "tessedit_char_whitelist" not in " ".join(captured_cmd[0])


def test_digits_uses_psm8_and_digit_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cmd: list[list[str]] = []

    monkeypatch.setattr("ocr.client.shutil.which", lambda cmd: f"/usr/bin/{cmd}")

    def _fake_run(cmd, **kwargs):
        captured_cmd.append(list(cmd))

        class _Proc:
            returncode = 0
            # bytes, not str: production runs ``subprocess.run`` without
            # ``text=True`` and decodes with errors="replace" (see _run_tesseract).
            stdout = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tconf\ttext\n"
            stderr = b""

        return _Proc()

    monkeypatch.setattr("ocr.client.subprocess.run", _fake_run)
    crop = np.zeros((10, 20, 3), dtype=np.uint8)

    OcrClient(get_settings())._run_tesseract(crop, preprocess="digits")

    cmd = captured_cmd[0]
    assert cmd[cmd.index("--psm") + 1] == "8"
    wl_idx = cmd.index("-c") + 1
    assert cmd[wl_idx] == "tessedit_char_whitelist=0123456789"
