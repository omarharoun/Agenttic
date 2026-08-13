# Receipt-Gated Tool Middleware — shared team contract

Every agent on this team reads this file first. It is the single source of
truth for the build. Where it disagrees with your own reading of the spec,
this file wins — it was reconciled against the actual source.

## What is being built

Verification spec §5, **in-process decorator variant only**:
`@require_receipt(...)` on a FastAPI endpoint, which refuses any call that
doesn't carry a valid, current, action-matched, single-use Tool Access
Receipt.

## Hard scope fence — do not cross

**In scope:** the `agenttic.gate` package, one demo protected endpoint, tests.

**Out of scope, do not touch:** the sidecar/reverse-proxy variant, the
knowledge graph, the Playground, eval-platform integrations, the scoring
engine, the Step 14 promotion gate, the `ui/` tree.

**Do not modify the existing `Receipt`** (`src/agenttic/schema/passport.py:87`).
It is an after-the-fact audit record, not a capability token. The Tool Access
Receipt is a *distinct, additive* artifact. If you believe you need to change
the existing receipt format, **stop and report it — do not guess.**

## Ground truth (verified against source, do not re-derive)

| Thing | Where | Note |
|---|---|---|
| Ed25519 sign/verify | `passport/keys.py` — `sign_payload`, `verify_payload(pub_b64, payload, sig_b64)` | returns `False` on any failure, never raises |
| Key manager | `passport/keys.py:105` — `PassportKeyManager.sign/jwks/key_id/keyref_for` | rotation-with-overlap already handled |
| JWKS route | `server/routes/passport.py:24` `/.well-known/agenttic-jwks.json` | public, unauthenticated |
| Offline verifier | `verifier/sdk.py` — `verify_passport`, `verify_receipt`, `check_status(status_url, fetcher=None)` | errors: `VerifyError`/`TamperedError`/`ExpiredError`/`RevokedError`/`UnknownKeyError` |
| Header codec | `verifier/header.py` — `HEADER_NAME = "Agent-Passport"`, base64-of-JSON | reuse the same encoding for the receipt header |
| Canonical JSON | `certification/hashing.py:20` — `canonical_json`, `sha256_hex` | `sort_keys=True, separators=(",",":"), ensure_ascii=False` |
| Passport revocation | `registry/sqlite_store.py:3301` `passport_status()`; route `GET /passport/{id}/status` | revocation is **per-passport**, there is no receipt CRL |
| Gateway decision | `enforce/gateway.py:113` `evaluate_tool_call` → `Decision(decision_id, policy_hash, action_class, ...)` | `action_class` is `read\|write\|unknown` today |
| Audit receipt issuer | `passport/receipts.py:32` `ReceiptIssuer.issue_receipt` | requires a logged allow (Hard Rule 29) — do not break this |

### Canonicalization trap — this will silently break signatures

Use **exactly** `certification.hashing.canonical_json` + the passport Ed25519
keys. Do **not** use `safety_cert.canonical_json` (`ensure_ascii=True`), the
cert-track keys at `/.well-known/agenttic-cert-keys.json`, or the
`CertificationRow` HMAC. Any of those produces bytes the JWKS offline path
cannot verify.

## The token

Schema is already settled in `RECEIPT-SCHEMA.md` §1. Read it. Summary:
`typ: "agenttic/tool-access-receipt@1"`, `action_hash`, `action_class`
(`read|write|irreversible`), conditional `bound_params` + `bound_param_names`,
`passport_id`, `passport_hash`, `principal`, `gateway_id`, `decision_id`,
`policy_hash`, `nonce`, `issued_at`, `not_before`, `expires_at`, `key_id`,
`signature`. Signature covers every field except `signature`.

## Verification order — fail-closed, this exact order

```
0. typ supported                                          [offline]
1. signature verifies vs JWKS (kid → key)   → TamperedError/UnknownKeyError
2. now ∈ [not_before - skew, expires_at)    → ExpiredError   [offline]
3. action_hash == this tool's own hash      → ActionMismatchError [offline]
4. if action_class == "irreversible":
     bound_params == sha256(salt=nonce, values=actual args) → ActionMismatchError
5. passport not revoked                     → RevokedError
     normal        → cached status, 60s TTL
     irreversible  → LIVE check, cache bypassed AND not populated
6. claim nonce single-use (claim-by-insert) → ReplayError  [stateful]
```

Rules that are not negotiable:
- Any failure at any step → reject. No partial trust.
- Cheap offline checks first; the network check next; the **stateful** nonce
  claim **last**, so a receipt that was going to fail never burns its nonce.
- The nonce claim is **claim-by-insert, never check-then-insert.**
  Check-then-insert has a TOCTOU replay window.
- An irreversible live check must not read *or write* the cache. Writing it
  would let one irreversible call warm a stale entry for later normal calls.
- Clock skew is real (gateway host ≠ tool host): default 5s both edges,
  configurable.
- Nonce is claimed *before* execution. If execution then fails the nonce stays
  spent — fail-closed. Callers needing at-least-once get a fresh receipt.

## Decisions already made — do not relitigate

- **FastAPI first.** There is no Node backend in this repo.
- **Nonce store: in-memory default, pluggable.** The decorator runs *inside a
  third party's tool*, which has no Agenttic registry — so the default cannot
  depend on `registry/sqlite_store.py`. Ship a `NonceStore` protocol with a
  lock-guarded in-memory implementation and a `# ponytail:` comment naming the
  single-process ceiling. Do **not** add a SQLModel table in this task.
- **Revocation TTL: 60s** for normal actions.
- **Receipt TTL: 60s** default, 15–30s for irreversible.
- **Injectable clock and injectable status fetcher** are mandatory, not
  optional — the required revocation test cannot be written without them.

## Definition of done

1. `@require_receipt(action=..., action_class=..., params_schema=...)` wraps a
   FastAPI endpoint and rejects any call without a valid receipt (403).
2. Verification order above, fail-closed, implemented exactly.
3. Reuses the existing keys/JWKS/canonicalization. No new signing scheme.
4. One demo endpoint (`delete_customer`, irreversible) proving the loop.
5. A test that **demonstrates** — not asserts — that revoking a passport
   blocks a call inside the 60s TTL window, and that it does so via the
   live-check path while the cached path is still stale. Both halves of the
   §3.3 step-2 split must be visible in the test.
6. `pytest -q` green. No existing test edited or deleted.

## Style

This repo is terse and comment-light where code is obvious, and carries a
short "why" comment where a decision is load-bearing. Match it. No new
dependencies — `cryptography`, `pydantic`, `fastapi` are already present.
