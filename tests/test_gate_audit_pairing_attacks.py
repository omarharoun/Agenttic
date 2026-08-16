"""ADVERSARY: attacks on the audit-Receipt / capability-token pairing.

Claim under attack:

    "every allowed gated call leaves an audit record that a third party can
    verify, and no audit record exists without a real allow behind it."

Each test below is a runnable exploit attempt. Attacks that are correctly
blocked stay as regression tests asserting the block. The harness is shared
with ``test_gate_issuance`` on purpose — attacking the same wiring the feature
tests bless is the point; a private harness could differ from the real route.
"""

from __future__ import annotations

import hashlib
import itertools
import json

import pytest

from agenttic.enforce.dashboard import dashboard_metrics
from agenttic.enforce.export import export_json, export_otel
from agenttic.gate.middleware import HEADER_NAME
from agenttic.passport.receipts import ReceiptError, ReceiptIssuer, find_receipt
from agenttic.schema.enforcement import EnforcementEvent
from agenttic.verifier.header import decode_passport_header
from tests.test_gate_issuance import AGENT, _call, _passport, _setup


def _audit_events(reg):
    return [e for e in reg.list_enforcement_events() if e["kind"] == "receipt"]


# --------------------------------------------------------------------------- #
# 0. POSITIVE CONTROL — if this fails, every "blocked" verdict below is vacuous.
# --------------------------------------------------------------------------- #

def test_positive_control_the_harness_detects_an_unbacked_receipt(tmp_path):
    """Forge an audit-receipt EVENT whose decision_ref names a decision that was
    never allowed, then ask the verifier about it.

    This is the control: it proves (a) the harness can write to the log the same
    way the issuer does, (b) ``find_receipt`` reconstructs whatever is there
    without re-checking anything, and (c) ``backed_by_allow`` actually goes
    False when the backing allow is absent. If this test passes, a
    ``backed_by_allow is True`` anywhere below means something.
    """
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat)
        real = find_receipt(reg, _audit_events(reg)[0]["detail"]["receipt_id"])
        assert real is not None
        receipt, session_id = real

        # same receipt, re-logged under a decision ref that has no allow event
        forged = receipt.model_copy(update={"receipt_id": "rcpt-forged"})
        detail = dict(_audit_events(reg)[0]["detail"])
        detail["receipt_id"] = "rcpt-forged"
        detail["decision_id"] = "dec-never-happened"
        reg.append_enforcement_event(EnforcementEvent(
            event_id="evt-forged", session_id=session_id, agent_id=AGENT,
            kind="receipt", action="allow", actor="passport",
            decision_ref="decision:dec-never-happened",
            policy_hash=receipt.policy_hash, detail=detail))

        issuer = ReceiptIssuer(reg, {}, keys)
        found = find_receipt(reg, "rcpt-forged")
        assert found is not None, "control: the log write must be visible"
        v = issuer.verify_receipt(found[0], session_id)
        assert v["backed_by_allow"] is False, (
            "CONTROL BROKEN: an unbacked receipt reported as backed — every "
            "backed_by_allow assertion in this file is meaningless")
        # and the genuine one is still backed, so the check is not just "always False"
        assert issuer.verify_receipt(receipt, session_id)["backed_by_allow"] is True
        assert forged.receipt_id == "rcpt-forged"
        assert r.json()["action"] == "allow"


# --------------------------------------------------------------------------- #
# 1. Minting an audit receipt with no allow behind it.
# --------------------------------------------------------------------------- #

def test_audit_receipt_refused_when_the_allow_was_never_logged(tmp_path):
    """Hard Rule 29, head on: hand the issuer a decision object that says
    ``allow`` but was never written to the log. It must refuse."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        passport = _passport(reg, keys)
        r = _call(client, pat)
        decision_json = r.json()

        from agenttic.schema.enforcement import Decision
        ghost = Decision(**decision_json).model_copy(
            update={"decision_id": "dec-ghost"})
        issuer = ReceiptIssuer(reg, {}, keys)
        with pytest.raises(ReceiptError):
            issuer.issue_receipt(passport, decision_json["session_id"], ghost,
                                 input_data={})
        assert len(_audit_events(reg)) == 1, "the refusal wrote nothing"


def test_a_receipt_event_cannot_back_itself_or_another_receipt(tmp_path):
    """The audit row is itself an event with ``action="allow"`` and a
    ``decision_ref``. If ``_logged_allow`` looked at action+ref without checking
    ``kind``, one audit receipt would authorise the next one forever. Delete the
    real decision event's backing by scoping to a session that only holds the
    receipt row."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        passport = _passport(reg, keys)
        r = _call(client, pat)
        body = r.json()
        row = _audit_events(reg)[0]

        # replay the receipt event alone into a fresh session id
        detail = dict(row["detail"])
        reg.append_enforcement_event(EnforcementEvent(
            event_id="evt-replay", session_id="sess-empty", agent_id=AGENT,
            kind="receipt", action="allow", actor="passport",
            decision_ref=f"decision:{body['decision_id']}",
            policy_hash=body["policy_hash"], detail=detail))

        from agenttic.schema.enforcement import Decision
        issuer = ReceiptIssuer(reg, {}, keys)
        with pytest.raises(ReceiptError):
            issuer.issue_receipt(passport, "sess-empty", Decision(**body),
                                 input_data={})
        # and the capability minter refuses on the same session for the same reason
        assert issuer.issue_tool_access(
            "sess-empty", Decision(**body), principal_id="jane@x.com",
            args={"customer_id": "c-1"}) is None


def test_an_allow_in_one_session_does_not_back_a_receipt_in_another(tmp_path):
    """``_logged_allow`` is session-scoped. A decision allowed in session A must
    not mint an audit receipt filed under session B — otherwise the log's
    session grouping stops meaning anything."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        passport = _passport(reg, keys)
        body = _call(client, pat).json()
        other = client.post("/api/enforce/sessions",
                            headers={"Authorization": f"Bearer {pat}"},
                            json={"agent_id": AGENT}).json()["session_id"]

        from agenttic.schema.enforcement import Decision
        with pytest.raises(ReceiptError):
            ReceiptIssuer(reg, {}, keys).issue_receipt(
                passport, other, Decision(**body), input_data={})


def test_a_denied_call_writes_no_audit_row_even_over_many_attempts(tmp_path):
    """Repeated denies must never accumulate audit records — a record asserts an
    action happened."""
    client, pat, reg = _setup(tmp_path, deny_delete=True)
    with client:
        _passport(reg, client.app.state.passport_keys)
        for _ in range(5):
            assert _call(client, pat).json()["action"] == "deny"
        assert _audit_events(reg) == []
        assert HEADER_NAME not in _call(client, pat).headers


# --------------------------------------------------------------------------- #
# 2. Do the two artifacts actually join?
# --------------------------------------------------------------------------- #

def test_two_calls_produce_two_receipts_that_do_not_cross_join(tmp_path):
    """Two gated allows in one session: each audit row must resolve to its OWN
    decision and its own capability. A shared or reused decision id here would
    let one signed record stand in for two real actions."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r1 = _call(client, pat, args={"customer_id": "c-1"})
        r2 = _call(client, pat, args={"customer_id": "c-2"})
        rows = _audit_events(reg)
        caps = [decode_passport_header(r.headers[HEADER_NAME]) for r in (r1, r2)]

    assert len(rows) == 2
    ids = [row["detail"]["decision_id"] for row in rows]
    assert ids == [r1.json()["decision_id"], r2.json()["decision_id"]]
    assert len(set(ids)) == 2
    assert [c["decision_id"] for c in caps] == [f"decision:{i}" for i in ids]
    # distinct receipt ids, distinct nonces, distinct input hashes
    assert len({row["detail"]["receipt_id"] for row in rows}) == 2
    assert len({c["nonce"] for c in caps}) == 2
    assert len({row["detail"]["input_sha256"] for row in rows}) == 2


def test_the_capability_decision_ref_resolves_to_the_audit_row(tmp_path):
    """A third party holding only the capability token must be able to walk to
    the audit record: capability.decision_id (a ref) → event.decision_ref →
    receipt detail. This is the join the whole pairing rests on."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        r = _call(client, pat)
        cap = decode_passport_header(r.headers[HEADER_NAME])
        events = reg.list_enforcement_events()

    matches = [e for e in events
               if e["kind"] == "receipt" and e["decision_ref"] == cap["decision_id"]]
    assert len(matches) == 1
    found = find_receipt(reg, matches[0]["detail"]["receipt_id"])
    assert found is not None
    receipt, session_id = found
    v = ReceiptIssuer(reg, {}, keys).verify_receipt(receipt, session_id)
    assert v["signature_valid"] and v["backed_by_allow"]
    # the two artifacts also agree on passport and policy
    assert receipt.passport_id == cap["passport_id"]
    assert receipt.policy_hash == cap["policy_hash"]


def test_verify_receipt_valid_flag_ignores_backing_a_caller_must_check_both(
        tmp_path):
    """TRAP, asserted so it cannot silently change: ``verify_receipt``'s headline
    ``valid`` is signature-only. Pointed at a session with no backing allow it
    still reports ``valid: True`` while ``backed_by_allow`` is False. A third
    party must read ``backed_by_allow``, not ``valid``."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        _call(client, pat)
        receipt, session_id = find_receipt(
            reg, _audit_events(reg)[0]["detail"]["receipt_id"])
        v = ReceiptIssuer(reg, {}, keys).verify_receipt(receipt, "sess-nonexistent")
    assert v["signature_valid"] is True
    assert v["backed_by_allow"] is False
    assert v["valid"] is True, (
        "behaviour changed: valid now accounts for backing — strictly stronger, "
        "update this test deliberately")


# --------------------------------------------------------------------------- #
# 3. Leakage into the append-only log.
# --------------------------------------------------------------------------- #

def test_the_capability_nonce_never_reaches_the_log_or_any_export(tmp_path):
    """The nonce is a single-use replay token. Anyone with log READ access who
    finds one can present it to the tool. Check the raw log, the redacted JSON
    export, the OTel export and the HTTP events endpoint — all four."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        cap = decode_passport_header(r.headers[HEADER_NAME])
        nonce = cap["nonce"]
        assert _audit_events(reg), "guard: an audit row was actually written"
        surfaces = {
            "log": json.dumps(reg.list_enforcement_events()),
            "export_json": export_json(reg),
            "export_otel": json.dumps(export_otel(reg)),
            "http_events": client.get(
                "/api/enforce/events",
                headers={"Authorization": f"Bearer {pat}"}).text,
        }
    assert nonce and len(nonce) >= 16
    for name, blob in surfaces.items():
        assert nonce not in blob, f"the capability nonce leaked into {name}"
    # nor any other capability field that would help replay it
    for name, blob in surfaces.items():
        assert cap["signature"] not in blob, f"capability signature leaked into {name}"


def test_args_are_not_recoverable_from_the_log_by_a_reader_who_never_saw_them(
        tmp_path):
    """EXPLOIT. ``input_sha256`` is an unsalted SHA-256 over the tool arguments,
    written into the append-only log on every gated allow. The log is readable
    by ANY authenticated caller (``GET /api/enforce/events`` carries no
    ``require_operator``), including the shared machine token that is denied a
    capability precisely because there is no human behind it.

    A tool argument drawn from a small or guessable domain — a customer id, an
    account number, an email, a filename — is recoverable by enumeration. This
    plays the attacker: read the log with the shared token, enumerate customer
    ids, and see whether the exact argument of another principal's call falls
    out.
    """
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        # jane@x.com deletes a specific customer
        victim_arg = {"customer_id": "c-1"}
        assert _call(client, pat, args=victim_arg).json()["action"] == "allow"

        # the attacker holds only the shared token: authenticated, no human, and
        # deliberately given no capability by this very feature.
        log = client.get("/api/enforce/events",
                         headers={"Authorization": "Bearer t"}).json()

    hashes = {e["detail"]["input_sha256"] for e in log if e["kind"] == "receipt"}
    assert hashes, "guard: the leak surface exists — an audit row was written"

    def _h(args):  # exactly the issuer's hashing: unsalted, canonical, no secret
        return hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()

    recovered = next(
        (cid for cid in (f"c-{n}" for n in itertools.chain(range(50), "x"))
         if _h({"customer_id": str(cid)}) in hashes), None)

    assert recovered is None, (
        f"args recovered from the append-only log by brute force: "
        f"customer_id={recovered!r}. input_sha256 is an unsalted hash over a "
        f"low-entropy argument space and the log is readable by any "
        f"authenticated principal.")


def test_the_audit_row_carries_no_payload_and_no_passport_secret(tmp_path):
    """Hard Rule 30 on the new production path: the row holds hashes and
    signatures, never argument values and never key material."""
    client, pat, reg = _setup(tmp_path)
    with client:
        keys = client.app.state.passport_keys
        _passport(reg, keys)
        _call(client, pat, args={"customer_id": "c-secret-42"})
        row = _audit_events(reg)[0]
        blob = json.dumps(row)
    assert "content" not in row["detail"]
    assert "c-secret-42" not in blob
    assert "customer_id" not in blob


# --------------------------------------------------------------------------- #
# 4. Event-count growth: does anything downstream shift?
# --------------------------------------------------------------------------- #

def test_audit_rows_do_not_move_any_dashboard_or_export_number(tmp_path):
    """The new event doubles the row count on a gated allow. Everything that
    aggregates the log filters on ``kind``, so no metric may move. Compare a
    gated session against an ungated one with the same number of allows."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        start = len(reg.list_enforcement_events())
        for _ in range(3):
            _call(client, pat, tool="http.get", args={})
        ungated = dashboard_metrics(reg, agent_id=AGENT)
        ungated_spans = len(export_otel(reg, agent_id=AGENT)["spans"])
        mid = len(reg.list_enforcement_events())

        for _ in range(3):
            _call(client, pat)
        gated = dashboard_metrics(reg, agent_id=AGENT)
        gated_spans = len(export_otel(reg, agent_id=AGENT)["spans"])
        grown = len(reg.list_enforcement_events()) - mid
        ungated_grown = mid - start

    assert gated["decisions"] == ungated["decisions"] * 2
    assert gated_spans == ungated_spans * 2          # one span per DECISION only
    assert gated["block_rate"] == ungated["block_rate"] == 0.0
    assert gated["fail_open_count"] == ungated["fail_open_count"]
    assert gated["by_action"] == {"allow": 6}
    assert len(_audit_events(reg)) == 3
    # documented growth: exactly ONE extra row per gated allow, no more. (Each
    # _call also opens a session, so the per-call baseline is session+decision.)
    assert ungated_grown == 6
    assert grown == 9, f"gated allow wrote {(grown - 6) / 3} extra rows, expected 1"


def test_no_receipt_kind_leaks_into_a_kind_filtered_consumer(tmp_path):
    """Belt and braces on the growth: nothing that reads the log by kind picks
    the new row up as a decision, an approval, a shadow block or an admin row."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        _call(client, pat)
        kinds = [e["kind"] for e in reg.list_enforcement_events()]
        row = _audit_events(reg)[0]
    assert kinds.count("receipt") == 1
    assert row["kind"] == "receipt" and row["actor"] == "passport"
    assert kinds.count("decision") == 1
    assert not any(k in kinds for k in ("shadow", "admin", "webhook", "preserved"))


# --------------------------------------------------------------------------- #
# 5. Does the pair ever disagree about what was permitted?
# --------------------------------------------------------------------------- #

def test_the_two_artifacts_agree_on_the_action_class(tmp_path):
    """A third party reading only the log must learn that this call was
    IRREVERSIBLE. The capability says ``irreversible`` (from the gate catalog);
    the audit row copies ``decision.action_class``, which is a different
    vocabulary entirely (read/write/unknown)."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        r = _call(client, pat)
        cap = decode_passport_header(r.headers[HEADER_NAME])
        audit = _audit_events(reg)[0]["detail"]

    assert cap["action_class"] == "irreversible"
    assert audit["action_class"] == cap["action_class"], (
        f"the audit record says action_class={audit['action_class']!r} for a "
        f"call the capability governed as {cap['action_class']!r} — the log "
        f"understates the blast radius of the very action it records")


def test_a_failed_audit_write_does_not_hand_out_a_live_capability(
        tmp_path, monkeypatch):
    """Fault injection: the audit write fails (disk full, db locked, tenant row
    error). The route's second ``try`` swallows it and the capability header has
    ALREADY been set — so a live, single-use, irreversible-action token goes out
    with no record of it in the log. Asserts the pairing property: either the
    call is recorded, or it is not permitted."""
    client, pat, reg = _setup(tmp_path)
    with client:
        _passport(reg, client.app.state.passport_keys)
        real_append = reg.append_enforcement_event

        def flaky(event):
            if getattr(event, "kind", None) == "receipt":
                raise RuntimeError("simulated registry failure")
            return real_append(event)

        monkeypatch.setattr(reg, "append_enforcement_event", flaky)
        r = _call(client, pat)
        assert r.json()["action"] == "allow"
        header = r.headers.get(HEADER_NAME)
        monkeypatch.undo()
        rows = _audit_events(reg)

    assert rows == [], "guard: the injected failure really suppressed the row"
    assert header is None, (
        "a capability token for an irreversible action was issued while its "
        "audit record silently failed — the log now disagrees with what was "
        "permitted, and nothing downstream can tell")
