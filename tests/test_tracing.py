"""OTel tracing wrapper (no-op when disabled/uninstalled) + token metrics."""

from agenttic.server import metrics
from agenttic.server.tracing import setup_tracing, span


def test_tracing_disabled_is_noop():
    assert setup_tracing({"observability": {"otel_enabled": False}}) is False
    with span("x", foo="bar") as s:   # must not raise; yields None when off
        assert s is None


def test_setup_safe_when_otel_missing_but_enabled():
    # otel extra not installed in the default env -> returns False, no crash
    result = setup_tracing({"observability": {"otel_enabled": True}})
    assert result in (True, False)
    with span("y"):
        pass


def test_record_tokens_metric():
    metrics.reset()
    metrics.record_tokens("agent", 100, 50)
    metrics.record_tokens("judge", 200, None)
    out = metrics.render()
    assert 'ascore_llm_tokens_total{component="agent",kind="input"} 100' in out
    assert 'ascore_llm_tokens_total{component="agent",kind="output"} 50' in out
    assert 'ascore_llm_tokens_total{component="judge",kind="input"} 200' in out


def test_record_tokens_ignores_zero_none():
    metrics.reset()
    metrics.record_tokens("agent", None, 0)
    assert "ascore_llm_tokens_total" not in metrics.render()
