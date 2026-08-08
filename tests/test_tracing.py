"""OTel tracing wrapper (no-op when disabled/uninstalled) + token metrics."""

import pytest

from agenttic.airgap import AirgapEgressError, assert_airgap_safe, egress_self_check
from agenttic.server import metrics
from agenttic.server.tracing import setup_langwatch, setup_tracing, span


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


# --- LangWatch export: opt-in by key, and air-gap sees it -------------------

def test_langwatch_noop_without_key(monkeypatch):
    monkeypatch.delenv("LANGWATCH_API_KEY", raising=False)
    assert setup_langwatch() is False


def test_airgap_blocks_langwatch_export():
    cfg = {"airgap": {"enabled": True, "mock_llm": True}}
    with pytest.raises(AirgapEgressError) as ei:
        assert_airgap_safe(cfg, {"LANGWATCH_API_KEY": "sk-lw-x"})
    assert "langwatch_export" in str(ei.value)


def test_airgap_allows_self_hosted_langwatch():
    cfg = {"airgap": {"enabled": True, "mock_llm": True}}
    env = {"LANGWATCH_API_KEY": "sk-lw-x",
           "LANGWATCH_ENDPOINT": "http://langwatch.internal"}
    names = [o["name"] for o in egress_self_check(cfg, env)["offenders"]]
    assert "langwatch_export" not in names


def test_record_tokens_metric():
    metrics.reset()
    metrics.record_tokens("agent", 100, 50)
    metrics.record_tokens("judge", 200, None)
    out = metrics.render()
    assert 'agenttic_llm_tokens_total{component="agent",kind="input"} 100' in out
    assert 'agenttic_llm_tokens_total{component="agent",kind="output"} 50' in out
    assert 'agenttic_llm_tokens_total{component="judge",kind="input"} 200' in out


def test_record_tokens_ignores_zero_none():
    metrics.reset()
    metrics.record_tokens("agent", None, 0)
    assert "agenttic_llm_tokens_total" not in metrics.render()
