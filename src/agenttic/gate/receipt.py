"""Tool Access Receipt — the capability token (RECEIPT-SCHEMA.md §1, §2).

A short-lived, single-use, Ed25519-signed token that says: *this gateway,
applying this policy, allowed an action of this shape (and, for irreversible
actions, this instance) on behalf of this human, once, before ``expires_at``.*

Distinct from :class:`agenttic.schema.passport.Receipt`, which is an
after-the-fact audit record and cannot exist without a logged allow-decision
(Hard Rule 29). This one is issued *to permit*, not *to record* — different
ordering, different schema, same crypto.

Canonicalization is pinned: ``certification.hashing.canonical_json``
(``ensure_ascii=False``) + the passport Ed25519 keys, which is byte-identical
to the offline verifier's private ``_canonical_json``. The other two
canonicalisers in this repo produce bytes the JWKS offline path cannot verify,
silently (RECEIPT-SCHEMA.md §0.4).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from agenttic.certification.hashing import sha256_hex
from agenttic.passport.keys import PassportKeyManager

TYP = "agenttic/tool-access-receipt@1"

ActionClass = Literal["read", "write", "irreversible"]

# §2.4: short enough that a leaked receipt is near-useless, long enough for the
# agent→tool hop. Irreversible actions already pay a live-revocation round-trip.
DEFAULT_TTL_SECONDS = 60
IRREVERSIBLE_TTL_SECONDS = 30


class Principal(BaseModel):
    """The human at the end of the delegation chain (net-new — passports model
    no human). ``via`` is the agent hops in between, root-first."""

    kind: Literal["human"] = "human"
    id: str
    via: list[str] = Field(default_factory=list)


class ToolAccessReceipt(BaseModel):
    typ: Literal["agenttic/tool-access-receipt@1"] = TYP
    receipt_id: str

    # what is authorised: the action SHAPE (§2.1)
    action_hash: str
    action_class: ActionClass

    # instance binding — required iff action_class == "irreversible" (§2.2, §4)
    bound_params: str | None = None
    bound_param_names: list[str] | None = None

    # who is acting
    passport_id: str
    passport_hash: str
    principal: Principal

    # who authorised it
    gateway_id: str
    decision_id: str
    policy_hash: str

    # when / one-time-ness
    nonce: str
    issued_at: datetime
    not_before: datetime | None = None
    expires_at: datetime

    # signature envelope
    key_id: str
    signature: str = ""

    @model_validator(mode="after")
    def _normalise(self) -> "ToolAccessReceipt":
        for attr in ("issued_at", "not_before", "expires_at"):
            v = getattr(self, attr)
            if v is not None and v.tzinfo is None:
                setattr(self, attr, v.replace(tzinfo=timezone.utc))
        if self.not_before is None:
            # concrete in the signed payload, so no verifier has to re-derive it
            self.not_before = self.issued_at
        if self.action_class == "irreversible":
            if not self.bound_params or not self.bound_param_names:
                raise ValueError(
                    "irreversible actions require bound_params and "
                    "bound_param_names (RECEIPT-SCHEMA.md §4): the nonce makes a "
                    "substituted instance happen at most once, not on the right "
                    "instance")
        elif bool(self.bound_params) != bool(self.bound_param_names):
            raise ValueError(
                "bound_params and bound_param_names must be set together")
        return self

    def signing_input(self) -> dict:
        # the signature covers every field except itself — including the
        # timestamps and the nonce, which an unsigned relay could otherwise
        # forge. Same convention as Receipt.signing_input().
        data = self.model_dump(mode="json")
        data.pop("signature", None)
        return data

    def ref(self) -> str:
        return f"tool-access-receipt:{self.receipt_id}"


def compute_action_hash(tool: str, action_class: ActionClass,
                        params_schema: Any) -> str:
    """Bind the action SHAPE (§2.1): tool name + class + declared parameter
    schema, never argument values. The tool name is in the hash so
    ``read_customer`` and ``delete_customer`` never collide on an identical
    param schema. Issuance and verification must hash the *same* canonicalized
    schema — drift fails action-match closed."""
    return sha256_hex({"tool": tool, "action_class": action_class,
                       "params_schema": params_schema})


def compute_bound_params(nonce: str, values: dict[str, Any]) -> str:
    """Bind the INSTANCE (§2.2), salted by the receipt's own nonce so the hash
    is not a brute-forceable oracle over low-cardinality ids. Still hashes, not
    payloads — the plaintext id never rides in the receipt."""
    return sha256_hex({"salt": nonce, "values": values})


def new_nonce() -> str:
    """16 CSPRNG bytes, base64url, unpadded (§2.3). Not a UUID: a raw CSPRNG
    value is unguessable and carries no structure to leak."""
    return secrets.token_urlsafe(16)


def issue_tool_access_receipt(
    keys: PassportKeyManager,
    *,
    tool: str,
    action_class: ActionClass,
    params_schema: Any,
    passport_id: str,
    passport_hash: str,
    principal: Principal,
    gateway_id: str,
    decision_id: str,
    policy_hash: str,
    bound_values: dict[str, Any] | None = None,
    ttl_seconds: float | None = None,
    now: datetime | None = None,
) -> ToolAccessReceipt:
    """Mint and Ed25519-sign a Tool Access Receipt.

    ``bound_values`` is required for irreversible actions and is hashed, not
    stored; ``bound_param_names`` is derived from its keys so the tool knows
    what to recompute. Minting is deliberately decoupled from the gateway: this
    does *not* write to the append-only log and is not the audit ReceiptIssuer.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if ttl_seconds is None:
        ttl_seconds = (IRREVERSIBLE_TTL_SECONDS
                       if action_class == "irreversible"
                       else DEFAULT_TTL_SECONDS)

    nonce = new_nonce()
    bound_params = bound_param_names = None
    if bound_values:
        bound_params = compute_bound_params(nonce, bound_values)
        bound_param_names = sorted(bound_values)

    receipt = ToolAccessReceipt(
        receipt_id=f"tar-{uuid.uuid4().hex[:16]}",
        action_hash=compute_action_hash(tool, action_class, params_schema),
        action_class=action_class,
        bound_params=bound_params, bound_param_names=bound_param_names,
        passport_id=passport_id, passport_hash=passport_hash,
        principal=principal, gateway_id=gateway_id, decision_id=decision_id,
        policy_hash=policy_hash, nonce=nonce,
        issued_at=now, not_before=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        key_id=keys.key_id())
    receipt.signature = keys.sign(receipt.signing_input())
    return receipt
