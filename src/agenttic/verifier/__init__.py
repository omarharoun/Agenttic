"""Verifier SDK (SPEC-2 M17) — offline verification of passports, receipts, and
delegation chains against a fetched JWKS. No Agenttic account required.

Verification failures raise distinct, named errors (Hard Rule 27); status is
checked separately from the signature (Hard Rule 28)."""

from agenttic.verifier.sdk import (  # noqa: F401
    ExpiredError,
    RevokedError,
    TamperedError,
    UnknownKeyError,
    VerifyError,
    check_status,
    verify_chain,
    verify_passport,
    verify_receipt,
)
