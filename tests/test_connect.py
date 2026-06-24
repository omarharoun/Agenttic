"""The "Connect your agent" step — the safe HTTP/webhook way to point Agenttic
at a live agent so the Safety Battery can test it.

Covers:
* the pure request/response mapping + presets (openai / generic / custom),
* connection config persisting with the auth header ENCRYPTED + masked (never
  returned, never logged),
* SSRF-blocked URLs rejected at SAVE time and at REQUEST time,
* the test-connection probe: mapped reply on a loopback stub, and clear fix-it
  errors (bad mapping, SSRF-blocked),
* the X-Agenttic-Safety-Test header on every request,
* the consent gate blocking a scan until the user confirms authorization, then a
  full keyless scan over a loopback endpoint grading A.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from ascore import connect
from ascore.connect import (
    Mapping, build_request_body, extract_reply, render_prompt,
)
from ascore.registry.sqlite_store import Registry
from ascore.security import UnsafeURLError
from ascore.server.app import create_app
from ascore.server.connections import ConnectionStore

CFG = {"security": {"blackbox_block_private": False},
       "connect": {"rate_limit_s": 0, "timeout_s": 5}}

REFUSAL = "I'm sorry, I can't help with that — it would be unsafe."


# --------------------------------------------------------------------------- #
# 1. Pure mapping + presets.
# --------------------------------------------------------------------------- #


class TestMapping:
    def test_generic_preset_defaults(self):
        m = Mapping.resolve("generic")
        assert build_request_body(m, "hi") == {"input": "hi"}
        assert extract_reply(m, {"output": "yo"}) == "yo"

    def test_generic_custom_field_and_path(self):
        m = Mapping.resolve("custom", request_field="prompt", response_path="data.reply")
        assert build_request_body(m, "hi") == {"prompt": "hi"}
        assert extract_reply(m, {"data": {"reply": "yo"}}) == "yo"

    def test_openai_preset_maps_correctly(self):
        m = Mapping.resolve("openai", model="claude-x")
        assert build_request_body(m, "hi") == {
            "model": "claude-x",
            "messages": [{"role": "user", "content": "hi"}]}
        assert m.response_path == "choices[0].message.content"
        body = {"choices": [{"message": {"content": "the reply"}}]}
        assert extract_reply(m, body) == "the reply"

    def test_openai_preset_default_model(self):
        m = Mapping.resolve("openai")
        assert build_request_body(m, "x")["model"]  # a non-empty default

    def test_render_prompt_joins_request_and_content(self):
        # injection cases carry both request + content; both must reach the agent
        out = render_prompt({"request": "Summarize this.", "content": "DOC <inject>"})
        assert "Summarize this." in out and "DOC <inject>" in out

    def test_render_prompt_request_only(self):
        assert render_prompt({"request": "hello"}) == "hello"

    def test_bad_mapping_raises_friendly(self):
        m = Mapping.resolve("openai")
        with pytest.raises(connect.MappingError) as ei:
            extract_reply(m, {"unexpected": "shape"})
        assert "choices[0].message.content" in str(ei.value)


# --------------------------------------------------------------------------- #
# 2. ConnectionStore: persistence, encryption, masking, consent.
# --------------------------------------------------------------------------- #


class TestConnectionStore:
    def test_persists_with_auth_encrypted_and_masked(self, tmp_path):
        reg = Registry(tmp_path / "c.db")
        store = ConnectionStore(reg.engine, CFG)
        SECRET = "Bearer sk-SUPERSECRET-9999"
        status = store.save("default", endpoint_url="https://agent.example/chat",
                            agent_name="Bot", preset="openai", model="m",
                            auth_header_name="Authorization", auth_header_value=SECRET,
                            consent=False)
        # status NEVER carries the secret — only a masked last4
        assert status["connected"] is True
        assert status["auth_set"] is True
        assert "9999" in status["auth_masked"]
        assert SECRET not in json.dumps(status)
        # the raw secret is not stored in the clear; ciphertext decrypts back
        from sqlmodel import Session, select
        from ascore.registry.sqlite_store import AgentConnectionRow
        with Session(reg.engine) as s:
            row = s.exec(select(AgentConnectionRow)).first()
        assert SECRET not in row.auth_ciphertext
        assert row.auth_ciphertext  # something was stored
        # get() (server-side) decrypts for adapter building
        conn = store.get("default")
        assert conn.auth_header_value == SECRET
        assert conn.auth_headers() == {"Authorization": SECRET}

    def test_update_preserves_secret_when_blank(self, tmp_path):
        reg = Registry(tmp_path / "c.db")
        store = ConnectionStore(reg.engine, CFG)
        store.save("default", endpoint_url="https://a.example/x",
                   auth_header_name="Authorization", auth_header_value="Bearer keep-1234")
        # re-save WITHOUT the secret (e.g. editing the URL) keeps it
        store.save("default", endpoint_url="https://a.example/y",
                   auth_header_name="Authorization", auth_header_value="")
        conn = store.get("default")
        assert conn.auth_header_value == "Bearer keep-1234"
        assert conn.endpoint_url.endswith("/y")

    def test_ssrf_blocked_url_rejected_at_save(self, tmp_path):
        reg = Registry(tmp_path / "c.db")
        # block_private ON: a cloud-metadata IP must be refused at save time
        store = ConnectionStore(reg.engine, {"security": {"blackbox_block_private": True}})
        with pytest.raises(UnsafeURLError):
            store.save("default", endpoint_url="http://169.254.169.254/latest/meta-data")
        with pytest.raises(UnsafeURLError):
            store.save("default", endpoint_url="file:///etc/passwd")

    def test_consent_gate_stamps_timestamp(self, tmp_path):
        reg = Registry(tmp_path / "c.db")
        store = ConnectionStore(reg.engine, CFG)
        store.save("default", endpoint_url="https://a.example/x")
        assert store.status("default")["consent"] is False
        st = store.set_consent("default", True)
        assert st["consent"] is True and st["consent_at"]


# --------------------------------------------------------------------------- #
# Loopback stub: a tiny agent supporting BOTH generic and openai shapes. It
# records the headers it received so we can assert the safety-test header.
# --------------------------------------------------------------------------- #


def _make_stub():
    received_headers: list[dict] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            received_headers.append({k: v for k, v in self.headers.items()})
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            req = json.loads(raw or b"{}")
            # echo a safe refusal; respond in the shape the request implies
            if "messages" in req:                       # openai-compatible
                body = {"choices": [{"message": {"role": "assistant",
                                                 "content": REFUSAL}}]}
            else:                                        # generic {input}->{output}
                body = {"output": REFUSAL}
            out = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):  # silence
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/chat"
    return srv, url, received_headers


# --------------------------------------------------------------------------- #
# 3. probe() against a real loopback endpoint + request-time SSRF.
# --------------------------------------------------------------------------- #


class TestProbe:
    def test_probe_returns_mapped_reply_generic(self):
        srv, url, headers = _make_stub()
        try:
            conn = connect.ConnectionConfig(endpoint_url=url, preset="generic")
            adapter = connect.build_connection_adapter(CFG, conn, allow_private=True)
            res = connect.probe(adapter)
            assert res.ok is True
            assert res.reply == REFUSAL
            # every request carried the safety-test header
            assert headers[-1].get("X-Agenttic-Safety-Test") == "true"
        finally:
            srv.shutdown()

    def test_probe_returns_mapped_reply_openai(self):
        srv, url, _ = _make_stub()
        try:
            conn = connect.ConnectionConfig(endpoint_url=url, preset="openai")
            adapter = connect.build_connection_adapter(CFG, conn, allow_private=True)
            res = connect.probe(adapter)
            assert res.ok is True and res.reply == REFUSAL
        finally:
            srv.shutdown()

    def test_probe_bad_mapping_clear_error(self):
        srv, url, _ = _make_stub()
        try:
            # generic stub returns {"output": ...} but we point the path at a
            # field that isn't there
            conn = connect.ConnectionConfig(endpoint_url=url, preset="custom",
                                            response_path="result.text")
            adapter = connect.build_connection_adapter(CFG, conn, allow_private=True)
            res = connect.probe(adapter)
            assert res.ok is False
            assert "result.text" in res.error
        finally:
            srv.shutdown()

    def test_probe_ssrf_blocked_at_request_time(self):
        # block_private ON -> the request-time guard refuses the loopback dial
        srv, url, _ = _make_stub()
        try:
            conn = connect.ConnectionConfig(endpoint_url=url, preset="generic")
            adapter = connect.build_connection_adapter(
                {"security": {"blackbox_block_private": True}, "connect": {}},
                conn, allow_private=False)
            res = connect.probe(adapter)
            assert res.ok is False
            assert "private" in res.error.lower() or "ssrf" in res.error.lower()
        finally:
            srv.shutdown()


# --------------------------------------------------------------------------- #
# 4. HTTP surface: save / test / consent gate / full scan.
# --------------------------------------------------------------------------- #


CONFIG_YAML = """\
models: {agent_default: agent-model, judge_executor: judge-x, judge_strong: judge-model, judge_light: judge-light, generator: gen}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
security: {blackbox_block_private: false}
connect: {rate_limit_s: 0, timeout_s: 5}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm", required: true, allow_signup: true, signup_role: operator, session_secret: testsecret}
"""


@pytest.fixture
def ctx(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                                  "c": tmp_path / "c"})
    reg = Registry(tmp_path / "a.db")
    client = TestClient(create_app(str(cfg), registry=reg))
    with client as c:
        yield c


def _adm():
    return {"Authorization": "Bearer adm"}


def _poll(client, scan_id, tries=300):
    body = {}
    for _ in range(tries):
        body = client.get(f"/api/scan/{scan_id}", headers=_adm()).json()
        if body["status"] != "running":
            return body
        time.sleep(0.03)
    raise AssertionError(f"scan did not finish: {body}")


class TestConnectHttp:
    def test_save_status_masked_and_no_secret_in_responses_or_logs(self, ctx, caplog):
        import logging
        SECRET = "Bearer sk-DONOTLEAK-7777"
        with caplog.at_level(logging.DEBUG):
            r = ctx.put("/api/connect", json={
                "endpoint_url": "https://agent.example/v1/chat",
                "agent_name": "My bot", "preset": "openai", "model": "m",
                "auth_header_name": "Authorization", "auth_header_value": SECRET},
                headers=_adm())
            assert r.status_code == 200, r.text
            g = ctx.get("/api/connect", headers=_adm())
        assert g.json()["connected"] is True
        assert g.json()["auth_set"] is True
        assert "7777" in g.json()["auth_masked"]
        # the raw secret must not appear in any response body or log line
        assert "DONOTLEAK" not in r.text and "DONOTLEAK" not in g.text
        assert "DONOTLEAK" not in caplog.text

    def test_save_rejects_ssrf_url(self, ctx):
        # need block_private ON for this assertion — use a fresh app config? The
        # ctx app has block_private false; instead assert the metadata IP via a
        # store with block_private on is covered in TestConnectionStore. Here we
        # assert a non-http scheme is always rejected regardless of config.
        r = ctx.put("/api/connect", json={"endpoint_url": "file:///etc/passwd"},
                    headers=_adm())
        assert r.status_code == 400
        assert "allowed" in r.text.lower() or "scheme" in r.text.lower()

    def test_test_connection_returns_reply(self, ctx):
        srv, url, headers = _make_stub()
        try:
            r = ctx.post("/api/connect/test", json={
                "endpoint_url": url, "preset": "generic"}, headers=_adm())
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["reply"] == REFUSAL
            assert headers[-1].get("X-Agenttic-Safety-Test") == "true"
        finally:
            srv.shutdown()

    def test_test_connection_bad_mapping_error(self, ctx):
        srv, url, _ = _make_stub()
        try:
            r = ctx.post("/api/connect/test", json={
                "endpoint_url": url, "preset": "custom",
                "response_path": "nope.field"}, headers=_adm())
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is False and "nope.field" in body["error"]
        finally:
            srv.shutdown()

    def test_scan_blocked_without_consent_then_runs(self, ctx):
        srv, url, headers = _make_stub()
        try:
            # save the connection WITHOUT consent
            ctx.put("/api/connect", json={"endpoint_url": url, "preset": "generic",
                                          "agent_name": "loopback-bot"}, headers=_adm())
            blocked = ctx.post("/api/scan", json={"target": "connection"},
                               headers=_adm())
            assert blocked.status_code == 403
            assert "authoriz" in blocked.text.lower()

            # confirm authorization, then the scan runs keyless and grades A
            ctx.post("/api/connect/consent", json={"consent": True}, headers=_adm())
            r = ctx.post("/api/scan", json={"target": "connection"}, headers=_adm())
            assert r.status_code == 200, r.text
            done = _poll(ctx, r.json()["scan_id"])
            assert done["status"] == "done", done
            assert done["result"]["grade"] == "A"
            assert done["result"]["cost_usd"] == 0.0   # keyless: no Agenttic spend
            # the scan traffic carried the safety-test header on every request
            assert all(h.get("X-Agenttic-Safety-Test") == "true" for h in headers)
            assert len(headers) >= 14                  # the full battery was sent
        finally:
            srv.shutdown()

    def test_scan_connection_requires_saved_connection(self, ctx):
        r = ctx.post("/api/scan", json={"target": "connection"}, headers=_adm())
        assert r.status_code == 400 and "connect" in r.text.lower()
