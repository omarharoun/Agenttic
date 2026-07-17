"""The PUBLIC, UNAUTHENTICATED intake bot — the landing-page scan-intake
assistant powered by the SAME CopilotAgent loop + SSE machinery as the authed
Copilot, but on the server key, the public-demo tenant, the public intake
persona, and a STRICT demo-only tool allowlist.

Follows tests/test_scan.py::TestPublicDemoHttp style: everything is anonymous
(no auth header), with an injected fake Anthropic client via
``create_app(clients=...)`` so no network is touched. Covers:

* the public status endpoint reports availability + model, unauthenticated;
* an anonymous chat turn streams the SAME SSE events (session/token/tool/
  approval_required/done) as the authed route;
* the free demo scan is proposed (write tool → approval_required) and, once
  approved via the public approve route, actually starts a demo scan;
* the public rate-limit/daily-cap fails closed with the shared 402/429 card;
* a platform/certification tool is NOT reachable on the public surface — even if
  the model names one, dispatch default-denies it (never a tenant/platform tool).
"""

from __future__ import annotations

import json
import time
import types

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace as NS

from agenttic.copilot import credits
from agenttic.registry.sqlite_store import Registry
from agenttic.server import abuse
from agenttic.server.app import create_app
from agenttic.server.routes import copilot as copilot_route
from agenttic.server.routes.scan import PUBLIC_DEMO_TENANT

REFUSAL = "I'm sorry, I can't help with that — it would be unsafe."


# --------------------------------------------------------------------------- #
# Fakes: a scripted streaming+tool-use client for the BOT, and a create()-only
# client for the demo AGENT the bot's start_demo_scan tool actually runs.
# --------------------------------------------------------------------------- #


def _text_block(t):
    return NS(type="text", text=t)


def _tool_block(name, inp, id_="tu_1"):
    return NS(type="tool_use", name=name, input=inp, id=id_)


def turn_text(t, usage=(10, 5)):
    return {"chunks": [t], "content": [_text_block(t)], "stop": "end_turn",
            "usage": usage}


def turn_tool(name, inp=None, say="", usage=(10, 5)):
    content = ([_text_block(say)] if say else []) + [_tool_block(name, inp or {})]
    return {"chunks": [say] if say else [], "content": content,
            "stop": "tool_use", "usage": usage}


class _FakeStream:
    def __init__(self, turn):
        self._turn = turn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        yield from self._turn["chunks"]

    def get_final_message(self):
        u = self._turn["usage"]
        return NS(content=self._turn["content"], stop_reason=self._turn["stop"],
                  usage=NS(input_tokens=u[0], output_tokens=u[1]))


class _FakeMessages:
    def __init__(self, outer):
        self.outer = outer

    def stream(self, **kwargs):
        self.outer.requests.append(kwargs)
        if not self.outer.turns:
            raise AssertionError("FakeBotClient ran out of scripted turns")
        return _FakeStream(self.outer.turns.pop(0))


class FakeBotClient:
    """Scripted streaming tool-use client for the intake bot (the copilot)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.messages = _FakeMessages(self)


class _SafeAgentClient:
    """create()-only demo agent that refuses everything (grades A, no gaps)."""

    class _Messages:
        def create(self, **kwargs):
            block = types.SimpleNamespace(type="text", text=REFUSAL)
            usage = types.SimpleNamespace(input_tokens=10, output_tokens=8)
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[block], usage=usage)

    def __init__(self):
        self.messages = self._Messages()


# --------------------------------------------------------------------------- #
# App fixture. Public surface: NO auth, injected clients live on app.state.
# --------------------------------------------------------------------------- #


CONFIG_YAML = """\
models: {agent_default: agent-model, judge_executor: judge-x, judge_strong: judge-model, judge_light: judge-light, generator: gen}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
security: {blackbox_block_private: false}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm", required: true, allow_signup: true, signup_role: operator, session_secret: testsecret}
copilot: {model: claude-haiku-4-5, rate_limit_per_minute: %(rlpm)s, daily_message_cap_per_user: %(cap)s, daily_message_cap_global: %(gcap)s}
abuse: {demo: {per_ip_per_minute: %(ip)s, global_per_day: %(gday)s}}
certification: {profiles: {cert-agent-safety-v1: {required_domains: [harm_refusal], thresholds: {}}}}
"""


def _mk_app(tmp_path, turns, *, ip=0, gday=0, cap=0, gcap=0, rlpm=100):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML % {"db": tmp_path / "p.db", "r": tmp_path / "r",
                                  "c": tmp_path / "cal", "ip": ip, "gday": gday,
                                  "cap": cap, "gcap": gcap, "rlpm": rlpm})
    reg = Registry(tmp_path / "p.db")
    bot = FakeBotClient(turns)
    # copilot → the intake bot; agent/judge → the demo scan the bot may start.
    clients = {"copilot": bot, "agent": _SafeAgentClient(),
               "judge": _SafeAgentClient()}
    app = create_app(str(cfg), registry=reg, clients=clients)
    copilot_route._RL._hits.clear()
    abuse.reset_abuse()
    credits.reset_daily_cap()
    return app, bot


def _events(resp):
    out, ev = [], None
    for line in resp.text.splitlines():
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            out.append((ev, line[6:].replace("\\n", "\n").replace("\\\\", "\\")))
    return out


def _kind(events, kind):
    return [d for e, d in events if e == kind]


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #


class TestPublicCopilotStatus:
    def test_status_is_unauthenticated_and_reports_model(self, tmp_path):
        app, _ = _mk_app(tmp_path, [])
        with TestClient(app) as c:
            r = c.get("/api/public/copilot/status")   # no auth header
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["available"] is True          # injected client present
            assert body["model"] == "claude-haiku-4-5"

    def test_status_unavailable_without_server_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("COPILOT_ANTHROPIC_KEY", raising=False)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(CONFIG_YAML % {"db": tmp_path / "q.db", "r": tmp_path / "r",
                                      "c": tmp_path / "cal", "ip": 0, "gday": 0,
                                      "cap": 0, "gcap": 0, "rlpm": 100})
        reg = Registry(tmp_path / "q.db")
        app = create_app(str(cfg), registry=reg)      # NO injected client
        with TestClient(app) as c:
            r = c.get("/api/public/copilot/status")
            assert r.status_code == 200
            assert r.json()["available"] is False


class TestPublicCopilotChat:
    def test_anonymous_chat_streams_sse_events(self, tmp_path):
        app, bot = _mk_app(tmp_path, [
            turn_tool("preview_scan"),
            turn_text("A scan grades four safety dimensions."),
        ])
        with TestClient(app) as c:
            r = c.post("/api/public/copilot/chat",   # NO auth header
                       json={"message": "what does a scan check?"})
            assert r.status_code == 200, r.text
            events = _events(r)
        # SAME protocol: a session event with a session_id, then tokens + done
        sess = json.loads(_kind(events, "session")[0])
        assert sess["session_id"].startswith("cop_")
        tool_evs = [json.loads(d) for d in _kind(events, "tool")]
        assert any(t["tool"] == "preview_scan" and t["phase"] == "done"
                   and t["ok"] for t in tool_evs)
        assert "four safety dimensions" in "".join(_kind(events, "token"))
        assert json.loads(_kind(events, "done")[0])["status"] == "ready"
        # the bot was driven with the PUBLIC persona + the restricted tool set
        req = bot.requests[0]
        assert "public site" in req["system"] or "VISITOR" in req["system"]
        assert {t["name"] for t in req["tools"]} == {
            "preview_scan", "start_demo_scan", "get_scan_status",
            "get_scan_findings"}

    def test_demo_scan_proposed_then_approved_runs(self, tmp_path):
        """start_demo_scan is a WRITE tool: the loop pauses with approval_required
        (same as authed), and the public approve route resumes + actually starts
        the demo scan on the server key."""
        app, _ = _mk_app(tmp_path, [
            turn_tool("start_demo_scan", say="I'll run the free demo."),
            turn_text("The demo scan is running — I'll follow its progress."),
        ])
        with TestClient(app) as c:
            r1 = c.post("/api/public/copilot/chat",
                        json={"message": "run the demo"})
            ev1 = _events(r1)
            appr = _kind(ev1, "approval_required")
            assert appr, "expected an approval_required for start_demo_scan"
            card = json.loads(appr[0])
            assert card["tool"] == "start_demo_scan"
            assert "cost_note" in card["card"]
            sid = json.loads(_kind(ev1, "session")[0])["session_id"]
            assert json.loads(_kind(ev1, "done")[0])["status"] == \
                "awaiting_approval"

            r2 = c.post("/api/public/copilot/approve",
                        json={"session_id": sid, "approved": True})
            ev2 = _events(r2)
            tool_done = [json.loads(d) for d in _kind(ev2, "tool")]
            started = [t for t in tool_done
                       if t["tool"] == "start_demo_scan" and t["ok"]]
            assert started, f"demo scan did not start: {tool_done}"
            # the tool fed a real scan_id back to the model
            assert "running" in "".join(_kind(ev2, "token"))

            # the background scan actually runs (scheduled on app.state.
            # workspaces.loop) on the server key + public-demo tenant, and is
            # pollable through the public demo-scan surface — with NO auth.
            from agenttic.server.routes import scan as scan_mod
            demo_ids = [sid for sid, j in scan_mod._JOBS.items()
                        if j.tenant == PUBLIC_DEMO_TENANT]
            assert demo_ids, "no public-demo scan job was created"
            scan_id = demo_ids[-1]
            body = None
            for _i in range(200):
                body = c.get(f"/api/public/demo-scan/{scan_id}").json()
                if body["status"] != "running":
                    break
                time.sleep(0.03)
            assert body and body["status"] == "done", body
            assert body["result"]["grade"] == "A"
            assert body["certificate"] is None          # demo mints no cert
            # tenant-isolated from the authed surface
            assert c.get(f"/api/scan/{scan_id}",
                         headers={"Authorization": "Bearer adm"}
                         ).status_code == 404

    def test_session_resumes_by_id_no_cookies(self, tmp_path):
        app, _ = _mk_app(tmp_path, [
            turn_text("Hi — tell me what your agent does."),
            turn_text("Got it — I can run a free demo scan."),
        ])
        with TestClient(app) as c:
            r1 = c.post("/api/public/copilot/chat", json={"message": "hi"})
            sid = json.loads(_kind(_events(r1), "session")[0])["session_id"]
            # continue the anonymous conversation with just the session_id
            r2 = c.post("/api/public/copilot/chat",
                        json={"message": "it reads emails", "session_id": sid})
            assert r2.status_code == 200
            assert json.loads(_kind(_events(r2), "session")[0])["session_id"] \
                == sid


class TestPublicCopilotGuardrails:
    def test_platform_tool_is_not_reachable(self, tmp_path):
        """Even if the model names a tenant/platform/certification tool, the
        public surface default-denies it at dispatch — it never runs."""
        app, _ = _mk_app(tmp_path, [
            turn_tool("start_certification",
                      {"agent_id": "ref-agent",
                       "profile_id": "cert-agent-safety-v1"},
                      say="Trying to certify."),
            turn_text("I can't do that here — I can run a free demo scan."),
        ])
        with TestClient(app) as c:
            r = c.post("/api/public/copilot/chat",
                       json={"message": "certify ref-agent"})
            events = _events(r)
        # NOT proposed as a write action (no approval card for it)
        assert not _kind(events, "approval_required")
        # refused at dispatch, surfaced as a failed tool event
        tool_evs = [json.loads(d) for d in _kind(events, "tool")]
        refused = [t for t in tool_evs
                   if t["tool"] == "start_certification" and not t["ok"]]
        assert refused, f"start_certification should be refused: {tool_evs}"
        # never entered the model's tool schema list in the first place
        # (the bot only ever saw the 4 public tools) — covered above.

    def test_rate_limit_trips_with_429_card(self, tmp_path):
        # The CHAT limiter (copilot.rate_limit_per_minute) governs chat turns, NOT
        # the tight demo-scan ceiling. rlpm=1: first chat passes, second trips.
        app, _ = _mk_app(tmp_path, [turn_text("one"), turn_text("two")], rlpm=1)
        with TestClient(app) as c:
            r1 = c.post("/api/public/copilot/chat", json={"message": "a"})
            assert r1.status_code == 200
            r2 = c.post("/api/public/copilot/chat", json={"message": "b"})
            assert r2.status_code == 429, r2.text
            detail = r2.json()["detail"]
            # same {code, message, action} card shape as the authed route
            assert detail["code"] == "rate_limited"
            assert "action" in detail and detail["message"]

    def test_demo_ceiling_does_not_throttle_chat(self, tmp_path):
        # Regression: the tight demo-scan limit (per-IP 2/min + global/day) must
        # NEVER govern chat turns — a visitor was hitting "too fast" after two
        # messages because chat borrowed the demo ceiling. With the demo limit at
        # its most aggressive (per-IP 1/min, global 1/day) and a generous chat
        # limiter, several chat turns in a row all succeed.
        turns = [turn_text(str(i)) for i in range(5)]
        app, _ = _mk_app(tmp_path, turns, ip=1, gday=1, rlpm=100)
        with TestClient(app) as c:
            for i in range(5):
                r = c.post("/api/public/copilot/chat", json={"message": f"m{i}"})
                assert r.status_code == 200, (i, r.text)

    def test_daily_cap_trips_with_402_card(self, tmp_path):
        # global daily message cap = 1: second message fails closed with 402.
        app, _ = _mk_app(tmp_path, [turn_text("one"), turn_text("two")], gcap=1)
        with TestClient(app) as c:
            r1 = c.post("/api/public/copilot/chat", json={"message": "a"})
            assert r1.status_code == 200
            r2 = c.post("/api/public/copilot/chat", json={"message": "b"})
            assert r2.status_code == 402, r2.text
            detail = r2.json()["detail"]
            assert detail["code"] == "daily_limit"
            assert detail["message"]

    def test_demo_ceiling_bounds_scans_not_chat(self, tmp_path):
        # The demo ceiling (abuse.demo.global_per_day = 1) bounds the EXPENSIVE
        # demo *scan*, not chat: the first demo scan runs, the second is refused
        # 429 — while a chat turn stays available throughout (independent limit).
        app, _ = _mk_app(tmp_path, [turn_text("hi")], gday=1)
        with TestClient(app) as c:
            s1 = c.post("/api/public/demo-scan", json={})
            assert s1.status_code == 200, s1.text
            s2 = c.post("/api/public/demo-scan", json={})
            assert s2.status_code == 429, s2.text        # scan ceiling bites
            assert s2.json()["detail"]["code"] == "rate_limited"
            # chat is unaffected by the demo scan ceiling
            rc = c.post("/api/public/copilot/chat", json={"message": "a"})
            assert rc.status_code == 200, rc.text
