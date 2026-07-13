"""Example relying-party server (SPEC-2 T33.3).

A minimal service that accepts requests only from agents presenting a valid
``Agent-Passport`` header. It verifies the passport OFFLINE against a JWKS it
fetched once, and rejects expired / revoked / tampered passports with distinct
errors — using only the public verifier SDK (no Agenttic account).

Run: ``uvicorn examples.relying_party:app`` (after pointing JWKS_URL / STATUS at
your Agenttic deployment). This file is illustrative; the logic is exercised by
``tests/test_verify_sdk.py``.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request

from agenttic.verifier.header import HEADER_NAME, verify_agent_passport
from agenttic.verifier.sdk import (
    ExpiredError,
    RevokedError,
    TamperedError,
    UnknownKeyError,
)

app = FastAPI(title="Example Agenttic relying party")

# In a real deployment: fetch once from
# https://<agenttic>/.well-known/agenttic-jwks.json and cache.
JWKS: dict = {"keys": []}
STATUS_FETCHER = None  # optional callable(status_url) -> {"status": ...}


def configure(jwks: dict, status_fetcher=None) -> None:
    global JWKS, STATUS_FETCHER
    JWKS = jwks
    STATUS_FETCHER = status_fetcher


@app.get("/protected")
def protected(request: Request):
    header_value = request.headers.get(HEADER_NAME)
    if not header_value:
        raise HTTPException(401, f"missing {HEADER_NAME} header")
    try:
        claims = verify_agent_passport(header_value, JWKS,
                                       status_fetcher=STATUS_FETCHER)
    except RevokedError:
        raise HTTPException(403, "agent passport revoked")
    except ExpiredError:
        raise HTTPException(403, "agent passport expired")
    except TamperedError:
        raise HTTPException(403, "agent passport signature invalid")
    except UnknownKeyError:
        raise HTTPException(403, "agent passport signed by an unknown key")
    return {"ok": True, "agent_id": claims["agent_id"], "tier": claims["tier"]}
