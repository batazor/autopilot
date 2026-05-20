from __future__ import annotations

from config.w3c_traceparent import trace_id_hex_from_carrier, w3c_trace_id_hex


def test_w3c_trace_id_hex_parses_header() -> None:
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert w3c_trace_id_hex(tp) == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_w3c_trace_id_hex_rejects_malformed() -> None:
    assert w3c_trace_id_hex("") is None
    assert w3c_trace_id_hex("nope") is None
    assert w3c_trace_id_hex("00-short-00f067aa0ba902b7-01") is None
    assert w3c_trace_id_hex("00-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz-00f067aa0ba902b7-01") is None


def test_trace_id_hex_from_carrier_prefers_direct() -> None:
    assert trace_id_hex_from_carrier({"trace_id": "abc123"}) == "abc123"


def test_trace_id_hex_from_carrier_parses_traceparent() -> None:
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert trace_id_hex_from_carrier({"traceparent": tp}) == "4bf92f3577b34da6a3ce929d0e0e4736"
