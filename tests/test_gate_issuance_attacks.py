"""Attacks on the ISSUANCE side (§5) — "the gateway never hands out a
capability it shouldn't".

The verification side is covered by test_gate_attacks_1..4; nothing here
presents a forged receipt. Everything here tries to make the *gateway mint* a
receipt for an action, instance, principal, agent or tenant it never allowed.

Each test is a runnable exploit attempt. Ones that are blocked stay as
regression tests asserting the block.
"""

from __future__ import annotations

import copy
import json

import pytest
import yaml
from fastapi.testclient import TestClient

from agenttic.enforce.gateway import compute_policy_hash
from agenttic.gate.middleware import HEADER_NAME, InMemoryNonceStore
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.schema.passport import Passport, PassportClaims
from agenttic.server.app import create_app
from agenttic.server.pats import PatStore
from agenttic.verifier.header import decode_passport_header
from examples.receipt_gated_tool import CUSTOMER_ID_SCHEMA, build_demo_app

AGENT = "a"

#: the catalog entry the honest deployment ships (mirrors test_gate_issuance)
GOOD_ENTRY = {"action_class": "irreversible", "bound_params": ["customer_id"],
              "input_schema": CUSTOMER_ID_SCHEMA}


# --------------------------------------------------------------------------- #
# Harness.
# --------------------------------------------------------------------------- #


def _policy(rules: list[Rule] | None = None, agent_id: str = AGENT
            ) -> EnforcementPolicy:
    p = EnforcementPolicy(policy_id="p1", agent_id=agent_id, rules=rules or [])
    p.content_hash = compute_policy_hash(p)
    return p


def _config(tmp_path, *, tools=None, enforcement_extra: dict | None = None) -> str:
    cfg = {
        "models": {"agent_default": "a", "judge_strong": "j", "judge_light": "l"},
        "harness": {"timeout_seconds": 10, "max_parallel": 5,
                    "transport_retries": 1, "max_steps": 10},
        "scoring": {"calibration_threshold": 0.8},
        "live": {"sample_rate": 0.05, "drift_threshold": 0.15,
                 "drift_window_runs": 50},
        "paths": {"registry_db": str(tmp_path / "a.db"),
                  "review_dir": str(tmp_path / "r"),
                  "calibration_dir": str(tmp_path / "c")},
        "auth": {"required": True, "token": "t"},
        "security": {"login_max_attempts": 5, "login_lockout_seconds": 900},
        "enforcement": {
            "tool_access": {
                "gateway_id": "gw:test",
                "tools": {"delete_customer": copy.deepcopy(GOOD_ENTRY)}
                if tools is None else tools,
            },
        },
    }
    cfg["enforcement"].update(enforcement_extra or {})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


def _passport(reg, keys, *, passport_id="pp-test", expires_hours=1,
              agent_id=AGENT) -> Passport:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    claims = PassportClaims(
        agent_id=agent_id, tier="B", dossier_sha256="h", policy_hash="ph",
        issued_at=now, expires_at=now + timedelta(hours=expires_hours),
        status_url=f"https://agenttic.local/passport/{passport_id}/status",
        key_id=keys.key_id())
    p = Passport(passport_id=passport_id, claims=claims)
    p.signature = keys.sign(p.signing_input())
    reg.save_passport(p)
    return p


def _setup(tmp_path, *, rules=None, tools=None, enforcement_extra=None,
           role="operator", email="jane@x.com"):
    """(client, pat, reg). Passports must be created inside ``with client:``."""
    reg = Registry(tmp_path / "a.db")
    reg.save_policy(_policy(rules))
    pat = PatStore(reg.engine).create(user_email=email, tenant="default",
                                      role=role, name="e2e")["token"]
    client = TestClient(create_app(
        _config(tmp_path, tools=tools, enforcement_extra=enforcement_extra),
        registry=reg))
    return client, pat, reg


def _session(client, token, agent_id=AGENT):
    r = client.post("/api/enforce/sessions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"agent_id": agent_id})
    return r


def _call(client, token, *, tool="delete_customer", args=None, session_id=None,
          agent_id=AGENT, headers=None):
    auth = {"Authorization": f"Bearer {token}"}
    if session_id is None:
        session_id = _session(client, token, agent_id).json()["session_id"]
    return client.post("/api/enforce/tool-call",
                       headers={**auth, **(headers or {})},
                       json={"session_id": session_id, "tool_name": tool,
                             "args": {"customer_id": "c-1"} if args is None
                             else args})


def _tool_client(keys):
    return TestClient(build_demo_app(
        keys, status_fetcher=lambda url: {"status": "active"},
        nonce_store=InMemoryNonceStore()))


def _receipt(response) -> dict:
    return decode_passport_header(response.headers[HEADER_NAME])


# --------------------------------------------------------------------------- #
# 0. Positive control — if the harness is wrong, this fails.
# --------------------------------------------------------------------------- #


def test_positive_control_harness_really_mints_and_really_verifies(tmp_path):
    """The harness must (a) actually mint a usable capability and (b) actually
    run the tool's verification. A single flipped byte in the signature must
    turn the same call into a 403 — otherwise every "blocked" below is vacuous
    because nothing was ever being checked."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat)
        assert r.json()["action"] == "allow"
        header = r.headers[HEADER_NAME]

    from agenttic.verifier.header import encode_passport_header
    bad = decode_passport_header(header)
    sig = bad["signature"]
    bad["signature"] = ("B" if sig[0] != "B" else "C") + sig[1:]

    tool = _tool_client(keys)
    assert tool.delete("/customers/c-1", headers={
        HEADER_NAME: encode_passport_header(bad)}).status_code == 403
    assert tool.delete("/customers/c-1",
                       headers={HEADER_NAME: header}).status_code == 200


# --------------------------------------------------------------------------- #
# 1. Lane 2 transform — the receipt must never bind args the tool never saw.
# --------------------------------------------------------------------------- #


def test_redacted_bound_param_never_mints(tmp_path):
    """The named attack: Lane 2 TRANSFORMS args, but ``issue_tool_access`` is
    handed the ORIGINAL ``body.args``. If a transform could ever be an allow,
    the receipt would bind the pre-redaction value while the tool executes on
    the post-redaction one. Make the *bound* param itself trip redaction."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat, args={"customer_id": "jane@x.com"})
        assert r.json()["action"] == "transform", r.json()
        assert HEADER_NAME not in r.headers


def test_redacted_sibling_arg_never_mints(tmp_path):
    """Same, with the secret in an unbound sibling arg: still a transform, so
    still no capability."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat,
                  args={"customer_id": "c-1", "note": "sk-abcdefgh12345"})
        assert r.json()["action"] == "transform", r.json()
        assert HEADER_NAME not in r.headers


def test_require_approval_never_mints(tmp_path):
    """An approval-gated call is not an allow — no capability before the human."""
    rules = [Rule(rule_id="r1", lane="lane1", action="require_approval",
                  matcher={"tool": "delete_customer"}, origin="t")]
    client, pat, reg = _setup(tmp_path, rules=rules)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        assert r.json()["action"] == "require_approval"
        assert HEADER_NAME not in r.headers


# --------------------------------------------------------------------------- #
# 2. Confused deputy on the principal.
# --------------------------------------------------------------------------- #


def test_principal_cannot_be_spoofed_by_headers_or_args(tmp_path):
    """``principal_id`` comes from the authenticated PAT, never from anything
    the caller sends. Try every plausible injection point at once."""
    client, pat, reg = _setup(tmp_path, email="jane@x.com")
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat,
                  # deliberately not email-shaped: an email in args trips Lane 2
                  # redaction and the call becomes a transform, which mints
                  # nothing and would prove nothing about the principal.
                  args={"customer_id": "c-1", "user_email": "sub:root",
                        "principal_id": "root", "principal": {"id": "sub:root"}},
                  headers={"X-User-Email": "root@x.com",
                           "X-Forwarded-User": "root@x.com"})
        assert r.json()["action"] == "allow"
        assert _receipt(r)["principal"]["id"] == "sub:jane@x.com"


def test_viewer_role_mints_nothing(tmp_path):
    """Below operator the endpoint is refused outright — no decision, no
    receipt."""
    client, pat, reg = _setup(tmp_path, role="viewer")
    with client:
        _passport(reg, client.app.state.passport_keys)
        assert _session(client, pat).status_code == 403
        r = client.post("/api/enforce/tool-call",
                        headers={"Authorization": f"Bearer {pat}"},
                        json={"session_id": "sess-anything",
                              "tool_name": "delete_customer",
                              "args": {"customer_id": "c-1"}})
        assert r.status_code == 403
        assert HEADER_NAME not in r.headers


def test_evaluator_role_can_mint_a_delete_capability(tmp_path):
    """``evaluator`` is documented as an INDEPENDENT principal isolated to
    certified-run artifacts, yet ``ROLES["evaluator"] == ROLES["operator"]`` so
    it clears ``require_operator``. It therefore mints a live, signed delete
    capability for an agent it does not own.

    Recorded as observed behaviour, not a crypto break: the same endpoint was
    already reachable by this role. What changed is that its response is now a
    capability a third-party tool honours.
    """
    client, pat, reg = _setup(tmp_path, role="evaluator", email="ev@x.com")
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat)
        assert r.json()["action"] == "allow"
        header = r.headers.get(HEADER_NAME)
    assert header, "evaluator got no receipt — role isolation holds"
    tool = _tool_client(keys)
    assert tool.delete("/customers/c-1",
                       headers={HEADER_NAME: header}).status_code == 200


# --------------------------------------------------------------------------- #
# 3. Session / agent / tenant confusion.
# --------------------------------------------------------------------------- #


def test_session_for_an_agent_without_a_policy_is_refused(tmp_path):
    """``_call`` lets the caller name any agent_id. Naming one with no policy
    cannot manufacture a session (and so cannot reach issuance at all)."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys, agent_id="other",
                  passport_id="pp-other")
        assert _session(client, pat, agent_id="other").status_code == 404


def test_cross_tenant_session_id_is_not_usable(tmp_path):
    """Sessions live on the tenant's own gateway instance. A tenant-B operator
    presenting tenant A's session_id gets a 404, not a receipt against A."""
    client, pat_a, reg = _setup(tmp_path)
    pat_b = PatStore(reg.engine).create(user_email="bob@y.com", tenant="t2",
                                        role="operator", name="b")["token"]
    with client:
        _passport(reg, client.app.state.passport_keys)
        sess_a = _session(client, pat_a).json()["session_id"]
        r = client.post("/api/enforce/tool-call",
                        headers={"Authorization": f"Bearer {pat_b}"},
                        json={"session_id": sess_a, "tool_name": "delete_customer",
                              "args": {"customer_id": "c-1"}})
        assert r.status_code == 404
        assert HEADER_NAME not in r.headers


def test_tenant_b_cannot_mint_against_tenant_a_passport(tmp_path):
    """Tenant B runs its own agent "a" with its own passport. Its receipt must
    name B's passport — never the identically-named agent's passport in A."""
    client, pat_a, reg = _setup(tmp_path)
    pat_b = PatStore(reg.engine).create(user_email="bob@y.com", tenant="t2",
                                        role="operator", name="b")["token"]
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys, passport_id="pp-tenant-a")
        ws_b = client.app.state.workspaces.get("t2")
        ws_b.reg.save_policy(_policy())
        _passport(ws_b.reg, keys, passport_id="pp-tenant-b")

        r = _call(client, pat_b)
        assert r.json()["action"] == "allow"
        assert _receipt(r)["passport_id"] == "pp-tenant-b"

        r = _call(client, pat_a)
        assert _receipt(r)["passport_id"] == "pp-tenant-a"


def test_revoked_session_must_not_mint(tmp_path):
    """BYPASS — this test FAILS today.

    The agent trips a ``revoke_access`` rule. The gateway kills the session
    (``gateway.py:258-261`` sets ``session.active = False``,
    ``session.revoked = True``). Those two flags are written in exactly two
    places and READ IN NONE: ``EnforcementGateway.get_session`` (gateway.py:105)
    returns the dead session unconditionally, ``_evaluate`` never consults it,
    and ``issue_tool_access`` (receipts.py:122) takes a ``session_id`` and
    asserts nothing about the session's state.

    So the next call in the SAME revoked session evaluates to ``allow`` and the
    gateway signs a live, single-use, irreversible DELETE capability that the
    third-party tool then honours — after access was revoked. Nothing here is
    misconfiguration: the rule fired exactly as written.
    """
    rules = [Rule(rule_id="r1", lane="lane1", action="revoke_access",
                  matcher={"tool": "exfil"}, origin="t")]
    client, pat, reg = _setup(tmp_path, rules=rules)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        sid = _session(client, pat).json()["session_id"]
        killed = _call(client, pat, tool="exfil", args={}, session_id=sid)
        assert killed.json()["action"] == "revoke_access"
        gw = client.app.state.workspaces.get("default").enforcer
        assert gw.get_session(sid).revoked is True    # the session IS dead

        r = _call(client, pat, session_id=sid)
        header = r.headers.get(HEADER_NAME)

    assert header is None, (
        "a revoked session minted a capability; it is usable: "
        f"{_tool_client(keys).delete('/customers/c-1', headers={HEADER_NAME: header}).status_code}")


# --------------------------------------------------------------------------- #
# 4. Passport selection.
# --------------------------------------------------------------------------- #


def test_revoking_the_newest_passport_falls_back_to_the_older_one(tmp_path):
    """``_active_passport`` walks newest-first and takes the first active,
    unexpired one. Revoke the newest and an older, superseded-but-never-revoked
    passport is used instead.

    Not a bypass: "active" is defined per-passport by the same
    ``passport_status`` the tool's own live revocation check reads, so the tool
    independently agrees the named passport is live. It is only a resurrection
    if superseding is meant to imply revocation — it is not modelled anywhere.
    """
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys, passport_id="pp-old")
        _passport(reg, keys, passport_id="pp-new")
        reg.append_passport_event("pp-new", AGENT, "revoked", "compromised")
        r = _call(client, pat)
        assert _receipt(r)["passport_id"] == "pp-old"
        assert reg.passport_status("pp-old") == "active"

        # revoke it too and nothing mints — the walk has no other fallback
        reg.append_passport_event("pp-old", AGENT, "revoked", "cleanup")
        assert HEADER_NAME not in _call(client, pat).headers


def test_newest_active_wins_over_older_active(tmp_path):
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys, passport_id="pp-old")
        _passport(reg, client.app.state.passport_keys, passport_id="pp-new")
        assert _receipt(_call(client, pat))["passport_id"] == "pp-new"


def test_expired_newest_falls_back_only_to_an_unexpired_passport(tmp_path):
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys, passport_id="pp-live")
        _passport(reg, keys, passport_id="pp-dead", expires_hours=-1)
        assert _receipt(_call(client, pat))["passport_id"] == "pp-live"


# --------------------------------------------------------------------------- #
# 5. Catalog poisoning.
# --------------------------------------------------------------------------- #


def _tools(entry) -> dict:
    return {"delete_customer": entry}


def test_missing_action_class_with_bound_params_mints_nothing(tmp_path):
    """A catalog entry that names bound_params but omits action_class used to
    fall through ``entry.get("action_class", "read")`` and mint a READ receipt
    with NO instance binding — useless (action_class is inside action_hash, so
    the tool 403s it) but weaker than the author asked for, and it surfaced as
    an unexplained 403 in another process. ``tool_access_entry`` now refuses the
    entry outright, so the misconfiguration is visible at the gateway."""
    client, pat, reg = _setup(
        tmp_path, tools=_tools({"bound_params": ["customer_id"],
                                "input_schema": CUSTOMER_ID_SCHEMA}))
    with client:
        _passport(reg, client.app.state.passport_keys)
        assert HEADER_NAME not in _call(client, pat).headers


def test_irreversible_with_empty_bound_params_mints_nothing(tmp_path):
    client, pat, reg = _setup(
        tmp_path, tools=_tools({"action_class": "irreversible",
                                "bound_params": [],
                                "input_schema": CUSTOMER_ID_SCHEMA}))
    with client:
        _passport(reg, client.app.state.passport_keys)
        assert HEADER_NAME not in _call(client, pat).headers


def test_bound_params_as_a_bare_string_mints_nothing(tmp_path):
    """YAML ``bound_params: customer_id`` (no list) iterates as characters —
    none of which are in args, so it fails closed rather than binding nothing."""
    client, pat, reg = _setup(
        tmp_path, tools=_tools({"action_class": "irreversible",
                                "bound_params": "customer_id",
                                "input_schema": CUSTOMER_ID_SCHEMA}))
    with client:
        _passport(reg, client.app.state.passport_keys)
        assert HEADER_NAME not in _call(client, pat).headers


def test_missing_input_schema_mints_a_receipt_the_tool_refuses(tmp_path):
    client, pat, reg = _setup(
        tmp_path, tools=_tools({"action_class": "irreversible",
                                "bound_params": ["customer_id"]}))
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        header = _call(client, pat).headers[HEADER_NAME]
    tool = _tool_client(keys)
    assert tool.delete("/customers/c-1",
                       headers={HEADER_NAME: header}).status_code == 403


def test_unknown_action_class_mints_nothing(tmp_path):
    """A catalog ``action_class`` outside the receipt's Literal. With
    bound_params named, ``tool_access_entry`` refuses the entry before the
    minter ever sees it, so nothing is issued and nothing raises.

    (Without bound_params the value does reach the minter and raises a pydantic
    ValidationError — see ``test_unknown_action_class_alone_is_caught_at_the_route``.
    ``issue_tool_access`` is documented as not raise-proof for exactly this
    config fault; the route turns it into "no header", still closed.)"""
    client, pat, reg = _setup(
        tmp_path, tools=_tools({"action_class": "superuser",
                                "bound_params": ["customer_id"],
                                "input_schema": CUSTOMER_ID_SCHEMA}))
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        assert r.status_code == 200
        assert HEADER_NAME not in r.headers


def test_unknown_action_class_alone_is_caught_at_the_route(tmp_path):
    """The same bad class with no bound_params reaches the minter and raises.
    The route must still answer 200-with-no-header, never 500, never a header."""
    from agenttic.passport.receipts import ReceiptIssuer

    entry = {"action_class": "superuser", "input_schema": CUSTOMER_ID_SCHEMA}
    client, pat, reg = _setup(tmp_path, tools=_tools(entry))
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        assert r.status_code == 200
        assert HEADER_NAME not in r.headers          # fail closed at the route

        # underneath it, the config fault surfaces rather than minting
        gw = client.app.state.workspaces.get("default").enforcer
        sid = _session(client, pat).json()["session_id"]
        decision = gw.evaluate_tool_call(sid, "delete_customer",
                                         {"customer_id": "c-1"})
        issuer = ReceiptIssuer(
            reg, yaml.safe_load(open(_config(tmp_path, tools=_tools(entry)))),
            client.app.state.passport_keys)
        with pytest.raises(Exception):
            issuer.issue_tool_access(sid, decision, principal_id="jane@x.com",
                                     args={"customer_id": "c-1"})


def test_tools_block_of_the_wrong_shape_mints_nothing(tmp_path):
    """``tools`` as a list, not a mapping: ``.get`` on a list raises inside
    ``tool_access_entry``. Must still be no-header, never a header."""
    client, pat, reg = _setup(tmp_path, tools=["delete_customer"])
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        assert r.status_code == 200
        assert HEADER_NAME not in r.headers


def test_tool_name_variants_are_not_catalogued(tmp_path):
    """Exact-match catalog lookup: a near-miss tool name mints nothing (and so
    cannot be used to dodge a name-matched deny rule and still get a token)."""
    rules = [Rule(rule_id="r1", lane="lane1", action="deny",
                  matcher={"tool": "delete_customer"}, origin="t")]
    client, pat, reg = _setup(tmp_path, rules=rules)
    with client:
        _passport(reg, client.app.state.passport_keys)
        for name in ("delete_customer ", "DELETE_CUSTOMER", "delete_customer\n",
                     "./delete_customer"):
            r = _call(client, pat, tool=name)
            assert HEADER_NAME not in r.headers, name


# --------------------------------------------------------------------------- #
# 6. Type confusion in bound_values.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [1, 1.0, True, None, ["c-1"], {"id": "c-1"},
                                   "c-1 ", " c-1"])
def test_non_string_bound_value_mints_only_an_unusable_receipt(tmp_path, value):
    """``args[n]`` is taken verbatim from JSON and hashed through canonical_json.
    The tool re-hashes a path param, which is always a ``str`` — so every
    non-string (and every whitespace variant) yields a receipt that matches no
    instance the tool can ever be called with. Fails closed, but at the tool."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat, args={"customer_id": value})
        if r.json()["action"] != "allow":
            pytest.skip("decision was not an allow for this value")
        header = r.headers[HEADER_NAME]
    tool = _tool_client(keys)
    for path in ("c-1", "1", "1.0", "true", "True", "null", "None"):
        assert tool.delete(f"/customers/{path}", headers={
            HEADER_NAME: header}).status_code == 403, (value, path)


def test_extra_args_do_not_widen_the_binding(tmp_path):
    """Only the catalogued bound_params are hashed; smuggling extra keys cannot
    change what the receipt is bound to."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        a = _receipt(_call(client, pat, args={"customer_id": "c-1"}))
        b = _receipt(_call(client, pat, args={"customer_id": "c-1",
                                              "bound_params": ["nope"],
                                              "action_class": "read"}))
    assert a["bound_param_names"] == b["bound_param_names"] == ["customer_id"]
    assert a["action_class"] == b["action_class"] == "irreversible"


def test_bound_value_null_is_not_treated_as_absent(tmp_path):
    """``n not in args`` is a key check: an explicit null passes it and binds
    None. The receipt is unusable (a path param is never None) — assert it can
    never delete anything rather than that it is never minted."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat, args={"customer_id": None})
        header = r.headers.get(HEADER_NAME)
    if not header:
        return  # minting refused outright: also fine
    tool = _tool_client(keys)
    for path in ("c-1", "null", "None", "0"):
        assert tool.delete(f"/customers/{path}",
                           headers={HEADER_NAME: header}).status_code == 403


# --------------------------------------------------------------------------- #
# 7. Fail-open allows.
# --------------------------------------------------------------------------- #


def test_fail_open_allow_mints_nothing(tmp_path):
    """A Lane-1 crash on a read-class tool applies ``fail_policy.read: open`` →
    ``action=allow, fail_open=True``, and the DENY rule sitting behind the
    crashing rule never runs.

    An unenforced call surviving is the fail-open policy working as written. A
    *receipt* for it is not: the token asserts the action was governed by a
    scoped, signed decision, and this decision could not be evaluated. So the
    call proceeds ungoverned and the capability is withheld — ``issue_tool_access``
    returns None on ``decision.fail_open``.

    The crashing rule is ``max_calls: "abc"`` (``int()`` raises inside Lane 1) —
    policy-authored, not attacker-supplied.
    """
    rules = [
        Rule(rule_id="boom", lane="lane1", action="allow",
             matcher={"tool": "delete_customer", "max_calls": "abc"}, origin="t"),
        Rule(rule_id="deny", lane="lane1", action="deny",
             matcher={"tool": "delete_customer"}, origin="t"),
    ]
    client, pat, reg = _setup(
        tmp_path, rules=rules,
        enforcement_extra={"fail_policy": {"write": "closed", "read": "open"},
                           "action_classes": {"read": ["delete_customer"]}})
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        body = r.json()
        # the decision itself still allows — only the capability is withheld
        assert body["action"] == "allow" and body["fail_open"] is True, body
        assert HEADER_NAME not in r.headers


def test_fail_closed_allow_is_the_default_for_unknown_class(tmp_path):
    """Without the read/open config above the same crash denies — the default
    fail policy for an unclassified tool is closed, and a deny mints nothing."""
    rules = [
        Rule(rule_id="boom", lane="lane1", action="allow",
             matcher={"tool": "delete_customer", "max_calls": "abc"}, origin="t"),
    ]
    client, pat, reg = _setup(tmp_path, rules=rules)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        assert r.json()["action"] == "deny"
        assert HEADER_NAME not in r.headers


# --------------------------------------------------------------------------- #
# 8. Nothing minted is ever reusable beyond its one call.
# --------------------------------------------------------------------------- #


def test_two_receipts_for_the_same_instance_are_independent(tmp_path):
    """Minting is not idempotent: each allow carries its own nonce, so burning
    one receipt does not burn the other. Documents that "single-use" is per
    receipt, not per instance — N allows really do buy N deletes."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        h1 = _call(client, pat).headers[HEADER_NAME]
        h2 = _call(client, pat).headers[HEADER_NAME]
    assert decode_passport_header(h1)["nonce"] != decode_passport_header(h2)["nonce"]
    tool = _tool_client(keys)
    assert tool.delete("/customers/c-1", headers={HEADER_NAME: h1}).status_code == 200
    assert tool.delete("/customers/c-1", headers={HEADER_NAME: h2}).status_code == 200


def test_receipt_is_never_written_to_the_enforcement_log(tmp_path):
    """The nonce must not be readable by anyone with log access — a logged
    nonce is a replay handed to every log reader."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        nonce = _receipt(r)["nonce"]
        blob = json.dumps(reg.list_enforcement_events())
    assert nonce not in blob
