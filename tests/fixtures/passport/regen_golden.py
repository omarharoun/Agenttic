"""Regenerate golden.json — committed so the parity fixture is reproducible.

Run from repo root:  python tests/fixtures/passport/regen_golden.py
Rebuilds keys, passport, and a 2-hop receipt chain with the CURRENT signing
semantics (created_at is signed), then rewrites golden.json.
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ascore.certification.dossier import assemble
from ascore.config import load_config
from ascore.passport.issuer import PassportIssuer
from ascore.passport.keys import PassportKeyManager, generate_key
from ascore.passport.receipts import ReceiptIssuer
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import Attestation, CertificationProfile, TierDecision
from ascore.schema.enforcement import Decision, EnforcementEvent

OUT = Path(__file__).with_name("golden.json")


def main() -> None:
    cfg = load_config("config.yaml")
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    km = PassportKeyManager(cfg, private_key=generate_key())
    assemble(reg, agent_id="a", agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"]),
             coverage=[], attestation=Attestation(mode="self_attested", tenant="default"))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    passport = PassportIssuer(reg, cfg, km).issue("a", now=now)

    ri = ReceiptIssuer(reg, cfg, km)
    session = "sess-golden"

    def allow(decision_id: str, tool: str) -> Decision:
        d = Decision(decision_id=decision_id, session_id=session, agent_id="a",
                     action="allow", tool_name=tool, action_class="read",
                     phase="tool_call", lane="lane1",
                     policy_hash="pol-golden")
        reg.append_enforcement_event(EnforcementEvent(
            event_id=f"ev-{decision_id}", session_id=session, agent_id="a",
            kind="decision", action="allow", decision_ref=d.ref(),
            policy_hash=d.policy_hash))
        return d

    parent = ri.issue_receipt(passport, session, allow("d-parent", "search.query"),
                              input_data={"q": "root"}, output_data={"ok": True})
    child = ri.issue_receipt(passport, session, allow("d-child", "http.get"),
                             input_data={"u": "x"}, output_data={"ok": True},
                             parent_receipt_id=parent.receipt_id)

    fx = {"jwks": km.jwks(),
          "passport": json.loads(passport.model_dump_json()),
          "receipt": json.loads(child.model_dump_json()),
          "chain": [json.loads(child.model_dump_json()),
                    json.loads(parent.model_dump_json())],
          "now": now.isoformat(),
          "expected": {"passport_valid": True, "tier": "B"}}
    OUT.write_text(json.dumps(fx, indent=2) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
