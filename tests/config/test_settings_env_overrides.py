from __future__ import annotations

from typing import TYPE_CHECKING

from config.loader import load_settings

if TYPE_CHECKING:
    from pathlib import Path


def test_load_settings_applies_runtime_env_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        """
redis:
  url: redis://yaml:6379/0
  key_prefix: wos
ocr:
  lang: eng
  tesseract_cmd: tesseract
  tessdata_dir: ""
  timeout_seconds: 10
omniparser:
  url: http://yaml-omni:8765
  timeout_seconds: 120
scheduler:
  interval_seconds: 30
  ortools_timeout_seconds: 1.0
worker: {}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WOS_REDIS_URL", "redis://env:6379/1")
    monkeypatch.setenv("WOS_REDIS_KEY_PREFIX", "test")
    monkeypatch.setenv("WOS_OCR_LANG", "eng")
    monkeypatch.setenv("WOS_TESSERACT_CMD", "/opt/bin/tesseract")
    monkeypatch.setenv("TESSDATA_PREFIX", "/opt/share/tessdata")
    monkeypatch.setenv("WOS_OCR_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("OMNIPARSER_URL", "http://env-omni:8765")
    monkeypatch.setenv("OMNIPARSER_TIMEOUT_SECONDS", "7")

    settings = load_settings(settings_path)

    assert settings.redis.url == "redis://env:6379/1"
    assert settings.redis.key_prefix == "test"
    assert settings.ocr.lang == "eng"
    assert settings.ocr.tesseract_cmd == "/opt/bin/tesseract"
    assert settings.ocr.tessdata_dir == "/opt/share/tessdata"
    assert settings.ocr.timeout_seconds == 42
    assert settings.omniparser.url == "http://env-omni:8765"
    assert settings.omniparser.timeout_seconds == 7
