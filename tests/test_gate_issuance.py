"""Gateway issuance (§5, RECEIPT-SCHEMA.md:390) — the loop actually closes.

The gate was built with nothing minting for it: ``@require_receipt`` refused
every call because no receipt existed to present. These tests prove the two
halves now agree — a gateway allow produces a receipt that the tool, in its own
process with only the JWKS, accepts *verbatim*; and every path that should not
mint one does not.

Test 1 is the whole point. The rest are the fail-closed edges, each of which
would otherwise hand out a live capability.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from agenttic.enforce.gateway import compute_policy_hash
from agenttic.gate.middleware import HEADER_NAME, InMemoryNonceStore
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.schema.passport import Passport, PassportClaims
from agenttic.server.app import create_app
from agenttic.server.pats import PatStore
from examples.receipt_gated_tool import CUSTOMER_ID_SCHEMA, build_demo_app

AGENT = "a"


def _policy(*, deny_delete: bool = False) -> EnforcementPolicy:
    rules = []
    if deny_delete:
        rules.append(Rule(rule_id="r1", lane="lane1", action="deny",
                          matcher={"tool": "delete_customer"},
                          origin="test:deny"))
    p = EnforcementPolicy(policy_id="p1", agent_id=AGENT, rules=rules)
    p.content_hash = compute_policy_hash(p)
    return p


def _config(tmp_path) -> str:
    """The tool catalog entry, with input_schema inlined FROM the tool's own
    constant (YAML is a JSON superset). One source of truth: the issuer/tool
    drift this catalog exists to prevent is structurally impossible here."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""\
models: {{agent_default: a, judge_strong: j, judge_light: l}}
harness: {{timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}}
scoring: {{calibration_threshold: 0.8}}
live: {{sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}}
paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, calibration_dir: {tmp_path / 'c'}}}
auth: {{required: true, token: t}}
security: {{login_max_attempts: 5, login_lockout_seconds: 900}}
enforcement:
  tool_access:
    gateway_id: gw:test
    tools:
      delete_customer:
        action_class: irreversible
        bound_params: [customer_id]
        input_schema: {json.dumps(CUSTOMER_ID_SCHEMA)}
""")
    return str(cfg)


def _passport(reg, keys, *, expires_hours: int = 1) -> Passport:
    """An active passport for the agent. Built directly rather than via
    PassportIssuer.issue, which needs a full assembled dossier."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    claims = PassportClaims(
        agent_id=AGENT, tier="B", dossier_sha256="h",
        policy_hash="ph", issued_at=now,
        expires_at=now + timedelta(hours=expires_hours),
        status_url="https://agenttic.local/passport/pp-test/status",
        key_id=keys.key_id())
    p = Passport(passport_id="pp-test", claims=claims)
    p.signature = keys.sign(p.signing_input())
    reg.save_passport(p)
    return p


def _setup(tmp_path, *, deny_delete: bool = False):
    """Returns (api_client, pat_token, reg). ``app.state.passport_keys`` is built
    in the lifespan, so the passport must be created inside ``with client:``."""
    reg = Registry(tmp_path / "a.db")
    reg.save_policy(_policy(deny_delete=deny_delete))
    pat = PatStore(reg.engine).create(user_email="jane@x.com", tenant="default",
                                      role="operator", name="e2e")["token"]
    return TestClient(create_app(_config(tmp_path), registry=reg)), pat, reg


def _tool_client(keys):
    """Handing the tool the server's own key manager models "the tool fetched the
    gateway's JWKS once". The nonce store is injected so the test never touches
    the host-wide FileNonceStore."""
    return TestClient(build_demo_app(
        keys, status_fetcher=lambda url: {"status": "active"},
        nonce_store=InMemoryNonceStore()))


def _call(client, token, tool="delete_customer", args=None):
    auth = {"Authorization": f"Bearer {token}"}
    sess = client.post("/api/enforce/sessions", headers=auth,
                       json={"agent_id": AGENT}).json()
    return client.post("/api/enforce/tool-call", headers=auth,
                       json={"session_id": sess["session_id"], "tool_name": tool,
                             "args": args if args is not None else
                             {"customer_id": "c-1"}})


# --------------------------------------------------------------------------- #
# 1. The loop.
# --------------------------------------------------------------------------- #

def _minted(client, pat, reg, **kw) -> tuple[str, object]:
    """Issue a passport, make an allowed call, return (header, keys)."""
    keys = client.app.state.passport_keys
    _passport(reg, keys)
    r = _call(client, pat, **kw)
    assert r.json()["action"] == "allow"
    return r.headers[HEADER_NAME], keys


def test_gateway_allow_mints_a_receipt_the_tool_accepts(tmp_path):
    """Gateway allows → mints → the tool, in its own app with only the JWKS,
    executes the delete. This one assertion proves both sides hash identical
    params_schema, action_class and bound-param set."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat)
        assert r.status_code == 200
        assert r.json()["action"] == "allow"
        header = r.headers.get(HEADER_NAME)
        assert header, "an allow on a receipt-gated tool must mint a receipt"

    # verbatim — no decode/re-encode. Re-serializing would re-canonicalize the
    # JSON and invalidate the Ed25519 signature; passing the string through is
    # exactly what a real agent does.
    tool = _tool_client(keys)
    got = tool.delete("/customers/c-1", headers={HEADER_NAME: header})
    assert got.status_code == 200, got.text
    assert got.json() == {"deleted": "c-1"}


def test_the_minted_receipt_is_single_use(tmp_path):
    """The nonce is claimed on first use: the same header cannot delete twice."""
    client, pat, reg = _setup(tmp_path)
    with client:
        header, keys = _minted(client, pat, reg)
    tool = _tool_client(keys)
    assert tool.delete("/customers/c-1",
                       headers={HEADER_NAME: header}).status_code == 200
    assert tool.delete("/customers/c-1",
                       headers={HEADER_NAME: header}).status_code == 403


def test_the_minted_receipt_is_bound_to_that_customer(tmp_path):
    """A receipt minted for c-1 cannot delete c-2, and the failed attempt does
    not burn the nonce — c-1 is still deletable afterwards."""
    client, pat, reg = _setup(tmp_path)
    with client:
        header, keys = _minted(client, pat, reg)
    tool = _tool_client(keys)
    assert tool.delete("/customers/c-2",
                       headers={HEADER_NAME: header}).status_code == 403
    assert tool.delete("/customers/c-1",
                       headers={HEADER_NAME: header}).status_code == 200


# --------------------------------------------------------------------------- #
# 2-5. The paths that must NOT mint.
# --------------------------------------------------------------------------- #

def test_deny_mints_nothing_and_the_tool_refuses(tmp_path):
    """A deny hands back no capability, and the tool refuses the call. The
    assertion is the side effect, not the status: a 403 with the customer
    already deleted is precisely the failure this gate exists to prevent."""
    client, pat, reg = _setup(tmp_path, deny_delete=True)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat)
        assert r.json()["action"] == "deny"
        assert HEADER_NAME not in r.headers

    # nothing to present → refused, and the record is untouched
    tool = _tool_client(keys)
    assert tool.delete("/customers/c-1").status_code == 403
    assert tool.get("/customers/c-1").status_code == 403


def test_shared_token_has_no_human_so_no_receipt(tmp_path):
    """`auth.token` is a shared secret, not a person. It authenticates, the
    decision allows — and it gets no capability, because Principal is a human."""
    client, _pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, "t")           # the config's shared token
        assert r.status_code == 200
        assert r.json()["action"] == "allow"
        assert HEADER_NAME not in r.headers


def test_uncatalogued_tool_mints_nothing(tmp_path):
    """A tool absent from the catalog is not receipt-gated — no behaviour
    change for every existing tool."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat, tool="http.get", args={})
        assert r.json()["action"] == "allow"
        assert HEADER_NAME not in r.headers


def test_revoked_passport_mints_nothing(tmp_path):
    """A dead credential must not mint a live capability."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        reg.append_passport_event("pp-test", AGENT, "revoked", "test")
        r = _call(client, pat)
        assert r.json()["action"] == "allow"
        assert HEADER_NAME not in r.headers


def test_expired_passport_mints_nothing(tmp_path):
    """verify_tool_receipt checks revocation but NEVER passport expiry, so the
    issuer is the only thing standing between an expired passport and a live
    receipt."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys, expires_hours=-1)
        r = _call(client, pat)
        assert r.json()["action"] == "allow"
        assert HEADER_NAME not in r.headers


def test_no_passport_at_all_mints_nothing(tmp_path):
    client, pat, _reg = _setup(tmp_path)
    with client:
        r = _call(client, pat)
        assert r.json()["action"] == "allow"
        assert HEADER_NAME not in r.headers


def test_irreversible_without_its_bound_arg_mints_nothing(tmp_path):
    """Minting without the bound value would produce a receipt that fails the
    tool's instance check every time — refuse at issuance instead."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat, args={})
        assert r.json()["action"] == "allow"
        assert HEADER_NAME not in r.headers
