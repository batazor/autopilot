from __future__ import annotations

from config.trace_links import tempo_trace_url


def test_tempo_trace_url_empty_without_template(monkeypatch) -> None:
    monkeypatch.delenv("WOS_TEMPO_TRACE_URL_TEMPLATE", raising=False)
    monkeypatch.delenv("GRAFANA_TEMPO_TRACE_URL_TEMPLATE", raising=False)
    assert tempo_trace_url("abc") == ""


def test_tempo_trace_url_substitutes_trace_id(monkeypatch) -> None:
    monkeypatch.setenv(
        "WOS_TEMPO_TRACE_URL_TEMPLATE",
        "https://grafana.example/explore?trace={trace_id}",
    )
    assert (
        tempo_trace_url("4bf92f3577b34da6a3ce929d0e0e4736")
        == "https://grafana.example/explore?trace=4bf92f3577b34da6a3ce929d0e0e4736"
    )
