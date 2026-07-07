"""Passport signing keys (SPEC-2 T31.2).

Ed25519 via the maintained ``cryptography`` library (never hand-rolled). The
issuer holds the PRIVATE key in memory only — it is loaded from the existing
secret handling (``ASCORE_PASSPORT_SIGNING_KEY`` / ``*_FILE``, base64 of the raw
32-byte seed) and is NEVER written to the registry, logs, events, or exports. The
matching PUBLIC keys are published as a JWKS at
``/.well-known/agenttic-jwks.json``. Rotation keeps the previous key published for
an overlap window so passports signed just before a rotation still verify.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from ascore.certification.hashing import canonical_json
from ascore.schema.passport import KeyRef

_ENV_KEY = "ASCORE_PASSPORT_SIGNING_KEY"


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def raw_public(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(pub: Ed25519PublicKey) -> str:
    """A stable kid = first 16 hex of sha256(raw public key)."""
    return hashlib.sha256(raw_public(pub)).hexdigest()[:16]


def public_key_b64(pub: Ed25519PublicKey) -> str:
    return _b64e(raw_public(pub))


def generate_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_seed_b64(priv: Ed25519PrivateKey) -> str:
    raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return _b64e(raw)


def load_private_from_seed(seed_b64: str) -> Ed25519PrivateKey:
    raw = _b64d(seed_b64)
    if len(raw) != 32:
        raise ValueError("Ed25519 signing seed must be 32 bytes (base64)")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _load_from_env() -> Ed25519PrivateKey | None:
    material = os.environ.get(_ENV_KEY)
    if not material:
        return None
    return load_private_from_seed(material.strip())


def sign_payload(priv: Ed25519PrivateKey, payload: dict) -> str:
    """Ed25519 signature (base64) over the canonical JSON of ``payload``."""
    msg = canonical_json(payload).encode("utf-8")
    return _b64e(priv.sign(msg))


def verify_payload(public_key_b64_str: str, payload: dict, signature_b64: str
                   ) -> bool:
    """Verify an Ed25519 signature against a base64 raw public key. Returns
    False on any failure (never raises)."""
    from cryptography.exceptions import InvalidSignature
    try:
        pub = Ed25519PublicKey.from_public_bytes(_b64d(public_key_b64_str))
        pub.verify(_b64d(signature_b64), canonical_json(payload).encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, Exception):  # noqa: BLE001
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PassportKeyManager:
    """Holds the active signing key + publishes the JWKS (with overlap keys).

    Inject ``private_key`` in tests (a real generated Ed25519 key). In production
    the key comes from the environment; a dev-only ephemeral key is generated if
    none is configured."""

    def __init__(self, cfg: dict | None = None,
                 private_key: Ed25519PrivateKey | None = None):
        self.cfg = cfg or {}
        self._priv = private_key or _load_from_env() or generate_key()
        pub = self._priv.public_key()
        self._kid = key_id(pub)
        # published keys: kid -> KeyRef (public only)
        self._published: dict[str, KeyRef] = {
            self._kid: KeyRef(key_id=self._kid, public_key_b64=public_key_b64(pub),
                              not_before=_now())
        }

    def key_id(self) -> str:
        return self._kid

    def public_keyref(self) -> KeyRef:
        return self._published[self._kid]

    def sign(self, payload: dict) -> str:
        return sign_payload(self._priv, payload)

    def keyref_for(self, kid: str) -> KeyRef | None:
        return self._published.get(kid)

    def _overlap_days(self) -> float:
        return float((self.cfg.get("passport", {}) or {}).get(
            "key_rotation_overlap_days", 14))

    def rotate(self, new_private_key: Ed25519PrivateKey | None = None,
               *, now: datetime | None = None) -> str:
        """Rotate to a new signing key. The OLD key stays published until
        ``now + key_rotation_overlap_days`` so recently-signed passports verify."""
        now = now or _now()
        # close the overlap window on the outgoing key
        old = self._published.get(self._kid)
        if old is not None:
            old.not_after = now + timedelta(days=self._overlap_days())
        self._priv = new_private_key or generate_key()
        pub = self._priv.public_key()
        self._kid = key_id(pub)
        self._published[self._kid] = KeyRef(
            key_id=self._kid, public_key_b64=public_key_b64(pub), not_before=now)
        return self._kid

    def published_keyrefs(self, *, now: datetime | None = None) -> list[KeyRef]:
        """Currently publishable keys (active + still within overlap)."""
        now = now or _now()
        out = []
        for kr in self._published.values():
            if kr.not_after is not None and now > kr.not_after:
                continue  # overlap window expired
            out.append(kr)
        return out

    def jwks(self, *, now: datetime | None = None) -> dict:
        return {"keys": [kr.jwk() for kr in self.published_keyrefs(now=now)]}
