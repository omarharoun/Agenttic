"""Air-gap self-check + no-egress run (SPEC-7 Step 38, T38.5).

Pins the acceptance: air-gap mode boots with egress blocked and completes a full
scan + certification with outbound network disabled; the self-check refuses to
boot (naming the offender) when a required-egress path is present.
"""
from __future__ import annotations

import socket
import tempfile

import pytest
from fastapi.testclient import TestClient

from agenttic.airgap import (
    AirgapEgressError,
    assert_airgap_safe,
    egress_self_check,
    is_airgap,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app


# --- self-check unit contracts ---------------------------------------------

def test_airgap_off_never_blocks():
    rep = egress_self_check({}, {})
    assert rep["enabled"] is False
    # assert_airgap_safe is a no-op when air-gap is off, even with offenders
    assert_airgap_safe({"anthropic": {}}, {})


def test_default_config_offends_on_remote_llm():
    cfg = {"airgap": {"enabled": True}, "observability": {"otel_enabled": False}}
    with pytest.raises(AirgapEgressError) as ei:
        assert_airgap_safe(cfg, {})
    assert "remote_llm" in str(ei.value)


def test_mock_llm_clears_the_llm_offender():
    cfg = {"airgap": {"enabled": True, "mock_llm": True},
           "observability": {"otel_enabled": False}}
    rep = assert_airgap_safe(cfg, {})   # boots
    assert rep["offenders"] == []
    # egress-only features are flagged unavailable, never silently degraded
    names = {u["name"] for u in rep["unavailable"]}
    assert "hosted_public_verify" in names


def test_external_otel_is_an_offender_and_allowlist_clears_it():
    base = {"airgap": {"enabled": True, "mock_llm": True},
            "observability": {"otel_enabled": True,
                              "otel_endpoint": "https://collector.datadoghq.com"}}
    assert "otel_remote_export" in {o["name"] for o in egress_self_check(base, {})["offenders"]}
    base["airgap"]["allow"] = ["otel_remote_export"]
    assert egress_self_check(base, {})["offenders"] == []


def test_in_cluster_endpoints_are_not_egress():
    cfg = {"airgap": {"enabled": True, "mock_llm": True},
           "observability": {"otel_enabled": True,
                             "otel_endpoint": "http://otel.observability.svc:4318"},
           "feeds": {"webhook_urls": ["http://hooks.internal/notify"]},
           "email": {"enabled": True, "smtp": {"host": "10.0.0.5"}}}
    assert egress_self_check(cfg, {})["offenders"] == []


def test_env_flag_enables_airgap():
    assert is_airgap({}, {"AGENTTIC_AIRGAP": "true"}) is True
    assert is_airgap({"airgap": {"enabled": True}}, {}) is True
    assert is_airgap({}, {}) is False


# --- startup: refuse vs boot -----------------------------------------------

def _cfg_file(tmp_path, extra: str) -> str:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
        "harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, "
        f"calibration_dir: {tmp_path / 'c'}}}\n"
        "security: {login_max_attempts: 5, login_lockout_seconds: 900}\n"
        + extra)
    return str(cfg)


def test_server_boots_in_airgap_with_mock_llm(tmp_path):
    cfg = _cfg_file(tmp_path, "airgap: {enabled: true, mock_llm: true}\n")
    reg = Registry(db_path=str(tmp_path / "a.db"))
    with TestClient(create_app(cfg, registry=reg)) as c:
        assert c.get("/health").status_code == 200


def test_server_refuses_boot_in_airgap_with_egress(tmp_path):
    cfg = _cfg_file(
        tmp_path,
        "airgap: {enabled: true}\n"          # no local/mock LLM → remote_llm offends
        "observability: {otel_enabled: false}\n")
    reg = Registry(db_path=str(tmp_path / "a.db"))
    with pytest.raises(AirgapEgressError):
        with TestClient(create_app(cfg, registry=reg)):
            pass


# --- no-egress run: ingest + certify complete with outbound network blocked -

class _EgressBlocked:
    """Block outbound connects to non-local hosts (loopback/private allowed),
    simulating a network with the internet disabled."""

    def __enter__(self):
        self._orig = socket.socket.connect
        self._orig_ex = socket.socket.connect_ex

        def _guard(sock, address, *a, **k):
            host = address[0] if isinstance(address, tuple) else str(address)
            if not _local(host):
                raise OSError(f"egress blocked (air-gap): {host}")
            return self._orig(sock, address, *a, **k)

        def _guard_ex(sock, address, *a, **k):
            host = address[0] if isinstance(address, tuple) else str(address)
            if not _local(host):
                return 1
            return self._orig_ex(sock, address, *a, **k)

        socket.socket.connect = _guard
        socket.socket.connect_ex = _guard_ex
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._orig
        socket.socket.connect_ex = self._orig_ex


def _local(host: str) -> bool:
    import ipaddress
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return host.endswith((".local", ".internal", ".svc"))


def test_ingest_roundtrip_with_egress_blocked():
    from agenttic.ingest import ingest_otlp_payload
    payload = {"resourceSpans": [{"resource": {"attributes": [
        {"key": "agenttic.agent_id", "value": {"stringValue": "ag"}}]},
        "scopeSpans": [{"scope": {"name": "x"}, "spans": [
            {"traceId": "t", "spanId": "s", "name": "chat",
             "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
             "attributes": [{"key": "gen_ai.request.model",
                             "value": {"stringValue": "m"}}]}]}]}]}
    with _EgressBlocked(), tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        rep = ingest_otlp_payload(reg, payload)
        assert rep["trace_count"] == 1


def test_mock_certification_completes_with_egress_blocked():
    """A full offline certification (mock provider) runs to a dossier with the
    internet disabled — the air-gap 'full scan + certification' acceptance."""
    import asyncio

    from agenttic.certification.certify import certify as _certify
    from agenttic.certification.mock_provider import MockAnthropicClient
    from agenttic.config import load_config
    cfg = load_config("config.yaml")
    with _EgressBlocked(), tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        client = MockAnthropicClient()
        res = asyncio.run(_certify(
            cfg, reg, agent_id="airgap-agent", profile_id="cert-agent-safety-v1",
            variant="reference", client=client, judge_client=client))
        assert res.dossier.tier_decision.tier in {"A", "B", "C"}
