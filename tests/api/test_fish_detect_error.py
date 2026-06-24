"""Tests for the fish-detect inference-error explainer.

A raw ``ConnectError`` when the optional inference container simply isn't running
should become a clear, actionable message; meaningful errors (HTTP/auth) pass
through untouched.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from api.services.fish_detect import _explain_inference_error

if TYPE_CHECKING:
    import pytest

_CONNECT_ERR = "inference request failed: ConnectError: All connection attempts failed"


def test_http_error_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-connect error must pass through verbatim — and not even consult the
    # lifecycle (so a raising probe proves it was never called).
    def _raise() -> dict[str, str]:
        msg = "lifecycle must not be queried for HTTP errors"
        raise AssertionError(msg)

    monkeypatch.setattr("api.services.inference_lifecycle.get_status", _raise)
    raw = "inference HTTP 401: Unauthorized"
    assert _explain_inference_error(raw) == raw


def test_connect_failure_when_stopped_points_at_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.inference_lifecycle.get_status", lambda: {"phase": "stopped"}
    )
    msg = _explain_inference_error(_CONNECT_ERR)
    assert "not running" in msg
    assert "Inference service control" in msg
    assert "stopped" in msg


def test_connect_failure_when_ready_keeps_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.inference_lifecycle.get_status", lambda: {"phase": "ready"}
    )
    # Genuinely running yet unreachable → keep the original diagnostic.
    assert _explain_inference_error(_CONNECT_ERR) == _CONNECT_ERR


def test_connect_failure_when_lifecycle_errors_says_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> dict[str, str]:
        msg = "docker probe blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr("api.services.inference_lifecycle.get_status", _raise)
    msg = _explain_inference_error(
        "inference request failed: ConnectError: connection refused"
    )
    assert "not running" in msg
    assert "unknown" in msg
