"""The sidecar refuses on behalf of a tool that knows nothing about receipts.

``_upstream_app`` below is the whole point: it imports nothing from
``agenttic.gate``, carries no decorator, and would happily delete a customer for
anyone who asked. Every "blocked" assertion here therefore checks the *upstream
side effect*, plus a forward counter that is only incremented once a socket
would have been opened — a 403 with the row already gone is precisely the
failure the sidecar exists to prevent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agenttic.gate.middleware import (
    HEADER_NAME,
    InMemoryNonceStore,
    RevocationCache,
    encode_receipt_header,
)
from agenttic.gate.receipt import Principal, issue_tool_access_receipt
from agenttic.gate.sidecar import build_sidecar
from agenttic.passport.keys import PassportKeyManager, generate_key

T0 = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)

CUSTOMER_ID_SCHEMA = {
    "type": "object",
    "properties": {"customer_id": {"type": "string"}},
    "required": ["customer_id"],
}

CFG = {"enforcement": {"tool_access": {
    "gateway_id": "gw:test",
    "upstream": "http://crm.internal",
    "tools": {
        "delete_customer": {
            "action_class": "irreversible",
            "bound_params": ["customer_id"],
            "input_schema": CUSTOMER_ID_SCHEMA,
            "route": "DELETE /customers/{customer_id}"},
        "read_customer": {
            "action_class": "read",
            "input_schema": CUSTOMER_ID_SCHEMA,
            "route": "GET /customers/{customer_id}"},
    }}}}


class _Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def _upstream_app(state: dict) -> FastAPI:
    """An ordinary CRM. No receipt, no decorator, no idea it is being gated."""
    app = FastAPI()

    @app.delete("/customers/{customer_id}")
    def delete_customer(customer_id: str):
        if customer_id not in state["customers"]:
            raise HTTPException(404, "no such customer")
        del state["customers"][customer_id]
        return {"deleted": customer_id}

    @app.get("/customers/{customer_id}")
    def read_customer(customer_id: str):
        state["reads"].append(customer_id)
        return {"customer_id": customer_id}

    @app.get("/secrets")
    def secrets():
        state["secrets"] += 1          # never catalogued, never routed
        return {"root_password": "hunter2"}

    return app


class _Sidecar:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.keys = PassportKeyManager({}, private_key=generate_key())
        self.state = {"customers": {"c-1": "Ada", "c-2": "Grace"},
                      "reads": [], "secrets": 0}
        self.upstream = TestClient(_upstream_app(self.state))
        self.forwards: list[tuple[str, str]] = []

        def forward(method, url, body, headers):
            self.forwards.append((method, url))
            r = self.upstream.request(method, url, content=body, headers=headers)
            return r.status_code, r.content, dict(r.headers)

        self.client = TestClient(build_sidecar(
            CFG, jwks=self.keys.jwks, forward=forward,
            nonce_store=InMemoryNonceStore(now=self.clock),
            revocations=RevocationCache(fetcher=lambda url: {"status": "active"},
                                        now=self.clock),
            now=self.clock))

    def receipt(self, tool="delete_customer", action_class="irreversible",
                bound_values=None, **kw) -> str:
        if action_class == "irreversible" and bound_values is None:
            bound_values = {"customer_id": "c-1"}
        r = issue_tool_access_receipt(
            self.keys, tool=tool, action_class=action_class,
            params_schema=CUSTOMER_ID_SCHEMA, bound_values=bound_values,
            passport_id="pp-sidecar", passport_hash="a1c9",
            principal=Principal(id="sub:okta|jane", via=["agent:triage-bot"]),
            gateway_id="gw:test", decision_id="decision:test",
            policy_hash="e0aa", now=self.clock(), **kw)
        return encode_receipt_header(r.model_dump(mode="json"))

    def delete(self, customer_id="c-1", header=None):
        headers = {} if header is None else {HEADER_NAME: header}
        return self.client.delete(f"/customers/{customer_id}", headers=headers)

    def untouched(self) -> bool:
        """Nothing upstream moved, and no hop was even attempted."""
        return (self.state["customers"] == {"c-1": "Ada", "c-2": "Grace"}
                and self.state["reads"] == [] and self.state["secrets"] == 0
                and self.forwards == [])


# ====================================================================== #
# The point: an unmodified tool, gated.
# ====================================================================== #


def test_valid_receipt_reaches_the_unmodified_upstream():
    s = _Sidecar()
    r = s.delete("c-1", s.receipt())
    assert r.status_code == 200
    assert r.json() == {"deleted": "c-1"}
    assert "c-1" not in s.state["customers"]          # upstream really acted
    assert len(s.forwards) == 1
    # rebuilt from the template against the configured upstream, not passed through
    assert s.forwards[0] == ("DELETE", "http://crm.internal/customers/c-1")


def test_read_receipt_reaches_upstream_and_relays_the_body():
    s = _Sidecar()
    r = s.client.get("/customers/c-2", headers={
        HEADER_NAME: s.receipt("read_customer", "read")})
    assert r.status_code == 200
    assert r.json() == {"customer_id": "c-2"}
    assert s.state["reads"] == ["c-2"]


# ====================================================================== #
# Refusals — asserted on the upstream side effect, not the status code.
# ====================================================================== #


def test_no_receipt_never_reaches_upstream():
    s = _Sidecar()
    r = s.delete("c-1", None)
    assert r.status_code == 403
    assert r.json()["detail"] == "missing tool access receipt"
    assert s.untouched()


def test_receipt_for_another_instance_never_reaches_upstream():
    s = _Sidecar()
    header = s.receipt(bound_values={"customer_id": "c-1"})
    r = s.delete("c-2", header)                       # substitution, not replay
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt does not authorise this call"
    assert s.untouched()
    # and the refused receipt did not burn its nonce: still good for c-1
    assert s.delete("c-1", header).status_code == 200


def test_replay_is_refused_and_upstream_is_called_once():
    s = _Sidecar()
    header = s.receipt()
    assert s.delete("c-1", header).status_code == 200
    assert len(s.forwards) == 1
    s.state["customers"]["c-1"] = "Ada"               # undo, so a second delete
    r = s.delete("c-1", header)                       # would be observable
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt already used"
    assert s.state["customers"]["c-1"] == "Ada"
    assert len(s.forwards) == 1                       # no second hop


def test_unmatched_path_is_refused_not_forwarded():
    s = _Sidecar()
    # A perfectly valid receipt does not turn an uncatalogued path into a
    # governed one. An unlisted path is an ungoverned path.
    r = s.client.get("/secrets", headers={
        HEADER_NAME: s.receipt("read_customer", "read")})
    assert r.status_code == 403
    # The exact reason, not just the code: this must be the allowlist refusing,
    # not the catch-all mopping up an IndexError further down. Both give 403,
    # only one of them is the gate.
    assert r.json()["detail"] == "no tool access receipt covers this request"
    assert s.untouched()


def test_wrong_method_on_a_known_path_is_refused():
    s = _Sidecar()
    r = s.client.post("/customers/c-1", headers={HEADER_NAME: s.receipt()})
    assert r.status_code == 403
    assert r.json()["detail"] == "no tool access receipt covers this request"
    assert s.untouched()


def test_read_receipt_cannot_drive_the_delete_route():
    s = _Sidecar()
    r = s.delete("c-1", s.receipt("read_customer", "read"))
    assert r.status_code == 403
    assert s.untouched()


def test_expired_receipt_never_reaches_upstream():
    s = _Sidecar()
    header = s.receipt()
    s.clock.advance(31)                               # irreversible TTL is 30s
    r = s.delete("c-1", header)
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt expired"
    assert s.untouched()


def test_revoked_passport_never_reaches_upstream():
    s = _Sidecar()
    s.client = TestClient(build_sidecar(
        CFG, jwks=s.keys.jwks,
        forward=lambda *a: (_ for _ in ()).throw(AssertionError("forwarded!")),
        nonce_store=InMemoryNonceStore(now=s.clock),
        revocations=RevocationCache(fetcher=lambda url: {"status": "revoked"},
                                    now=s.clock),
        now=s.clock))
    r = s.delete("c-1", s.receipt())
    assert r.status_code == 403
    assert r.json()["detail"] == "agent passport revoked"
    assert s.untouched()


def test_query_string_on_an_irreversible_call_is_refused():
    s = _Sidecar()
    # ?cascade=true is covered by nothing in the receipt, so it would ride in
    # unbound. Refuse rather than silently strip.
    r = s.client.delete("/customers/c-1?cascade=true",
                        headers={HEADER_NAME: s.receipt()})
    assert r.status_code == 403
    assert s.untouched()


def test_garbage_header_is_refused_not_forwarded():
    s = _Sidecar()
    for junk in ("!!!not-base64!!!", "", "   ", "ZZZ"):
        assert s.delete("c-1", junk).status_code == 403
    assert s.untouched()


def test_the_capability_token_is_never_handed_to_upstream():
    s = _Sidecar()
    seen: dict = {}

    def forward(method, url, body, headers):
        seen.update(headers)
        return 200, b'{"ok":true}', {}

    s.client = TestClient(build_sidecar(
        CFG, jwks=s.keys.jwks, forward=forward,
        nonce_store=InMemoryNonceStore(now=s.clock),
        revocations=RevocationCache(fetcher=lambda url: {"status": "active"},
                                    now=s.clock),
        now=s.clock))
    assert s.delete("c-1", s.receipt()).status_code == 200
    assert not any(k.lower() == HEADER_NAME.lower() for k in seen)


def test_upstream_failure_is_502_and_the_nonce_stays_burned():
    s = _Sidecar()
    header = s.receipt()

    def boom(*a):
        raise ConnectionError("upstream down")

    s.client = TestClient(build_sidecar(
        CFG, jwks=s.keys.jwks, forward=boom,
        nonce_store=(store := InMemoryNonceStore(now=s.clock)),
        revocations=RevocationCache(fetcher=lambda url: {"status": "active"},
                                    now=s.clock),
        now=s.clock))
    assert s.delete("c-1", header).status_code == 502
    # No refund: a refund is a replay window with paperwork. Retry = new receipt.
    s.client = TestClient(build_sidecar(
        CFG, jwks=s.keys.jwks, forward=lambda *a: (200, b"{}", {}),
        nonce_store=store,
        revocations=RevocationCache(fetcher=lambda url: {"status": "active"},
                                    now=s.clock),
        now=s.clock))
    assert s.delete("c-1", header).status_code == 403


def test_uncatalogued_route_key_is_simply_not_proxied():
    s = _Sidecar()
    cfg = {"enforcement": {"tool_access": {"upstream": "http://x", "tools": {
        "delete_customer": {"action_class": "irreversible",
                            "bound_params": ["customer_id"],
                            "input_schema": CUSTOMER_ID_SCHEMA}}}}}  # no route
    client = TestClient(build_sidecar(
        cfg, jwks=s.keys.jwks,
        forward=lambda *a: (_ for _ in ()).throw(AssertionError("forwarded!")),
        nonce_store=InMemoryNonceStore(now=s.clock),
        revocations=RevocationCache(fetcher=lambda url: {"status": "active"},
                                    now=s.clock),
        now=s.clock))
    assert client.delete("/customers/c-1",
                         headers={HEADER_NAME: s.receipt()}).status_code == 403
