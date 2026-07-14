"""Black-box HTTP adapter (Step 7) — wraps any agent reachable as an HTTP
endpoint. Produces a Trace with a single final_output span and latency;
``visibility="black_box"`` automatically restricts scoring to criteria that
do not require trajectory data (see scoring.engine.applicable_criteria).
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from agenttic.adapters.base import AgentAdapter
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.security import UnsafeURLError, pin_blackbox_target


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that dials a PRE-VALIDATED IP while keeping the original
    hostname for the Host header — so the socket goes to exactly the address the
    SSRF check approved, with no second DNS lookup (rebinding gap closed)."""

    def __init__(self, host: str, port: int, *, pinned_ip: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):  # noqa: D401
        self.sock = socket.create_connection((self._pinned_ip, self.port),
                                             self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS variant: connect to the validated IP but drive TLS SNI + certificate
    validation off the ORIGINAL hostname (``self.host``), so the cert still has to
    match the real domain even though we dialed a pinned address."""

    def __init__(self, host: str, port: int, *, pinned_ip: str, timeout: float,
                 context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self):  # noqa: D401
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _http_transport(url: str, payload: dict, timeout: float,
                    allow_private: bool = False,
                    headers: dict | None = None) -> dict:
    # Request-time SSRF check that RESOLVES ONCE and PINS the address: validate
    # scheme/allowlist/public-IP, then dial exactly that validated IP (no second,
    # unvalidated DNS lookup). Private targets are refused unless the operator
    # explicitly opted this agent in.
    host, port, pinned_ip = pin_blackbox_target(
        url, cfg={"security": {"blackbox_block_private": not allow_private}})
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    body = json.dumps(payload).encode()
    # http.client sets the Host header from `host` (the original hostname); do not
    # override it. No auth/Host injection here beyond the caller's headers.
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    if parsed.scheme == "https":
        conn = _PinnedHTTPSConnection(host, port, pinned_ip=pinned_ip,
                                      timeout=timeout,
                                      context=ssl.create_default_context())
    else:
        conn = _PinnedHTTPConnection(host, port, pinned_ip=pinned_ip,
                                     timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers=hdrs)
        resp = conn.getresponse()
        # No redirects: http.client never follows them, and we refuse a 3xx
        # outright so a validated URL can't be bounced onto an internal target.
        if 300 <= resp.status < 400:
            raise UnsafeURLError(
                f"refusing redirect ({resp.status}) to "
                f"{resp.getheader('Location')!r} — SSRF guard")
        data = resp.read()
    finally:
        conn.close()
    return json.loads(data.decode())


#: Header set on every request to the user's agent so the operator can recognise
#: Agenttic's safety traffic. Defined in ``agenttic.connect`` (single source).
from agenttic.connect import SAFETY_TEST_HEADER  # noqa: E402


class BlackBoxHTTPAgent(AgentAdapter):
    """Driver for a client's existing agent behind a POST endpoint.

    Two response-extraction modes:

    * legacy/flat — ``output_field``: a top-level key in the JSON response.
    * ``mapping`` (a :class:`agenttic.connect.Mapping`): the request prompt is
      rendered into the mapped request body and the reply is read from a dotted
      response path (e.g. ``choices[0].message.content``). This is what the
      "Connect your agent" flow uses; it also builds the request body so an
      OpenAI-compatible endpoint works one-click.

    Every request carries ``X-Agenttic-Safety-Test: true`` and an optional
    ``min_interval_s`` enforces gentle, rate-limited traffic.
    """

    visibility = "black_box"

    def __init__(
        self,
        *,
        agent_id: str,
        url: str,
        output_field: str = "output",
        timeout: float = 60.0,
        allow_private_url: bool = False,  # opt-in to hit private/loopback hosts
        cost_per_call_usd: float = 0.0,   # declared cost (black-box has no usage)
        headers: dict | None = None,      # auth / custom headers for the endpoint
        mapping=None,                     # agenttic.connect.Mapping | None
        min_interval_s: float = 0.0,      # min seconds between requests (rate limit)
        transport=None,  # injectable for tests; defaults to real HTTP
    ):
        self.agent_id = agent_id
        self.url = url
        self.output_field = output_field
        self.timeout = timeout
        self.allow_private_url = allow_private_url
        self.cost_per_call_usd = cost_per_call_usd
        self.mapping = mapping
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._last_call = 0.0
        # the safety-test header rides on EVERY request (probe + scan); explicit
        # caller headers (auth) merge on top.
        self.headers = {SAFETY_TEST_HEADER: "true", **(headers or {})}
        self._transport = transport or (
            lambda payload: _http_transport(self.url, payload, self.timeout,
                                            self.allow_private_url, self.headers)
        )

    def describe(self) -> dict:
        d = {"adapter": "BlackBoxHTTPAgent", "url": self.url,
             "cost_per_call_usd": self.cost_per_call_usd}
        if self.mapping is not None:
            d["mapping"] = self.mapping.public()
        else:
            d["output_field"] = self.output_field
        return d

    def _request_body(self, test_input: dict) -> dict:
        """The JSON body to POST: mapped (Connect flow) or the raw case input."""
        if self.mapping is not None:
            from agenttic.connect import build_request_body, render_prompt
            return build_request_body(self.mapping, render_prompt(test_input))
        return test_input

    def _extract_reply(self, body: dict) -> str:
        """The reply text from the response (raises so callers record an error)."""
        if self.mapping is not None:
            from agenttic.connect import extract_reply
            return extract_reply(self.mapping, body)
        if self.output_field not in body:
            from agenttic.connect import MappingError
            raise MappingError(
                f"response missing field {self.output_field!r}: keys={list(body)}")
        return str(body[self.output_field])

    def _throttle(self) -> None:
        if self.min_interval_s <= 0:
            return
        wait = self.min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        t0 = datetime.now(timezone.utc)
        wall = time.monotonic()
        error: str | None = None
        final = ""
        cost = 0.0
        try:
            self._throttle()  # gentle traffic: honour the per-agent rate limit
            body = self._transport(self._request_body(test_input))
            self._last_call = time.monotonic()
            cost = self.cost_per_call_usd  # the call was actually made
            final = self._extract_reply(body)  # may raise (mapping/missing field)
        except (ConnectionError, OSError):
            raise  # transport errors bubble up — the harness owns retries
        except Exception as exc:  # noqa: BLE001 — malformed response/blocked url is data
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.monotonic() - wall) * 1000.0
        t1 = datetime.now(timezone.utc)

        span = Span(
            span_id=uuid.uuid4().hex[:12],
            kind="error" if error else "final_output",
            name="blackbox_http",
            start_time=t0, end_time=t1,
            input=test_input,
            output={} if error else {"text": final},
            error=error,
        )
        return Trace(
            trace_id=uuid.uuid4().hex,
            agent_id=self.agent_id,
            agent_config_hash=self.config_hash(),
            test_case_id=test_case_id,
            spans=[span],
            visibility=self.visibility,
            final_output=final if not error else f"BLACKBOX_FAILURE:{error}",
            total_cost_usd=cost,          # declared per-call cost (0 if unknown)
            total_latency_ms=latency_ms,
            total_steps=1,
            schema_version=SCHEMA_VERSION,
        )
