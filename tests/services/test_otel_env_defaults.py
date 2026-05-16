"""Tests for ``config.env_loader.apply_otel_env_defaults``."""

from __future__ import annotations

import base64
import os

import pytest

from config.env_loader import apply_otel_env_defaults


@pytest.fixture(autouse=True)
def _clear_otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OTEL_SDK_DISABLED",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "GRAFANA_CLOUD_STACK_ID",
        "GRAFANA_CLOUD_API_TOKEN",
        "GRAFANA_CLOUD_OTLP_REGION",
    ):
        monkeypatch.delenv(key, raising=False)


def test_direct_gateway_from_grafana_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_CLOUD_STACK_ID", "1182575")
    monkeypatch.setenv("GRAFANA_CLOUD_API_TOKEN", "glc_test_token")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    apply_otel_env_defaults()

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "https://otlp-gateway-prod-eu-west-2.grafana.net/otlp"
    )
    expected = base64.b64encode(b"1182575:glc_test_token").decode("ascii")
    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == f"Authorization=Basic {expected}"
    assert os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"


def test_respects_custom_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_CLOUD_STACK_ID", "1")
    monkeypatch.setenv("GRAFANA_CLOUD_API_TOKEN", "tok")
    monkeypatch.setenv("GRAFANA_CLOUD_OTLP_REGION", "us-central-0")

    apply_otel_env_defaults()

    assert "us-central-0" in os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]


def test_noop_without_grafana_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_otel_env_defaults()
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ


def test_sdk_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("GRAFANA_CLOUD_STACK_ID", "1")
    monkeypatch.setenv("GRAFANA_CLOUD_API_TOKEN", "tok")

    apply_otel_env_defaults()

    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
