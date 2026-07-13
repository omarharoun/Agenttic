"""Deterministic canonical hashing for certification dossiers (SPEC-2 T11.3).

A dossier's hash must be reproducible byte-for-byte by an offline third-party
verifier. We canonicalize to JSON with **sorted keys**, **tight separators**, and
**UTF-8** (no ASCII escaping — the raw UTF-8 bytes are hashed), then SHA-256 the
encoded bytes.

The dossier's own ``content_sha256`` field is excluded from the content it names
(otherwise the hash would depend on itself). Chaining is via
``prev_dossier_sha256``, computed the same way over the previous dossier.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, tight separators, UTF-8 (not ASCII).

    Distinct from ``safety_cert.canonical_json`` (which uses ``ensure_ascii=True``
    for the legacy signed-certificate payload); dossiers hash the raw UTF-8 form
    so non-ASCII evidence text round-trips exactly."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_hex(payload: Any) -> str:
    """SHA-256 hex digest of the canonical UTF-8 encoding of ``payload``."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_dossier_hash(dossier: "Dossier | dict") -> str:  # noqa: F821
    """Compute a dossier's ``content_sha256`` over every field except itself.

    Accepts a :class:`~agenttic.schema.certification.Dossier` (uses its
    ``hashable_content()``) or a plain dict (``content_sha256`` is dropped)."""
    if hasattr(dossier, "hashable_content"):
        content = dossier.hashable_content()
    else:
        content = dict(dossier)
        content.pop("content_sha256", None)
    return sha256_hex(content)
