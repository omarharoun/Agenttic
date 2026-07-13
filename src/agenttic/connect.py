"""The "Connect your agent" model — the safe HTTP/webhook contract that lets a
normal user point Agenttic at their *live* agent so the existing Safety Battery
can test it.

This module is the pure, dependency-light core (no DB, no crypto, no FastAPI):

* a flexible **request/response mapping** (which JSON field the prompt goes into;
  a path to where the reply text lives in the response — e.g.
  ``choices[0].message.content``),
* two one-click **presets** — ``openai`` (POST ``{model, messages:[…]}`` →
  ``choices[0].message.content``) and ``generic`` (``{input} → {output}``) — plus
  a fully custom mapping,
* an adapter builder (:func:`build_connection_adapter`) that reuses the existing
  black-box HTTP adapter + SSRF guard, and
* a one-shot **probe** (:func:`probe`) behind ``POST /api/connect/test`` that
  sends a single harmless message through the mapping and classifies the result
  (reply / bad URL / auth failed / mapping miss / SSRF-blocked / timeout).

Persistence + secret encryption live in ``server/connections.py``; the HTTP
surface in ``server/routes/connect.py``. We only ever send text prompts and read
text replies — never execute tools — so connecting an agent is non-destructive
by construction.
"""

from __future__ import annotations

import json
import re
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime

#: Every request Agenttic sends to a user's agent carries this header so the
#: operator can recognise (and, if they wish, special-case or rate-limit) our
#: safety traffic. Set on the adapter so BOTH the test-probe and the scan carry it.
SAFETY_TEST_HEADER = "X-Agenttic-Safety-Test"

#: The single harmless message the test-connection probe sends.
PROBE_PROMPT = "Hello, this is a connection test from Agenttic."

PRESET_OPENAI = "openai"
PRESET_GENERIC = "generic"
PRESET_CUSTOM = "custom"
PRESETS = (PRESET_OPENAI, PRESET_GENERIC, PRESET_CUSTOM)

DEFAULT_REQUEST_FIELD = "input"
DEFAULT_RESPONSE_PATH = "output"
OPENAI_RESPONSE_PATH = "choices[0].message.content"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

#: Gentle-traffic defaults (overridable via the ``connect.*`` config section).
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_RATE_LIMIT_S = 0.5


class MappingError(ValueError):
    """The response came back but the configured response path didn't locate the
    reply text. Carries a user-facing message (safe to show in the UI)."""


# --------------------------------------------------------------------------- #
# Request/response mapping.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Mapping:
    """How a battery prompt is placed into the request body and where the reply
    text is read from the response. Build via :meth:`resolve` so a preset fills
    sensible defaults."""

    preset: str = PRESET_GENERIC
    request_field: str = DEFAULT_REQUEST_FIELD   # generic/custom: the prompt field
    response_path: str = DEFAULT_RESPONSE_PATH    # dotted path w/ [i] indices
    model: str = ""                               # openai preset only

    @classmethod
    def resolve(cls, preset: str = PRESET_GENERIC, *, request_field: str = "",
                response_path: str = "", model: str = "") -> "Mapping":
        preset = (preset or PRESET_GENERIC).lower().strip()
        if preset == PRESET_OPENAI:
            return cls(PRESET_OPENAI, "",
                       response_path.strip() or OPENAI_RESPONSE_PATH, model.strip())
        if preset not in (PRESET_GENERIC, PRESET_CUSTOM):
            preset = PRESET_GENERIC
        return cls(preset, request_field.strip() or DEFAULT_REQUEST_FIELD,
                   response_path.strip() or DEFAULT_RESPONSE_PATH, "")

    def public(self) -> dict:
        """Mapping as the API returns it (no secrets here — mapping is config)."""
        return {"preset": self.preset, "request_field": self.request_field,
                "response_path": self.response_path, "model": self.model}


def render_prompt(test_input: dict) -> str:
    """Flatten a Safety-Battery case input into one natural prompt string.

    Battery cases carry ``request`` (the instruction) and sometimes ``content``
    (a document/email/search-result that may hide an injection). A real agent
    expects a single message, so we join them; anything unexpected falls back to
    a JSON dump so no information is silently dropped."""
    req = str(test_input.get("request", "")).strip()
    content = str(test_input.get("content", "")).strip()
    if req and content:
        return f"{req}\n\n{content}"
    if req:
        return req
    if content:
        return content
    return json.dumps(test_input)


def build_request_body(mapping: Mapping, prompt: str) -> dict:
    """The JSON body POSTed to the user's agent for one prompt."""
    if mapping.preset == PRESET_OPENAI:
        return {"model": mapping.model or OPENAI_DEFAULT_MODEL,
                "messages": [{"role": "user", "content": prompt}]}
    return {mapping.request_field or DEFAULT_REQUEST_FIELD: prompt}


_PATH_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")


def _walk(body, path: str):
    """Walk ``path`` (e.g. ``choices[0].message.content``) into ``body``.
    Raises :class:`MappingError` with a friendly message if it can't be found."""
    cur = body
    for m in _PATH_TOKEN.finditer(path):
        idx, key = m.group(1), m.group(2)
        try:
            if idx is not None:
                cur = cur[int(idx)]
            else:
                cur = cur[key]
        except (KeyError, IndexError, TypeError):
            top = list(body) if isinstance(body, dict) else f"a {type(body).__name__}"
            raise MappingError(
                f"couldn't find the reply at '{path}'. The response top level is "
                f"{top}. Adjust the response path to point at the reply text.")
    return cur


def extract_reply(mapping: Mapping, body) -> str:
    """The agent's reply text, located via the mapping's response path."""
    path = mapping.response_path or (
        OPENAI_RESPONSE_PATH if mapping.preset == PRESET_OPENAI
        else DEFAULT_RESPONSE_PATH)
    value = _walk(body, path)
    if value is None:
        raise MappingError(f"the value at '{path}' was null — no reply text.")
    return value if isinstance(value, str) else json.dumps(value)


# --------------------------------------------------------------------------- #
# Connection config (the persisted, decrypted-for-server-use view).
# --------------------------------------------------------------------------- #


@dataclass
class ConnectionConfig:
    """A saved connection, as the server uses it to build an adapter. The
    ``auth_header_value`` here is DECRYPTED (server-side only); the API never
    returns it — see ``server/connections.ConnectionStore.status`` for the masked
    view. ``consent`` gates scanning (the user must confirm authorization)."""

    endpoint_url: str
    agent_name: str = "your-agent"
    preset: str = PRESET_GENERIC
    # blank by default so ``mapping()`` lets the preset fill the right defaults
    # (an openai connection resolves to choices[0].message.content, not "output").
    request_field: str = ""
    response_path: str = ""
    model: str = ""
    auth_header_name: str = ""
    auth_header_value: str = ""        # DECRYPTED — never serialise to the client
    consent: bool = False
    consent_at: datetime | None = None
    updated_at: datetime | None = None

    def mapping(self) -> Mapping:
        return Mapping.resolve(self.preset, request_field=self.request_field,
                               response_path=self.response_path, model=self.model)

    def auth_headers(self) -> dict:
        if self.auth_header_name.strip() and self.auth_header_value.strip():
            return {self.auth_header_name.strip(): self.auth_header_value.strip()}
        return {}


# --------------------------------------------------------------------------- #
# Adapter + gentle-traffic config.
# --------------------------------------------------------------------------- #


def _connect_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("connect", {}) or {}


def gentle_scan_cfg(cfg: dict) -> dict:
    """A shallow copy of ``cfg`` that forces low/sequential concurrency for a scan
    against a user's live agent — we never hammer it (max 1 in-flight request)."""
    out = dict(cfg)
    harness = dict(out.get("harness", {}))
    harness["max_parallel"] = 1
    out["harness"] = harness
    return out


def build_connection_adapter(cfg: dict, conn: ConnectionConfig, *,
                             agent_id: str | None = None,
                             allow_private: bool | None = None):
    """Build the black-box HTTP adapter for a saved connection: the request/
    response mapping, the (decrypted) auth header, the safety-test header, a
    per-request timeout, and a sane inter-request rate limit. SSRF validation is
    enforced inside the adapter's transport at request time."""
    from agenttic.adapters.blackbox_http import BlackBoxHTTPAgent

    cc = _connect_cfg(cfg)
    if allow_private is None:
        allow_private = not cfg.get("security", {}).get("blackbox_block_private", True)
    return BlackBoxHTTPAgent(
        agent_id=agent_id or conn.agent_name or "your-agent",
        url=conn.endpoint_url,
        mapping=conn.mapping(),
        headers=conn.auth_headers() or None,
        allow_private_url=bool(allow_private),
        timeout=float(cc.get("timeout_s", DEFAULT_TIMEOUT_S)),
        min_interval_s=float(cc.get("rate_limit_s", DEFAULT_RATE_LIMIT_S)),
    )


# --------------------------------------------------------------------------- #
# Test-connection probe.
# --------------------------------------------------------------------------- #


@dataclass
class ProbeResult:
    ok: bool
    reply: str = ""
    error: str = ""
    detail: dict = field(default_factory=dict)


def _http_error_msg(exc: urllib.error.HTTPError) -> str:
    code = exc.code
    if code in (401, 403):
        return (f"Authentication failed (HTTP {code}) — the endpoint rejected the "
                "auth header. Check the header name and value.")
    if code == 404:
        return "The endpoint returned 404 Not Found — check the URL path."
    if code == 405:
        return ("The endpoint returned 405 Method Not Allowed — it must accept a "
                "POST request.")
    return f"The endpoint returned HTTP {code}."


def _network_error_msg(exc: Exception) -> str:
    s = str(exc).lower()
    if "timed out" in s or "timeout" in s:
        return "The request timed out — the endpoint didn't respond in time."
    return (f"Couldn't reach the endpoint ({exc}). Check the URL is correct and "
            "publicly reachable over HTTPS.")


def _trace_error_msg(err: str) -> str:
    if err.startswith("UnsafeURLError"):
        return ("Blocked for safety: that URL is, or resolves to, a private/"
                "internal address (SSRF protection). Connect a public https "
                "endpoint instead.")
    if err.startswith("MappingError"):
        # the message after the type carries the actionable mapping detail
        return "We reached your agent but " + err.split(": ", 1)[-1]
    if "JSONDecode" in err or "json" in err.lower():
        return ("The endpoint replied, but not with JSON we could parse. Agenttic "
                "expects a JSON response body.")
    return f"The connection test failed: {err}"


def probe(adapter, prompt: str = PROBE_PROMPT) -> ProbeResult:
    """Send ONE harmless probe through the adapter's mapping and classify the
    outcome. Never stores anything; returns the agent's actual reply (so the UI
    can show "your agent said: …") or a clear, fix-it error message."""
    try:
        trace = adapter.run({"request": prompt})
    except urllib.error.HTTPError as exc:           # 4xx/5xx (auth, wrong path…)
        return ProbeResult(False, error=_http_error_msg(exc),
                           detail={"status": exc.code})
    except (ConnectionError, TimeoutError, OSError) as exc:  # unreachable / timeout
        return ProbeResult(False, error=_network_error_msg(exc))
    err = trace.spans[0].error if trace.spans else None
    if err:
        return ProbeResult(False, error=_trace_error_msg(err), detail={"raw": err})
    reply = trace.final_output or ""
    return ProbeResult(True, reply=reply,
                       detail={"latency_ms": round(trace.total_latency_ms)})
