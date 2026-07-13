"""Offline verifier SDK (SPEC-2 T33.1).

Self-contained: verifies passports / receipts / chains against a JWKS dict using
Ed25519 (the ``cryptography`` library). A relying party fetches the JWKS from
``/.well-known/agenttic-jwks.json`` once and verifies everything offline — no
Agenttic account, no network per call.

Failures raise distinct named errors:
``TamperedError`` (bad signature), ``ExpiredError``, ``RevokedError``,
``UnknownKeyError`` (kid not in JWKS).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone


class VerifyError(Exception):
    """Base class for verifier errors."""


class TamperedError(VerifyError):
    """The signature does not verify — the payload was tampered with."""


class ExpiredError(VerifyError):
    """The credential is past its expiry."""


class RevokedError(VerifyError):
    """The credential's status URL reports it revoked (beats a valid signature)."""


class UnknownKeyError(VerifyError):
    """The signing key id is not present in the JWKS."""


def _canonical_json(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _pubkey_from_jwks(jwks: dict, kid: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            x = k["x"]
            raw = base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
            return Ed25519PublicKey.from_public_bytes(raw)
    raise UnknownKeyError(f"no key with kid {kid!r} in JWKS")


def _verify_signature(public_key, payload: dict, signature_b64: str) -> None:
    from cryptography.exceptions import InvalidSignature
    try:
        public_key.verify(base64.b64decode(signature_b64), _canonical_json(payload))
    except (InvalidSignature, ValueError) as exc:
        raise TamperedError("signature does not verify") from exc


def _now(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Passport.
# --------------------------------------------------------------------------- #


def verify_passport(passport: dict, jwks: dict, *, now: datetime | None = None,
                    status: str | None = None) -> dict:
    """Verify a passport dict against a JWKS. Returns the claims on success;
    raises a distinct error otherwise. ``status`` (from ``check_status``) is
    checked separately from the signature."""
    claims = passport["claims"]
    kid = claims["key_id"]
    pub = _pubkey_from_jwks(jwks, kid)
    _verify_signature(pub, claims, passport.get("signature", ""))
    # signature good → now the independent checks
    expires = datetime.fromisoformat(claims["expires_at"])
    if _now(now) >= expires:
        raise ExpiredError(f"passport expired at {claims['expires_at']}")
    if status == "revoked":
        raise RevokedError("passport status is revoked")
    return claims


# --------------------------------------------------------------------------- #
# Receipt + chain.
# --------------------------------------------------------------------------- #


def verify_receipt(receipt: dict, jwks: dict) -> dict:
    """Verify a receipt dict's signature against a JWKS."""
    kid = receipt.get("key_id", "")
    pub = _pubkey_from_jwks(jwks, kid)
    payload = {k: v for k, v in receipt.items() if k != "signature"}
    _verify_signature(pub, payload, receipt.get("signature", ""))
    return receipt


def verify_chain(receipts: list[dict], jwks: dict) -> dict:
    """Verify a delegation chain (list of receipts, child→...→root). Each hop's
    signature is verified; the chain must resolve to a root (no parent). Returns
    a summary with the principal and every hop's policy hash."""
    by_id = {r["receipt_id"]: r for r in receipts}
    if not receipts:
        raise VerifyError("empty chain")
    # start from the first receipt and walk parents
    current = receipts[0]["receipt_id"]
    hops = []
    seen = set()
    principal = None
    while current is not None:
        if current in seen:
            raise VerifyError(f"cycle at receipt {current}")
        seen.add(current)
        r = by_id.get(current)
        if r is None:
            raise VerifyError(f"broken hop: receipt {current} not in chain")
        verify_receipt(r, jwks)  # raises TamperedError on bad sig
        hops.append({"receipt_id": r["receipt_id"],
                     "policy_hash": r.get("policy_hash", "")})
        parent = r.get("parent_receipt_id")
        if not parent:
            principal = {"passport_id": r.get("passport_id"),
                         "agent_id": r.get("agent_id")}
            break
        current = parent
    return {"resolved": principal is not None, "hops": hops,
            "principal": principal}


# --------------------------------------------------------------------------- #
# Status (checked separately from signature).
# --------------------------------------------------------------------------- #


def check_status(status_url: str, fetcher=None) -> str:
    """Fetch the passport's status URL and return its status ('active' |
    'revoked'). ``fetcher(url) -> dict`` is injectable (tests / custom HTTP)."""
    if fetcher is not None:
        body = fetcher(status_url)
    else:  # pragma: no cover - real network path
        import urllib.request
        with urllib.request.urlopen(status_url, timeout=10) as resp:
            body = json.loads(resp.read())
    return body.get("status", "active")
