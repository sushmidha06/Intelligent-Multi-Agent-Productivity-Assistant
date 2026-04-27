"""Tests for the observability module: JSON logs, request id, metrics."""

import json
import logging

from app.observability import (
    JsonFormatter,
    Metrics,
    configure_logging,
    request_id_ctx,
    user_id_ctx,
)


def test_json_formatter_emits_valid_json():
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    out = JsonFormatter().format(rec)
    payload = json.loads(out)
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "t"


def test_json_formatter_includes_context_vars():
    request_id_ctx.set("req-xyz")
    user_id_ctx.set("user-1")
    try:
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "x", (), None)
        payload = json.loads(JsonFormatter().format(rec))
        assert payload["request_id"] == "req-xyz"
        assert payload["user_id"] == "user-1"
    finally:
        request_id_ctx.set("-")
        user_id_ctx.set("-")


def test_json_formatter_attaches_extras():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "x", (), None)
    rec.foo = "bar"  # type: ignore[attr-defined]
    rec.count = 7  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["foo"] == "bar"
    assert payload["count"] == 7


def test_configure_logging_replaces_handlers():
    configure_logging("DEBUG")
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0].formatter, JsonFormatter)


# ---------- Metrics ----------

def test_metrics_counter_accumulates():
    m = Metrics()
    m.incr("hits")
    m.incr("hits", value=2)
    out = m.render_prometheus()
    assert "hits 3" in out


def test_metrics_counter_with_labels():
    m = Metrics()
    m.incr("requests_total", path="/chat", status="200")
    m.incr("requests_total", path="/chat", status="200")
    m.incr("requests_total", path="/chat", status="500")
    out = m.render_prometheus()
    assert 'requests_total{path="/chat",status="200"} 2' in out
    assert 'requests_total{path="/chat",status="500"} 1' in out


def test_metrics_observe_emits_count_sum_avg_max():
    m = Metrics()
    m.observe("latency", 0.1)
    m.observe("latency", 0.3)
    out = m.render_prometheus()
    assert "latency_count 2" in out
    assert "latency_sum 0.4" in out
    assert "latency_avg 0.2" in out
    assert "latency_max 0.3" in out


def test_metrics_histogram_capped_at_1000():
    m = Metrics()
    for i in range(1500):
        m.observe("x", float(i))
    # Internal cap — verify via count.
    out = m.render_prometheus()
    assert "x_count 1000" in out
