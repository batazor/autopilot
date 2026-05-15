"""Unit tests for ``config.logging_otel``.

Pins behaviour we care about:
* No env → no-op (no handler, no SDK touched).
* ``OTEL_EXPORTER_OTLP_ENDPOINT`` set → root logger gets a single
  ``LoggingHandler`` at the configured level.
* ``OTEL_LOGS_EXPORTER=none`` overrides the endpoint and disables.
* Context-vars (inst/player/node/scenario) land on log records as
  ``wos.*`` attributes (where the OTel SDK then maps them into OTLP
  log attributes, which Grafana Cloud surfaces / promotes to Loki labels).
* Idempotent: second ``setup_otel_logging`` call is a no-op.
"""

from __future__ import annotations

import contextlib
import logging
from unittest.mock import patch

import pytest

from config import log_context, logging_otel


@pytest.fixture(autouse=True)
def _reset_otel_logging():
    logging_otel._reset_for_tests()
    # Snapshot + restore root handlers so a test that attaches one doesn't
    # leak into the next.
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        yield
    finally:
        logging_otel._reset_for_tests()
        # Drop anything we added.
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)


def _root_has_loki_handler() -> bool:
    handler = logging_otel._otel_logging_handler_for_tests()
    return handler is not None and handler in logging.getLogger().handlers


def test_noop_when_endpoint_unset() -> None:
    with patch.dict("os.environ", {}, clear=False):
        for k in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_LOGS_EXPORTER", "OTEL_SDK_DISABLED"):
            import os as _os
            _os.environ.pop(k, None)
        logging_otel.setup_otel_logging("test")
        assert not _root_has_loki_handler()


def test_noop_when_logs_exporter_none() -> None:
    with patch.dict(
        "os.environ",
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
            "OTEL_LOGS_EXPORTER": "none",
        },
        clear=False,
    ):
        logging_otel.setup_otel_logging("test")
        assert not _root_has_loki_handler()


def test_noop_when_sdk_disabled() -> None:
    with patch.dict(
        "os.environ",
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
            "OTEL_SDK_DISABLED": "true",
        },
        clear=False,
    ):
        logging_otel.setup_otel_logging("test")
        assert not _root_has_loki_handler()


def test_handler_attached_when_endpoint_set() -> None:
    with patch.dict(
        "os.environ",
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"},
        clear=False,
    ):
        import os as _os
        _os.environ.pop("OTEL_LOGS_EXPORTER", None)
        _os.environ.pop("OTEL_SDK_DISABLED", None)
        logging_otel.setup_otel_logging("test")
        assert _root_has_loki_handler()


def test_setup_is_idempotent() -> None:
    with patch.dict(
        "os.environ",
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"},
        clear=False,
    ):
        import os as _os
        _os.environ.pop("OTEL_LOGS_EXPORTER", None)
        _os.environ.pop("OTEL_SDK_DISABLED", None)
        logging_otel.setup_otel_logging("test")
        first = logging_otel._otel_logging_handler_for_tests()
        logging_otel.setup_otel_logging("test-again")
        second = logging_otel._otel_logging_handler_for_tests()
        # Same instance — second call short-circuited on the module flag.
        assert first is second


def test_level_from_env() -> None:
    with patch.dict(
        "os.environ",
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
            "WOS_OTEL_LOG_LEVEL": "WARNING",
        },
        clear=False,
    ):
        import os as _os
        _os.environ.pop("OTEL_LOGS_EXPORTER", None)
        _os.environ.pop("OTEL_SDK_DISABLED", None)
        logging_otel.setup_otel_logging("test")
        handler = logging_otel._otel_logging_handler_for_tests()
        assert handler is not None
        assert handler.level == logging.WARNING


def test_attrs_filter_lifts_context_to_wos_namespace() -> None:
    """The filter copies ``record.scenario`` etc. into ``record.wos.scenario``.

    OTel's ``LoggingHandler`` then propagates these onto the OTLP log record,
    where Grafana Cloud's Loki integration can use them as labels.
    """
    filt = logging_otel._OtelLogAttrsFilter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=None,
        exc_info=None,
    )
    record.inst = "bs1"
    record.player = "alice"
    record.node = "city_main"
    record.scenario = "claim.daily"

    assert filt.filter(record)

    assert getattr(record, "wos.inst") == "bs1"
    assert getattr(record, "wos.player") == "alice"
    assert getattr(record, "wos.node") == "city_main"
    assert getattr(record, "wos.scenario") == "claim.daily"


def test_attrs_filter_skips_placeholders() -> None:
    """Empty strings and the ``-`` sentinel from LogContextFilter are dropped."""
    filt = logging_otel._OtelLogAttrsFilter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=None,
        exc_info=None,
    )
    record.inst = "-"
    record.player = ""
    record.node = "city_main"
    record.scenario = "-"

    filt.filter(record)

    assert not hasattr(record, "wos.inst")
    assert not hasattr(record, "wos.player")
    assert getattr(record, "wos.node") == "city_main"
    assert not hasattr(record, "wos.scenario")


def test_log_context_filter_now_carries_scenario() -> None:
    """``LogContextFilter`` must surface the new ``scenario`` contextvar."""
    log_context.set_log_context(scenario="hero.recall")
    filt = log_context.LogContextFilter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=None,
        exc_info=None,
    )
    filt.filter(record)
    assert record.scenario == "hero.recall"


def test_bind_log_context_reverts_on_exit() -> None:
    """Pin the contract that fixed the leak: scoped bind restores the prior value."""
    log_context.set_log_context(player="alice", scenario="claim.daily")
    assert log_context._player.get() == "alice"
    assert log_context._scenario.get() == "claim.daily"

    with log_context.bind_log_context(player="bob", scenario="hero.recall"):
        assert log_context._player.get() == "bob"
        assert log_context._scenario.get() == "hero.recall"

    # Restored — the next iteration must NOT inherit "bob" / "hero.recall".
    assert log_context._player.get() == "alice"
    assert log_context._scenario.get() == "claim.daily"


def test_bind_log_context_reverts_on_exception() -> None:
    """Even if the body raises, the prior value is restored."""
    log_context.set_log_context(player="alice")
    with contextlib.suppress(RuntimeError), log_context.bind_log_context(player="bob"):
        raise RuntimeError("boom")
    assert log_context._player.get() == "alice"


def test_bind_log_context_skips_none_args() -> None:
    """``None`` args don't bind anything — pre-existing values stay unchanged."""
    log_context.set_log_context(player="alice", scenario="claim.daily")
    with log_context.bind_log_context(player="bob"):
        assert log_context._scenario.get() == "claim.daily"
    assert log_context._player.get() == "alice"
