"""Black-box HTTP adapter (Step 7) — wraps any agent reachable as an HTTP
endpoint. Produces a Trace with a single final_output span and latency;
``visibility="black_box"`` automatically restricts scoring to criteria that
do not require trajectory data (see scoring.engine.applicable_criteria).
"""

from __future__ import annotations

import json
import time
import urllib.request
import uuid
from datetime import datetime, timezone

from ascore.adapters.base import AgentAdapter
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.security import validate_blackbox_url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so a validated URL can't be bounced to an internal
    target (SSRF redirect bypass)."""
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http_transport(url: str, payload: dict, timeout: float,
                    allow_private: bool = False,
                    headers: dict | None = None) -> dict:
    # request-time SSRF check: must resolve to a public address to be dialed
    # (unless the operator explicitly allowed private targets for this agent)
    validate_blackbox_url(
        url, resolve=True, allow_unresolved=False,
        cfg={"security": {"blackbox_block_private": not allow_private}})
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


#: Header set on every request to the user's agent so the operator can recognise
#: Agenttic's safety traffic. Defined in ``ascore.connect`` (single source).
from ascore.connect import SAFETY_TEST_HEADER  # noqa: E402


class BlackBoxHTTPAgent(AgentAdapter):
    """Driver for a client's existing agent behind a POST endpoint.

    Two response-extraction modes:

    * legacy/flat — ``output_field``: a top-level key in the JSON response.
    * ``mapping`` (a :class:`ascore.connect.Mapping`): the request prompt is
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
        mapping=None,                     # ascore.connect.Mapping | None
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
            from ascore.connect import build_request_body, render_prompt
            return build_request_body(self.mapping, render_prompt(test_input))
        return test_input

    def _extract_reply(self, body: dict) -> str:
        """The reply text from the response (raises so callers record an error)."""
        if self.mapping is not None:
            from ascore.connect import extract_reply
            return extract_reply(self.mapping, body)
        if self.output_field not in body:
            from ascore.connect import MappingError
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
