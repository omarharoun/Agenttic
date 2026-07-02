"""Symmetric encryption for secrets at rest (per-tenant Anthropic keys).

Uses Fernet (AES-128-CBC + HMAC) with a key derived from a server secret —
``ASCORE_SECRET_KEY`` if set, else the session secret. In production an unset
secret fails closed (no insecure default); outside production a dev fallback key
is used so local runs work (dev data is not protected — by design).
The derivation (SHA-256 → urlsafe base64) accepts any string as the secret, so
operators don't have to generate a Fernet key by hand. Set ``ASCORE_SECRET_KEY``
to a strong random value in production and keep it stable (rotating it makes
existing ciphertexts undecryptable).
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _derive_key(cfg: dict) -> bytes:
    secret = os.environ.get("ASCORE_SECRET_KEY")
    if not secret:
        from ascore.server.sessions import session_secret
        try:
            secret = session_secret(cfg)
        except Exception:  # noqa: BLE001
            secret = ""
    if not secret:
        # Fail closed in production: never encrypt tenant secrets under a
        # hard-coded default key. Outside production a deterministic dev key is
        # used so local runs work (dev data is not protected — by design).
        from ascore.certification import is_production
        if is_production(cfg):
            raise RuntimeError(
                "ASCORE_SECRET_KEY is not set — refusing to encrypt secrets "
                "with an insecure default in production (fail closed).")
        secret = "ascore-dev-insecure-secret"  # dev-only; never reached in prod
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _fernet(cfg: dict) -> Fernet:
    return Fernet(_derive_key(cfg))


def encrypt(cfg: dict, plaintext: str) -> str:
    return _fernet(cfg).encrypt(plaintext.encode()).decode()


def decrypt(cfg: dict, token: str) -> str | None:
    """Return the plaintext, or None if the ciphertext can't be decrypted
    (wrong/rotated secret or corruption) — never raises to callers."""
    try:
        return _fernet(cfg).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None
