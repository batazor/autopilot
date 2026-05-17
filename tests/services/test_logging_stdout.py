from __future__ import annotations

import logging

from config.log_context import LogContextFilter


def test_log_context_filter_supplies_otel_defaults_for_stdout_format() -> None:
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=None,
        exc_info=None,
    )

    assert LogContextFilter().filter(record)

    assert record.otelTraceID == "0"  # ty: ignore[unresolved-attribute]
    assert record.otelSpanID == "0"  # ty: ignore[unresolved-attribute]
    assert record.otelTraceSampled is False  # ty: ignore[unresolved-attribute]

