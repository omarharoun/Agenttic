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
                    allow_private: bool = False) -> dict:
    # request-time SSRF check: must resolve to a public address to be dialed
    # (unless the operator explicitly allowed private targets for this agent)
    validate_blackbox_url(
        url, resolve=True, allow_unresolved=False,
        cfg={"security": {"blackbox_block_private": not allow_private}})
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class BlackBoxHTTPAgent(AgentAdapter):
    """Driver for a client's existing agent behind a POST endpoint.

    ``output_field``: key in the JSON response holding the agent's answer.
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
        transport=None,  # injectable for tests; defaults to real HTTP
    ):
        self.agent_id = agent_id
        self.url = url
        self.output_field = output_field
        self.timeout = timeout
        self.allow_private_url = allow_private_url
        self._transport = transport or (
            lambda payload: _http_transport(self.url, payload, self.timeout,
                                            self.allow_private_url)
        )

    def describe(self) -> dict:
        return {"adapter": "BlackBoxHTTPAgent", "url": self.url,
                "output_field": self.output_field}

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        t0 = datetime.now(timezone.utc)
        wall = time.monotonic()
        error: str | None = None
        final = ""
        try:
            body = self._transport(test_input)
            if self.output_field not in body:
                error = f"response missing field {self.output_field!r}: keys={list(body)}"
            else:
                final = str(body[self.output_field])
        except (ConnectionError, OSError):
            raise  # transport errors bubble up — the harness owns retries
        except Exception as exc:  # noqa: BLE001 — malformed response is data
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
            total_cost_usd=0.0,           # unknown for black-box agents
            total_latency_ms=latency_ms,
            total_steps=1,
            schema_version=SCHEMA_VERSION,
        )
