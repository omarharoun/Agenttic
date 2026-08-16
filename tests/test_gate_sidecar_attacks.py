"""Adversarial suite against the sidecar's claim:

    "nothing reaches the upstream tool without a valid, unexpired, single-use
     receipt naming that exact action and instance."

Every assertion here is on the **upstream side effect**, never the status code.

Why this file does not use ``TestClient``
-----------------------------------------
``starlette.testclient`` percent-decodes the target **twice** before it reaches
the app (``httpx`` decodes ``URL.path``, then the transport calls ``unquote`` on
it again), so ``%252D`` arrives as ``-``. A real ASGI server decodes exactly
once. Path-confusion findings tested through ``TestClient`` are therefore
neither sound nor complete, in both directions.

:func:`_asgi` below is uvicorn's contract and nothing else: ``scope["path"]`` is
the raw target decoded **once**, ``raw_path`` keeps the bytes, ``query_string``
is untouched. It drives both hops — the client→sidecar hop and the
sidecar→upstream hop — so the two decodes a request really undergoes are both
present, which is precisely what the double-encoding attack turns on.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request

from agenttic.gate.middleware import (
    HEADER_NAME,
    InMemoryNonceStore,
    RevocationCache,
    encode_receipt_header,
)
from agenttic.gate.receipt import Principal, issue_tool_access_receipt
from agenttic.gate.sidecar import MAX_BODY_BYTES, build_sidecar
from agenttic.passport.keys import PassportKeyManager, generate_key

T0 = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)

SCHEMA = {"type": "object",
          "properties": {"customer_id": {"type": "string"}},
          "required": ["customer_id"]}

CFG = {"enforcement": {"tool_access": {
    "gateway_id": "gw:test",
    "upstream": "http://crm.internal",
    "tools": {
        "delete_customer": {
            "action_class": "irreversible",
            "bound_params": ["customer_id"],
            "input_schema": SCHEMA,
            "route": "DELETE /customers/{customer_id}"},
        "read_customer": {
            "action_class": "read",
            "input_schema": SCHEMA,
            "route": "GET /customers/{customer_id}"},
    }}}}


# --------------------------------------------------------------------------- #
# A real ASGI server's decoding contract, on both hops.
# --------------------------------------------------------------------------- #


def _asgi(app, method: str, target: str, headers=None, body: bytes = b""):
    """Call an ASGI app the way uvicorn would. ``target`` is the raw request
    target, exactly as it would appear on the wire (still percent-encoded).

    ``headers`` is a list of (name, value) pairs so duplicates are expressible.
    Returns ``(status, body_bytes, headers_list)``.
    """
    raw, _, query = target.partition("?")
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or [])]
    if body:
        hdrs.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method.upper(), "scheme": "http",
        "path": unquote(raw),            # decoded ONCE, like a real server
        "raw_path": raw.encode(), "query_string": query.encode(),
        "root_path": "", "headers": hdrs,
        "client": ("1.2.3.4", 40000), "server": ("testserver", 80),
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    out = b"".join(m.get("body", b"") for m in sent
                   if m["type"] == "http.response.body")
    return (start["status"], out,
            [(k.decode(), v.decode()) for k, v in start.get("headers", [])])


# --------------------------------------------------------------------------- #
# The unmodified tool. Knows nothing about receipts; would delete for anyone.
# --------------------------------------------------------------------------- #


def _upstream_app(state: dict, *, honour_method_override: bool = False,
                  honour_cascade: bool = False) -> FastAPI:
    app = FastAPI()

    @app.api_route("/customers/{customer_id}", methods=["DELETE"])
    async def delete_customer(customer_id: str, request: Request):
        if customer_id not in state["customers"]:
            raise HTTPException(404, "no such customer")
        del state["customers"][customer_id]
        deleted = [customer_id]
        if honour_cascade:
            # An ordinary CRM option. Nothing in the receipt covers it.
            raw = await request.body()
            try:
                cascade = json.loads(raw or b"{}").get("cascade")
            except ValueError:
                cascade = None
            if cascade:
                deleted += list(state["customers"])
                state["customers"].clear()
        state["deleted"] += deleted
        return {"deleted": deleted}

    @app.get("/customers/{customer_id}")
    def read_customer(customer_id: str):
        state["reads"].append(customer_id)
        return {"customer_id": customer_id}

    @app.get("/secrets")
    def secrets():
        state["secrets"] += 1
        return {"root_password": "hunter2"}

    if honour_method_override:
        inner = app

        async def override(scope, receive, send):
            # Django/Rails/Symfony/many API gateways all ship this. The tool is
            # still "unmodified" — this is a framework feature, not a receipt.
            if scope["type"] == "http":
                for k, v in scope["headers"]:
                    if k == b"x-http-method-override":
                        scope = dict(scope, method=v.decode().upper())
                        break
            await inner(scope, receive, send)

        return override
    return app


class _Rig:
    """Sidecar in front of the tool, both driven through :func:`_asgi`."""

    def __init__(self, cfg=CFG, *, requests_style: bool = False, **upstream_kw):
        self.t = T0
        self.keys = PassportKeyManager({}, private_key=generate_key())
        self.state = {"customers": {"c-1": "Ada", "c-2": "Grace"},
                      "deleted": [], "reads": [], "secrets": 0}
        self.upstream = _upstream_app(self.state, **upstream_kw)
        self.forwards: list[tuple[str, str]] = []
        self.forwarded_headers: list[dict] = []
        self.requests_style = requests_style

        def forward(method, url, body, headers):
            if self.requests_style:
                # What the shipped default forwarder really puts on the wire:
                # ``requests`` runs every URL through ``requote_uri``.
                from requests.models import requote_uri
                url = requote_uri(url)
            self.forwards.append((method, url))
            self.forwarded_headers.append(dict(headers))
            parts = urlsplit(url)
            target = parts.path + (f"?{parts.query}" if parts.query else "")
            status, out, hdrs = _asgi(self.upstream, method, target,
                                      list(headers.items()), body)
            return status, out, dict(hdrs)

        self.app = build_sidecar(
            cfg, jwks=self.keys.jwks, forward=forward,
            nonce_store=InMemoryNonceStore(now=lambda: self.t),
            revocations=RevocationCache(fetcher=lambda url: {"status": "active"},
                                        now=lambda: self.t),
            now=lambda: self.t)

    def receipt(self, tool="delete_customer", action_class="irreversible",
                bound_values=..., **kw) -> str:
        if bound_values is ...:
            bound_values = ({"customer_id": "c-1"}
                            if action_class == "irreversible" else None)
        r = issue_tool_access_receipt(
            self.keys, tool=tool, action_class=action_class,
            params_schema=SCHEMA, bound_values=bound_values,
            passport_id="pp-adv", passport_hash="a1c9",
            principal=Principal(id="sub:okta|jane", via=["agent:triage-bot"]),
            gateway_id="gw:test", decision_id="decision:test",
            policy_hash="e0aa", now=self.t, **kw)
        return encode_receipt_header(r.model_dump(mode="json"))

    def call(self, method, target, header=None, body=b"", extra=()):
        hdrs = list(extra)
        if header is not None:
            hdrs.append((HEADER_NAME, header))
        return _asgi(self.app, method, target, hdrs, body)

    def untouched(self) -> bool:
        return (self.state["customers"] == {"c-1": "Ada", "c-2": "Grace"}
                and self.state["deleted"] == [] and self.state["reads"] == []
                and self.state["secrets"] == 0 and self.forwards == [])


# ====================================================================== #
# POSITIVE CONTROLS — if these fail, every "blocked" below is vacuous.
# ====================================================================== #


def test_positive_control_the_harness_can_observe_an_ungated_delete():
    """The tool really is undefended and the rig really sees the side effect.
    Without this, a broken harness would report the whole file as 'blocked'."""
    r = _Rig()
    status, body, _ = _asgi(r.upstream, "DELETE", "/customers/c-1")
    assert status == 200
    assert json.loads(body) == {"deleted": ["c-1"]}
    assert r.state["customers"] == {"c-2": "Grace"}          # gone, no receipt


def test_positive_control_a_valid_receipt_still_succeeds_through_this_harness():
    """The raw-ASGI path is not simply refusing everything."""
    r = _Rig()
    status, body, _ = r.call("DELETE", "/customers/c-1", r.receipt())
    assert status == 200
    assert json.loads(body) == {"deleted": ["c-1"]}
    assert r.state["customers"] == {"c-2": "Grace"}
    assert r.forwards == [("DELETE", "http://crm.internal/customers/c-1")]


def test_positive_control_double_decoding_is_not_a_harness_artefact():
    """``_asgi`` decodes once per hop — the same as uvicorn, and unlike
    ``TestClient``, which decodes twice on the first hop alone."""
    seen = {}
    app = _upstream_app({"customers": {}, "deleted": [], "reads": [],
                         "secrets": 0})

    async def spy(scope, receive, send):
        seen["path"] = scope["path"]
        seen["raw"] = scope["raw_path"].decode()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    _asgi(spy, "GET", "/customers/c%252D1")
    assert seen["raw"] == "/customers/c%252D1"
    assert seen["path"] == "/customers/c%2D1"    # exactly one decode, not two
    assert app is not None


# ====================================================================== #
# FINDING 1 — verify one instance, act on another.
# ====================================================================== #


def test_a_double_encoded_segment_must_not_reach_an_instance_the_receipt_never_named():
    """FAILING — BYPASS 1. Asserts the invariant, and the invariant does not hold.

    The sidecar rebuilds the upstream URL by interpolating the *decoded* path
    parameter back into a URL **string** without re-encoding it::

        url = base + path_format.format(**params)

    ``params`` came out of ``scope["path"]``, which the sidecar's own server
    already decoded once. Any ``%XX`` the client double-encoded therefore
    survives into the outgoing URL and is decoded a **second** time on the next
    hop. The value the receipt is bound to and the value upstream acts on are
    then different strings.

    Wire target ``/customers/c%252D1``
      → sidecar sees ``c%2D1``  → receipt bound to ``c%2D1`` verifies (step 4
                                  compares the strings it was handed, and they
                                  do match — nothing here is broken)
      → forwards ``/customers/c%2D1``
      → upstream decodes        → deletes ``c-1``.

    ``c%2D1`` is not a customer. The only correct outcomes are a 403 from the
    gate or a 404 from the tool. Instead ``c-1`` is gone, so the signed evidence
    names an instance that was never touched, and any policy that decided on the
    customer id string decided about the wrong customer.
    """
    r = _Rig()
    r.call("DELETE", "/customers/c%252D1",
           r.receipt(bound_values={"customer_id": "c%2D1"}))
    assert "c-1" in r.state["customers"], (
        "c-1 was deleted under a receipt bound to 'c%2D1'; "
        f"upstream deleted {r.state['deleted']}")

    # Not specific to one id or to '-': %XX covers alphanumerics too ('%63' is
    # 'c'), so any instance can be spelled a way the receipt records verbatim
    # and the next hop resolves to something else.
    r2 = _Rig()
    r2.call("DELETE", "/customers/%2563-2",
            r2.receipt(bound_values={"customer_id": "%63-2"}))
    assert "c-2" in r2.state["customers"]


def test_the_shipped_requests_forwarder_survives_requote_uri():
    """BYPASS 1, second mechanism — now closed. Regression test.

    ``_requests_forward`` is the production hop, and ``requests`` normalises
    every URL through ``requote_uri``, which un-escapes all *unreserved*
    percent-escapes (``A-Za-z0-9-._~``). So a fix that emitted the verified
    value raw would be undone inside the proxy before the request ever left the
    process: ``requote_uri(".../c%2D1") == ".../c-1"``.

    The invariant is NOT "the wire bytes equal the verified string" — that was
    this test's original assertion, and it is both unreachable (no input is a
    fixed point of ``requote_uri`` mapping to it) and *wrong*: ``c%2D1`` on the
    wire decodes at the next hop to ``c-1``, a different customer than the one
    verified. The invariant is that the value UPSTREAM RESOLVES equals the value
    the receipt was checked against.

    The verified value is the literal 5-character string ``c%2D1``, so naming it
    on the wire requires escaping the percent: ``c%252D1``. That string IS a
    fixed point of ``requote_uri``, so it reaches the tool intact and decodes
    back to ``c%2D1`` — which is not a customer, so the tool 404s. c-1 lives.
    """
    r = _Rig(requests_style=True)
    r.call("DELETE", "/customers/c%252D1",
           r.receipt(bound_values={"customer_id": "c%2D1"}))
    assert r.forwards == [("DELETE", "http://crm.internal/customers/c%252D1")], (
        "the URL put on the wire does not name the value that was verified")
    # the point of all of it: the instance the receipt never named is untouched
    assert r.state["deleted"] == []
    assert r.state["customers"] == {"c-1": "Ada", "c-2": "Grace"}


# ====================================================================== #
# FINDING 2 — the method the sidecar gated is not the method upstream runs.
# ====================================================================== #


def test_a_method_override_header_must_not_be_relayed_to_the_tool():
    """FAILING — BYPASS 2 (precondition: the tool's framework honours the header).

    ``_HOP_BY_HOP`` strips the receipt, host and framing headers. It does not
    strip ``X-HTTP-Method-Override``. The sidecar picks its route, its action
    class and therefore its whole receipt requirement from ``request.method`` =
    GET — a *read* — and then hands the tool a header that turns the very same
    request into a DELETE.

    So the method the gate reasoned about is not the method the tool runs. A
    read receipt (no instance binding at all, longer TTL, cached rather than
    live revocation) drives an irreversible action.

    The header is ubiquitous — Django, Rails, Symfony, Spring and most API
    gateways honour it by default — so "the tool wouldn't do that" is not a
    property the gate gets to assume about a tool it deliberately does not
    modify.
    """
    r = _Rig(honour_method_override=True)
    r.call("GET", "/customers/c-1", r.receipt("read_customer", "read"),
           extra=[("X-HTTP-Method-Override", "DELETE")])
    assert r.state["deleted"] == [], "a read receipt drove a delete"
    assert "c-1" in r.state["customers"]


# ====================================================================== #
# FINDING 3 — the query string is refused, the body is not.
# ====================================================================== #


def test_an_unbound_body_must_not_ride_along_on_an_irreversible_call():
    """FAILING — BYPASS 3. The exact hole the query-string refusal was written
    to close, left open one field over.

    ``sidecar.py`` refuses a query string on an irreversible call, and says why:
    ``?cascade=1`` "would ride in unbound. Refuse rather than silently strip."
    The request **body** is covered by nothing in the receipt either, and it is
    relayed byte for byte.

    ``bound_params`` names only ``customer_id``; ``input_schema`` is hashed into
    ``action_hash`` but never validated against the payload. So a receipt that
    names exactly one instance drives a delete of every instance, and the
    receipt is a truthful record of an authorisation nobody granted.

    CLOSED BY REFUSAL, not by stripping — and this assertion was changed to
    match, because the original one asked for the unsafe remedy. It required
    ``deleted == ["c-1"]``: proceed with the body neutralised. Stripping can
    ESCALATE. Drop ``{"dry_run": true}`` and a rehearsal becomes a deletion;
    the caller asked for one thing and the tool did another, with a valid
    receipt over it. The module already refuses rather than strips for the query
    string (see the sibling test below) and this is the same hole one field
    over, so it gets the same answer. Validating the body against
    ``input_schema`` also lands on refusal here — ``{"cascade": true}`` has no
    ``customer_id`` and fails ``required``. Every principled reading gives 403.

    Refused BEFORE ``verify_tool_receipt``, so the nonce is not burned: a
    malformed call must not cost the caller its single-use capability.
    """
    r = _Rig(honour_cascade=True)
    status, _, _ = r.call("DELETE", "/customers/c-1",
                          r.receipt(bound_values={"customer_id": "c-1"}),
                          body=b'{"cascade": true}',
                          extra=[("content-type", "application/json")])
    assert status == 403
    assert r.forwards == [], "an unbound body reached the tool"
    # the side effect, not just the status: nothing was deleted, either instance
    assert r.state["deleted"] == []
    assert r.state["customers"] == {"c-1": "Ada", "c-2": "Grace"}


def test_query_string_on_an_irreversible_call_is_still_refused():
    """The half that is closed, kept as a regression next to the half that is
    not — so the asymmetry above cannot be read as an oversight in this file."""
    r = _Rig()
    status, _, _ = r.call("DELETE", "/customers/c-1?cascade=true", r.receipt())
    assert status == 403
    assert r.untouched()


# ====================================================================== #
# BLOCKED — path confusion. All of these stay as regressions.
# ====================================================================== #


def test_encoded_slash_cannot_reach_a_second_path_segment():
    r = _Rig()
    status, body, _ = r.call("DELETE", "/customers/c-1%2F..%2Fsecrets",
                             r.receipt())
    assert status == 403
    assert json.loads(body)["detail"] == "no tool access receipt covers this request"
    assert r.untouched()


def test_dot_dot_traversal_towards_an_uncatalogued_path_is_refused():
    r = _Rig()
    for target in ("/customers/../secrets", "/customers/%2e%2e/secrets",
                   "/customers/./c-1/../c-2"):
        status, _, _ = r.call("DELETE", target, r.receipt())
        assert status == 403, target
    assert r.untouched()


def test_trailing_slash_does_not_match_the_template():
    r = _Rig()
    status, _, _ = r.call("DELETE", "/customers/c-1/", r.receipt())
    assert status == 403
    assert r.untouched()


def test_double_slash_does_not_match_the_template():
    r = _Rig()
    for target in ("//customers/c-1", "/customers//c-1", "/./customers/c-1"):
        status, _, _ = r.call("DELETE", target, r.receipt())
        assert status == 403, target
    assert r.untouched()


def test_case_variation_does_not_match_the_template():
    r = _Rig()
    status, _, _ = r.call("DELETE", "/CUSTOMERS/c-1", r.receipt())
    assert status == 403
    assert r.untouched()


def test_a_unicode_lookalike_id_does_not_satisfy_the_receipt():
    """U+2010 HYPHEN, not U+002D. It matches the template but not the binding —
    nothing normalises the two together."""
    r = _Rig()
    status, _, _ = r.call("DELETE", "/customers/c%E2%80%901", r.receipt())
    assert status == 403
    assert r.untouched()


def test_a_fragment_cannot_smuggle_a_query_string_past_the_irreversible_check():
    """``/customers/c-1%23%3Fcascade=true`` hides the query behind a ``#``, so
    ``request.url.query`` is empty and the irreversible refusal never fires.
    Rebuilding the URL from the template is what closes it: the fragment is not
    a matched parameter, so it is dropped rather than relayed."""
    r = _Rig(honour_cascade=True)
    status, _, _ = r.call("DELETE", "/customers/c-1%23%3Fcascade=true",
                          r.receipt())
    assert status == 200                       # it did match, as c-1
    assert r.forwards == [("DELETE", "http://crm.internal/customers/c-1")]
    assert r.state["deleted"] == ["c-1"]       # only c-1: no cascade got through
    assert r.state["customers"] == {"c-2": "Grace"}


def test_an_encoded_question_mark_on_an_irreversible_call_is_refused():
    """``%3F`` puts the ``?`` into ``scope["path"]``; Starlette re-splits it, so
    ``request.url.query`` is populated and the irreversible check catches it."""
    r = _Rig()
    status, _, _ = r.call("DELETE", "/customers/c-1%3Fcascade=true", r.receipt())
    assert status == 403
    assert r.untouched()


# ====================================================================== #
# BLOCKED — headers, methods, config.
# ====================================================================== #


def test_duplicate_receipt_headers_neither_bypass_nor_reach_upstream():
    """The first header is the one verified; a valid one cannot be hidden
    behind a junk one, and neither copy is relayed to the tool."""
    r = _Rig()
    good, junk = r.receipt(), "!!!not-base64!!!"

    status, _, _ = r.call("DELETE", "/customers/c-1", None,
                          extra=[(HEADER_NAME, junk), (HEADER_NAME, good)])
    assert status == 403
    assert r.untouched()

    status, _, _ = r.call("DELETE", "/customers/c-1", None,
                          extra=[(HEADER_NAME, good), (HEADER_NAME, junk)])
    assert status == 200
    assert not any(k.lower() == HEADER_NAME.lower()
                   for h in r.forwarded_headers for k in h)


def test_head_and_options_cannot_ride_a_get_route():
    r = _Rig()
    for method in ("HEAD", "OPTIONS"):
        status, _, _ = r.call(method, "/customers/c-1",
                              r.receipt("read_customer", "read"))
        assert status == 403, method
    assert r.untouched()


def test_an_ambiguous_route_table_refuses_instead_of_picking_the_weaker_entry():
    """Two tools on one method+path is a config fault. Refusing beats resolving
    it by dict order, which would let a ``write`` entry answer for the
    ``irreversible`` one."""
    cfg = json.loads(json.dumps(CFG))
    cfg["enforcement"]["tool_access"]["tools"]["soft_delete_customer"] = {
        "action_class": "write", "input_schema": SCHEMA,
        "route": "DELETE /customers/{customer_id}"}
    r = _Rig(cfg)
    status, body, _ = r.call("DELETE", "/customers/c-1", r.receipt())
    assert status == 403
    assert json.loads(body)["detail"] == "no tool access receipt covers this request"
    assert r.untouched()


def test_an_oversized_body_is_refused_before_the_nonce_is_claimed():
    """The size check runs before ``verify_tool_receipt``, so a flood of huge
    bodies cannot burn other requests' capabilities."""
    r = _Rig()
    header = r.receipt()
    status, _, _ = r.call("DELETE", "/customers/c-1", header,
                          body=b"x" * (MAX_BODY_BYTES + 1))
    assert status == 403
    assert r.untouched()
    status, _, _ = r.call("DELETE", "/customers/c-1", header)   # still spendable
    assert status == 200


def test_a_conflicting_body_id_does_not_change_what_upstream_acts_on():
    """The flagged verify-one-forward-another shape, in its ordinary form: the
    path wins for verification *and* for the rebuilt URL, so a body claiming a
    different customer changes neither."""
    r = _Rig()
    status, _, _ = r.call("DELETE", "/customers/c-1", r.receipt(),
                          body=b'{"customer_id": "c-2"}',
                          extra=[("content-type", "application/json")])
    assert status == 200
    assert r.state["deleted"] == ["c-1"]
    assert r.state["customers"] == {"c-2": "Grace"}


def test_a_read_receipt_is_not_bound_to_any_instance():
    """Scope, not a proxy defect: step 4 binds instances for ``irreversible``
    only, so one read receipt reads any customer. Pinned here because the claim
    under attack says "that exact ... instance" without qualifying it."""
    r = _Rig()
    header = r.receipt("read_customer", "read", bound_values=None)
    status, _, _ = r.call("GET", "/customers/c-2", header)
    assert status == 200
    assert r.state["reads"] == ["c-2"]


def test_the_nonce_is_burned_before_the_hop_so_a_slow_upstream_cannot_be_replayed():
    r = _Rig()
    header = r.receipt()
    assert r.call("DELETE", "/customers/c-1", header)[0] == 200
    r.state["customers"]["c-1"] = "Ada"
    status, body, _ = r.call("DELETE", "/customers/c-1", header)
    assert status == 403
    assert json.loads(body)["detail"] == "tool access receipt already used"
    assert r.state["customers"]["c-1"] == "Ada"
    assert len(r.forwards) == 1


def test_an_expired_receipt_is_refused_on_the_raw_path_too():
    r = _Rig()
    header = r.receipt()
    r.t += timedelta(seconds=31)
    status, _, _ = r.call("DELETE", "/customers/c-1", header)
    assert status == 403
    assert r.untouched()
