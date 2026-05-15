from __future__ import annotations

from pathlib import Path

from config.loader import load_settings


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
  url: http://yaml-ocr:8000
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
    monkeypatch.setenv("WOS_OCR_URL", "http://env-ocr:8000")
    monkeypatch.setenv("WOS_OCR_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("OMNIPARSER_URL", "http://env-omni:8765")
    monkeypatch.setenv("OMNIPARSER_TIMEOUT_SECONDS", "7")

    settings = load_settings(settings_path)

    assert settings.redis.url == "redis://env:6379/1"
    assert settings.redis.key_prefix == "test"
    assert settings.ocr.url == "http://env-ocr:8000"
    assert settings.ocr.timeout_seconds == 42
    assert settings.omniparser.url == "http://env-omni:8765"
    assert settings.omniparser.timeout_seconds == 7
