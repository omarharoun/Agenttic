"""Signed action receipts (SPEC-2 T32.1).

A receipt binds a passport to ONE allowed action: tool_call_ref, action class,
policy hash, decision id, and input/output **hashes** (no payloads by default,
Hard Rule 30). Receipts ARE :class:`EnforcementEvent`s — **none can exist without
a logged allow-decision** (Hard Rule 29).
"""

from __future__ import annotations

import hashlib
import json
import uuid

from ascore.schema.enforcement import EnforcementEvent
from ascore.schema.passport import Receipt


def _sha256(data) -> str:
    if data is None:
        return ""
    payload = data if isinstance(data, str) else json.dumps(data, sort_keys=True,
                                                            default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReceiptError(RuntimeError):
    """A receipt could not be issued because there is no logged allow-decision."""


class ReceiptIssuer:
    def __init__(self, reg, cfg: dict, key_manager):
        self.reg = reg
        self.cfg = cfg or {}
        self.keys = key_manager

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

        receipt = Receipt(
            receipt_id=f"rcpt-{uuid.uuid4().hex[:12]}",
            passport_id=passport.passport_id, agent_id=decision.agent_id,
            tool_call_ref=f"toolcall:{decision.tool_name}",
            action_class=decision.action_class, policy_hash=decision.policy_hash,
            decision_id=decision.decision_id,
            input_sha256=_sha256(input_data), output_sha256=_sha256(output_data),
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
                  "key_id": receipt.key_id, "signature": receipt.signature}
        if include_content:
            from ascore.enforce.self_security import redact_obj
            detail["content"] = {"input": redact_obj(input_data),
                                 "output": redact_obj(output_data)}
        self.reg.append_enforcement_event(EnforcementEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}", session_id=session_id,
            agent_id=decision.agent_id, kind="receipt", action="allow",
            actor="passport", decision_ref=decision.ref(),
            policy_hash=decision.policy_hash, detail=detail))
        return receipt

    def verify_receipt(self, receipt: Receipt, session_id: str | None = None
                       ) -> dict:
        """Verify a receipt's signature and that a backing allow-decision exists."""
        from ascore.passport.keys import verify_payload
        kr = self.keys.keyref_for(receipt.key_id)
        sig_valid = kr is not None and verify_payload(
            kr.public_key_b64, receipt.signing_input(), receipt.signature)
        backed = self._logged_allow(
            session_id, f"decision:{receipt.decision_id}") if session_id else True
        return {"receipt_id": receipt.receipt_id, "signature_valid": sig_valid,
                "backed_by_allow": backed, "valid": sig_valid}


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
        signature=d.get("signature", ""))
