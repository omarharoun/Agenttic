"""Signed action receipts (SPEC-2 T32.1).

A receipt binds a passport to ONE allowed action: tool_call_ref, action class,
policy hash, decision id, and input/output **hashes** (no payloads by default,
Hard Rule 30). Receipts ARE :class:`EnforcementEvent`s — **none can exist without
a logged allow-decision** (Hard Rule 29).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime

from agenttic.schema.enforcement import EnforcementEvent
from agenttic.schema.passport import Receipt


def _digest(key: bytes, data) -> str:
    """Keyed, not bare.

    ``input_sha256`` is persisted into the append-only enforcement log, which
    any authenticated principal can read (``GET /api/enforce/events``). Tool
    arguments come from small, guessable spaces — a customer id, an account
    number, an email, a filename — so an unsalted digest of them is a lookup
    table rather than a commitment: enumerate the space, hash each candidate,
    read the exact argument of somebody else's call straight out of the log.

    Under a key only the issuer holds it stays a commitment the issuer can
    re-check and a log reader cannot invert. Same reason ``compute_bound_params``
    salts the bound values with the nonce it never logs.
    """
    if data is None:
        return ""
    payload = data if isinstance(data, str) else json.dumps(data, sort_keys=True,
                                                            default=str)
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


class ReceiptError(RuntimeError):
    """A receipt could not be issued because there is no logged allow-decision."""


def tool_access_entry(cfg: dict, tool_name: str) -> dict | None:
    """The receipt-gated catalog entry for ``tool_name``, or None if this tool is
    not receipt-gated — the default. Same absent-means-not-applicable contract as
    :func:`agenttic.enforce.lanes.action_class_of`."""
    tools = ((cfg or {}).get("enforcement", {})
             .get("tool_access", {}).get("tools") or {})
    if not isinstance(tools, dict):
        return None                    # a malformed block gates nothing
    entry = tools.get(tool_name)
    if not isinstance(entry, dict):
        return None
    # An entry that names bound params but does not declare itself irreversible
    # would mint a receipt with NO instance binding — weaker than the author
    # plainly intended. Refuse the entry rather than silently downgrade it.
    if entry.get("bound_params") and entry.get("action_class") != "irreversible":
        return None
    return entry


class ReceiptIssuer:
    def __init__(self, reg, cfg: dict, key_manager):
        self.reg = reg
        self.cfg = cfg or {}
        self.keys = key_manager

    def _hash_key(self) -> bytes:
        """The HMAC key for :func:`_digest`, derived from the signing key.

        A domain-separated signature: deterministic (Ed25519 is), so the same
        deployment re-derives the same key and can re-check an old receipt's
        input hash, and secret, because producing it needs the private key. An
        ephemeral dev key means old hashes stop re-checking after a restart —
        exactly like the signatures already do, and already reported DEGRADED.
        """
        if getattr(self, "_hk", None) is None:
            self._hk = self.keys.sign(
                {"purpose": "agenttic.receipt.payload-digest.v1"}).encode()
        return self._hk

    def _logged_allow(self, session_id: str, decision_ref: str) -> bool:
        for e in self.reg.list_enforcement_events(session_id):
            if (e.get("kind") == "decision" and e.get("decision_ref") == decision_ref
                    and e.get("action") == "allow"):
                return True
        return False

    def issue_receipt(self, passport, session_id: str, decision, *,
                      input_data=None, output_data=None,
                      include_content: bool = False,
                      parent_receipt_id: str | None = None) -> Receipt:
        """Issue a receipt for an allowed action. Refuses unless the decision has
        a logged allow. By default only hashes are recorded; ``include_content``
        opts in to storing (redaction-checked) payloads."""
        if decision.action != "allow":
            raise ReceiptError(
                f"cannot issue a receipt for a non-allow decision "
                f"({decision.action})")
        if not self._logged_allow(session_id, decision.ref()):
            raise ReceiptError(
                "no logged allow-decision backs this receipt (Hard Rule 29)")

        # The gate catalog's class where there is one. ``Decision.action_class``
        # is Literal[read|write|unknown] and cannot say "irreversible", so a
        # record of a gated delete would otherwise read "unknown" — understating
        # the blast radius of the very action it certifies, and disagreeing with
        # the capability token minted for the same decision.
        entry = tool_access_entry(self.cfg, decision.tool_name)
        key = self._hash_key()
        receipt = Receipt(
            receipt_id=f"rcpt-{uuid.uuid4().hex[:12]}",
            passport_id=passport.passport_id, agent_id=decision.agent_id,
            tool_call_ref=f"toolcall:{decision.tool_name}",
            action_class=(entry or {}).get("action_class") or decision.action_class,
            policy_hash=decision.policy_hash,
            decision_id=decision.decision_id,
            input_sha256=_digest(key, input_data),
            output_sha256=_digest(key, output_data),
            parent_receipt_id=parent_receipt_id, key_id=self.keys.key_id())
        receipt.signature = self.keys.sign(receipt.signing_input())

        # receipts ARE events (Hard Rule 29): persisted in the append-only log.
        detail = {"receipt_id": receipt.receipt_id,
                  "passport_id": receipt.passport_id,
                  "tool_call_ref": receipt.tool_call_ref,
                  "action_class": receipt.action_class,
                  "policy_hash": receipt.policy_hash,
                  "decision_id": receipt.decision_id,
                  "input_sha256": receipt.input_sha256,
                  "output_sha256": receipt.output_sha256,
                  "parent_receipt_id": receipt.parent_receipt_id,
                  "key_id": receipt.key_id, "signature": receipt.signature,
                  "created_at": receipt.created_at.isoformat()}
        if include_content:
            from agenttic.enforce.self_security import redact_obj
            detail["content"] = {"input": redact_obj(input_data),
                                 "output": redact_obj(output_data)}
        self.reg.append_enforcement_event(EnforcementEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}", session_id=session_id,
            agent_id=decision.agent_id, kind="receipt", action="allow",
            actor="passport", decision_ref=decision.ref(),
            policy_hash=decision.policy_hash, detail=detail))
        return receipt

    # -- tool access receipts (capability tokens, §5) -------------------------

    def active_passport(self, agent_id: str, now: datetime | None = None):
        """The newest active, unexpired passport for ``agent_id``, or None.

        ``verify_tool_receipt`` step 5 checks *revocation* only — it never looks
        at passport expiry — so this is the only place expiry gates a receipt.
        """
        for row in reversed(self.reg.list_passports(agent_id)):
            if row.get("status") != "active":
                continue
            try:
                p = self.reg.get_passport(row["passport_id"])
            except Exception:  # noqa: BLE001 — an unreadable passport is not a
                continue       # usable one; keep looking rather than raising
            if p is not None and not p.claims.is_expired(now):
                return p
        return None

    def issue_tool_access(self, session_id: str, decision, *,
                          principal_id: str | None,
                          args: dict | None = None,
                          now: datetime | None = None):
        """Mint a Tool Access Receipt for an allowed call on a receipt-gated tool.

        Returns None on every ineligible path rather than raising. This diverges
        from :meth:`issue_receipt`, which raises ``ReceiptError``: there a
        non-allow decision is a fault, here "this tool is not receipt-gated" is
        the ordinary case. No receipt means the tool refuses the call, so every
        early return below fails closed.

        It is not raise-*proof*: a catalog entry whose ``action_class`` is
        outside the receipt's Literal raises a ``ValidationError`` from the
        minter. That is a config fault, it surfaces loudly, and the caller in
        ``routes/enforce.py`` turns it into "no header" — still closed.

        Nothing is written to the append-only log: a capability token is issued
        *to permit*, and the backing decision is already logged. If issuance
        non-repudiation is wanted later, log receipt_id/action_hash/expires_at —
        never the nonce, which would hand a replay to anyone who can read it.

        Value types matter: ``bound_values`` are hashed through ``canonical_json``,
        so ``{"id": "1"}`` and ``{"id": 1}`` produce different receipts. The tool
        re-hashes its own parameter values (a FastAPI path param is a ``str``), so
        passing an int for a string-typed param mints a receipt it always refuses.
        """
        entry = tool_access_entry(self.cfg, decision.tool_name)
        if entry is None:
            return None                       # not receipt-gated
        if decision.action != "allow":
            return None                       # deny/transform/approval/terminate
        if decision.fail_open:
            # A fail-open allow means a lane could not be EVALUATED and the
            # read-class policy let it through. That is a survivable gap for an
            # unenforced call, but a receipt asserts the action was governed by
            # a scoped, signed decision — which this one was not. Whatever rule
            # sat behind the failing one never ran.
            return None
        if not principal_id:
            return None                       # no human ⇒ no capability
        if not self._logged_allow(session_id, decision.ref()):
            return None                       # Hard Rule 29
        passport = self.active_passport(decision.agent_id, now)
        if passport is None:
            return None                       # revoked, expired, or absent

        action_class = entry.get("action_class", "read")
        names = list(entry.get("bound_params") or [])
        bound_values = None
        if action_class == "irreversible":
            args = args or {}
            if not names or any(n not in args for n in names):
                return None                   # would fail the tool's step 4
            bound_values = {n: args[n] for n in names}

        from agenttic.certification.hashing import sha256_hex
        from agenttic.gate.receipt import Principal, issue_tool_access_receipt
        tool_access = (self.cfg.get("enforcement", {}).get("tool_access") or {})
        return issue_tool_access_receipt(
            self.keys, tool=decision.tool_name, action_class=action_class,
            params_schema=entry.get("input_schema"),
            passport_id=passport.passport_id,
            passport_hash=sha256_hex(passport.signing_input()),
            principal=Principal(id=f"sub:{principal_id}",
                                via=[f"agent:{decision.agent_id}"]),
            gateway_id=tool_access.get("gateway_id", "gw:local"),
            # decision.ref(), not the bare id: this is what joins against
            # EnforcementEvent.decision_ref in the log. The audit Receipt above
            # stores the bare id — deliberate, only one of the two is greppable.
            decision_id=decision.ref(), policy_hash=decision.policy_hash,
            bound_values=bound_values, now=now)

    def verify_receipt(self, receipt: Receipt, session_id: str | None = None
                       ) -> dict:
        """Verify a receipt's signature and that a backing allow-decision exists."""
        from agenttic.passport.keys import verify_payload
        kr = self.keys.keyref_for(receipt.key_id)
        sig_valid = kr is not None and verify_payload(
            kr.public_key_b64, receipt.signing_input(), receipt.signature)
        backed = self._logged_allow(
            session_id, f"decision:{receipt.decision_id}") if session_id else True
        return {"receipt_id": receipt.receipt_id, "signature_valid": sig_valid,
                "backed_by_allow": backed, "valid": sig_valid}


def find_receipt(reg, receipt_id: str) -> "tuple[Receipt, str] | None":
    """Locate a receipt (and its session) by id from the append-only log."""
    for e in reg.list_enforcement_events():
        if e.get("kind") == "receipt" and (e.get("detail") or {}).get(
                "receipt_id") == receipt_id:
            r = load_receipt_from_event(e)
            return (r, e.get("session_id", "")) if r else None
    return None


def verify_chain(reg, receipt_id: str, key_manager, *, max_hops: int = 32) -> dict:
    """Walk a delegation chain from ``receipt_id`` up through ``parent_receipt_id``
    to the human principal (the root receipt with no parent), carrying every hop's
    policy hash. Names a broken hop; every hop's signature is verified."""
    hops: list[dict] = []
    problems: list[str] = []
    current = receipt_id
    seen = set()
    principal = None
    for _ in range(max_hops):
        if current in seen:
            problems.append(f"cycle at receipt {current}")
            break
        seen.add(current)
        found = find_receipt(reg, current)
        if found is None:
            problems.append(f"broken hop: receipt {current} does not resolve")
            break
        receipt, session_id = found
        sig = key_manager.keyref_for(receipt.key_id)
        from agenttic.passport.keys import verify_payload
        sig_valid = sig is not None and verify_payload(
            sig.public_key_b64, receipt.signing_input(), receipt.signature)
        if not sig_valid:
            problems.append(f"invalid signature at receipt {current}")
        hops.append({"receipt_id": receipt.receipt_id,
                     "agent_id": receipt.agent_id,
                     "policy_hash": receipt.policy_hash,
                     "passport_id": receipt.passport_id,
                     "signature_valid": sig_valid})
        if not receipt.parent_receipt_id:
            # root receipt → resolves to the human principal behind the passport
            principal = {"passport_id": receipt.passport_id,
                         "agent_id": receipt.agent_id}
            break
        current = receipt.parent_receipt_id
    else:
        problems.append("max hops exceeded (possible unbounded chain)")

    return {"resolved": principal is not None and not problems,
            "hops": hops, "principal": principal, "problems": problems}


def load_receipt_from_event(event: dict) -> Receipt | None:
    """Reconstruct a Receipt from its persisted enforcement event."""
    d = event.get("detail") or {}
    if not d.get("receipt_id"):
        return None
    return Receipt(
        receipt_id=d["receipt_id"], passport_id=d.get("passport_id", ""),
        agent_id=event.get("agent_id", ""),
        tool_call_ref=d.get("tool_call_ref", ""),
        action_class=d.get("action_class", ""), policy_hash=d.get("policy_hash", ""),
        decision_id=d.get("decision_id", ""), input_sha256=d.get("input_sha256", ""),
        output_sha256=d.get("output_sha256", ""),
        parent_receipt_id=d.get("parent_receipt_id"), key_id=d.get("key_id", ""),
        signature=d.get("signature", ""),
        **({"created_at": datetime.fromisoformat(d["created_at"])}
           if d.get("created_at") else {}))
