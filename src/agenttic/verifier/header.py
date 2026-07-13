"""Agent self-identification header (SPEC-2 T33.3).

An agent identifies itself to a relying party with the ``Agent-Passport`` HTTP
header carrying its passport (base64 of the passport JSON). The relying party
verifies it offline against the JWKS and checks the status URL separately.
"""

from __future__ import annotations

import base64
import json

HEADER_NAME = "Agent-Passport"


def encode_passport_header(passport: dict) -> str:
    return base64.b64encode(json.dumps(passport).encode("utf-8")).decode("ascii")


def decode_passport_header(header_value: str) -> dict:
    return json.loads(base64.b64decode(header_value))


def verify_agent_passport(header_value: str, jwks: dict, *, status_fetcher=None,
                          check_status: bool = True) -> dict:
    """Verify an ``Agent-Passport`` header. Returns the claims on success; raises
    a distinct verifier error otherwise. When ``check_status`` and a fetcher are
    given, the status URL is consulted (revocation beats a valid signature)."""
    from agenttic.verifier.sdk import check_status as _check_status
    from agenttic.verifier.sdk import verify_passport

    passport = decode_passport_header(header_value)
    status = None
    if check_status and status_fetcher is not None:
        status = _check_status(passport["claims"]["status_url"], status_fetcher)
    return verify_passport(passport, jwks, status=status)
