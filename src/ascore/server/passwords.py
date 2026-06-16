"""Password hashing — bcrypt, with a SHA-256 pre-hash so passwords longer than
bcrypt's 72-byte limit aren't silently truncated. Plaintext is never logged or
stored; only the bcrypt hash is persisted."""

from __future__ import annotations

import base64
import hashlib

import bcrypt


def _prep(password: str) -> bytes:
    # base64(sha256(pw)) is <= 72 bytes, sidestepping bcrypt's input cap while
    # preserving full password entropy.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prep(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(password), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
