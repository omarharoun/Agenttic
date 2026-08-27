# Receipt-Gated Tool Access — Receipt Schema (Layer 3, Step 1)

Status: **design only, no implementation.** This settles the receipt schema before
the decorator (§6 of the handover asked for the schema reviewed first).

The deliverable is a schema a company's internal tool can verify **offline** so it
refuses any call that doesn't carry a valid, current, action-matched, single-use
receipt issued by the Agenttic gateway.

---

## 0. Ground truth first — what §4 claims vs. what the code actually does

I verified every "reuse, don't rebuild" building block against the source before
designing. The handover is mostly accurate but glosses four things that change the
design. **Read this before the schema.**

| §4 building block | Verdict | Evidence |
|---|---|---|
| Ed25519 passport issuance + JWKS rotation-with-overlap | ✅ real, reuse as-is | `passport/keys.py` (`PassportKeyManager`, `rotate()` overlap window, `jwks()`, fail-closed in prod); route `/.well-known/agenttic-jwks.json` at [passport.py:24](src/agenttic/server/routes/passport.py) |
| Enforcement gateway Lane 1–3 + SSRF guard | ✅ real, reuse | `enforce/lanes.py` (`lane1_evaluate`, `action_class_of`), `enforce/async_judge.py` (lane 3); SSRF = `security.validate_blackbox_url` reused in `_egress_blocked` ([lanes.py:86](src/agenttic/enforce/lanes.py)) |
| "Hashes not payloads" convention | ⚠️ real but **cited for the wrong thing** | see **§0.1** |
| Offline verifier SDK (Python/JS) | ✅ real, but **thinner than implied** | `verifier/sdk.py` + `verifier/js/sdk.js`; but `verify_receipt` checks **signature only** — no expiry/revocation/nonce (see **§0.3**) |

### 0.1 "Hashes not payloads" means the opposite of what §3.2 needs

The existing convention (`passport/receipts.py`, Hard Rule 30) hashes the **actual
input/output values** — `input_sha256 = _sha256(input_data)` — so the platform can
record *what happened* without **storing** the payload. §3.2 wants to hash the
parameter **schema, not values**, so a receipt binds to an action *shape*. These are
different hash targets for different reasons; §3.2's "consistent with the existing
pattern" is misleading.

Concretely: **there is no "parameter schema" concept anywhere in the gateway.** Lane
1 matches on `tool_name` + concrete `data` (arg values) — [lanes.py:49](src/agenttic/enforce/lanes.py)
`_matches()`. So the "hash of tool name + parameter schema" in §3.2 is **net-new** and
I have to define both the input and where the schema comes from (§2.1, §5).

### 0.2 Receipts already exist — but the existing `Receipt` is the wrong artifact

`passport/receipts.py` `Receipt` is an **after-the-fact audit record**, not a
pre-execution capability token:

- It **is** an `EnforcementEvent` and **cannot exist without a logged allow-decision**
  (Hard Rule 29) — issuance *follows* the action decision and writes to the append-only
  log ([receipts.py:56](src/agenttic/passport/receipts.py)).
- Its issuer-side `verify_receipt` proves backing by **scanning the registry**
  (`_logged_allow`) — [receipts.py:100](src/agenttic/passport/receipts.py). **Not
  offline-portable**: a third-party tool has no Agenttic registry.
- It carries `input_sha256`/`output_sha256` (an *output* that doesn't exist pre-call)
  and **no `expires_at`, no `nonce`** — the two anti-replay primitives Layer 3 is built
  on ([schema/passport.py:87](src/agenttic/schema/passport.py)).

**Decision: do not overload `Receipt`.** Define a distinct, self-contained token
(the *Tool Access Receipt* below) that **reuses the crypto** (same `PassportKeyManager`,
same JWKS, same canonicalization, same delegation-chain idea) but not the schema or the
registry-backed verification. Overloading would force half-null fields and would break
Hard Rule 29's "no receipt without a logged allow" invariant, because a capability
token is issued *to permit*, which is a different ordering than *to record*.

### 0.3 Net-new work is small and specific

Expiry, single-use nonce, action-shape hash, instance binding, and a human-principal
field are **all net-new** (passports have expiry; receipts have none; nothing models a
human principal — `PassportClaims` has `agent_id`/`tier`/`policy_hash`/`stage`/
`autonomy`, no human — [schema/passport.py:41](src/agenttic/schema/passport.py)). The
offline verifier SDK must be **extended** (as §4 says) to add the expiry/revocation/
action/nonce steps its current `verify_receipt` lacks.

### 0.4 Canonicalization + signing trap (must pin, or signatures silently fail)

The repo has **two** `canonical_json` and **three** signing schemes. Layer 3 **must**
use exactly one combination or offline verification breaks:

- ✅ **Use:** passport Ed25519 (`passport/keys.py`) + `certification.hashing.canonical_json`
  = `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`. The offline
  verifier's private `_canonical_json` is **byte-identical** to this
  ([sdk.py:40](src/agenttic/verifier/sdk.py) vs [hashing.py:20](src/agenttic/certification/hashing.py)) — verified.
- ❌ **Do not use:** `safety_cert.canonical_json` (`ensure_ascii=True`), the cert-track
  Ed25519 keys at `/.well-known/agenttic-cert-keys.json`, or the `CertificationRow` HMAC.
  Any of these produces bytes the JWKS offline path can't verify.

### 0.5 Revocation is per-passport, not per-receipt

`/passport/{id}/status` + append-only `passport_events` give **passport** revocation
([passport.py:74](src/agenttic/server/routes/passport.py)). The SPEC-12
`RevocationList` (`schema/attestation.py`) is for **certification manifests**. **There
is no receipt-level CRL, and Layer 3 doesn't need one:** a receipt is single-use and
expires in seconds, so the only thing worth revoking mid-flight is the *passport / agent
identity*. §3.3 step 2's "revocation list" is read here as **passport revocation**.

---

## 1. The Tool Access Receipt (schema)

One JSON object. Ed25519-signed by the passport key; verified against the JWKS. Every
field earns its place (justified inline). Distinct `typ` so it can never be confused
with the audit `Receipt`.

```jsonc
{
  "typ": "agenttic/tool-access-receipt@1",   // versioned type tag; verifier rejects
                                              //   unknown typ → no cross-protocol confusion
  "receipt_id": "tar-9f3c1a...",              // "tar-" + 16 hex; distinct from audit "rcpt-"

  // --- WHAT is authorised (the action SHAPE) ---
  "action_hash": "b3f0…",                     // sha256 over the action identity (§2.1)
  "action_class": "write",                    // read | write | irreversible  (drives §2.3, §3)

  // --- INSTANCE binding, present iff action_class == "irreversible" (§4) ---
  "bound_params": "7d21…",                    // salted sha256 of the identifying values (§2.2)
  "bound_param_names": ["customer_id"],       // which params were bound (so the tool knows
                                              //   what to recompute); names only, no values

  // --- WHO is acting ---
  "passport_id": "pp-4b8…",                   // the calling agent's passport
  "passport_hash": "a1c9…",                   // sha256 of the passport's signed claims —
                                              //   lets the tool check tier/stage + revocation
                                              //   without shipping the whole passport
  "principal": {                              // the HUMAN at the end of delegation (net-new §0.3)
    "kind": "human",
    "id": "sub:okta|jane.doe",                // stable subject id, resolved at issuance
    "via": ["agent:triage-bot", "agent:sub-worker"]  // the agent hops in between
  },

  // --- WHO authorised it (where the guarantee terminates, §6) ---
  "gateway_id": "gw:prod-1",                  // which gateway issued this
  "decision_id": "decision:…",               // joins back to the audit Receipt/EnforcementEvent
  "policy_hash": "e0aa…",                     // the exact policy the Lane 1–3 decision applied

  // --- WHEN / one-time-ness ---
  "nonce": "u7Yq3…",                          // 128-bit CSPRNG, base64url (§2.3); also the salt
  "issued_at": "2026-08-10T14:03:11.482Z",
  "not_before": "2026-08-10T14:03:11.482Z",   // = issued_at unless the gateway pre-dates
  "expires_at": "2026-08-10T14:04:11.482Z",   // short-lived, absolute UTC (§2.4)

  // --- signature envelope (reuses passport keys) ---
  "key_id": "3a9f…",                          // kid → JWKS
  "signature": "base64(ed25519)"              // over canonical_json(everything except signature)
}
```

Required always: `typ, receipt_id, action_hash, action_class, passport_id,
passport_hash, principal, gateway_id, decision_id, policy_hash, nonce, issued_at,
expires_at, key_id, signature`. Required **iff** `action_class == "irreversible"`:
`bound_params, bound_param_names`. Optional: `not_before` (defaults to `issued_at`).

The signature covers **every field except `signature`**, including all timestamps and
the nonce — same convention as the existing `Receipt.signing_input()`
([schema/passport.py:105](src/agenttic/schema/passport.py)). An unsigned timestamp or
nonce would be forgeable by any relay.

---

## 2. Hashing scheme

All hashes are `sha256_hex(canonical_json(obj))` with the **pinned** `canonical_json`
from §0.4 (`ensure_ascii=False`, sorted keys, tight separators). Reuse
`certification.hashing.sha256_hex` verbatim.

### 2.1 `action_hash` — binds the action SHAPE (not values)

```
action_hash = sha256_hex({
  "tool":         "delete_customer",     // the tool's registered name
  "action_class": "irreversible",        // read | write | irreversible (authenticated here too)
  "params_schema": { …canonical JSON Schema of the tool's parameters… }
})
```

- `params_schema` is the tool's **declared parameter JSON Schema**, not its argument
  values — this is the "schema not values" binding §3.2 wants.
- **Where the schema comes from (the integration contract):** the gateway has no schema
  today (§0.1). The tool declares it (`@require_receipt(action="delete_customer",
  params_schema=DeleteCustomerParams)`), and **the same declaration is registered with
  the gateway** at tool onboarding. Issuance and verification both hash the *same*
  canonicalized schema string. If they disagree, the receipt fails action-match closed
  — a safe failure, but it means schema drift is an operational concern to flag, not a
  silent risk.
- Because `tool` name is in the hash, "read customer" and "delete customer" never
  collide even if their param schemas are identical — which is the replay §3.2 names.

### 2.2 `bound_params` — binds the INSTANCE, only when it matters (§4)

```
bound_params = sha256_hex({
  "salt":   <nonce>,                       // the receipt's own nonce, so the hash is not a
                                           //   brute-forceable oracle over low-cardinality ids
  "values": { "customer_id": "123" }       // only the params in bound_param_names
})
```

- Salted by the nonce so an attacker can't dictionary-attack `customer_id ∈ 1..N` from
  the hash. The nonce is single-use and unpredictable, so the salt is per-receipt.
- Still "hashes not payloads": the receipt carries a **hash**, never the plaintext id.
- Absent for `read`/`write`; the tool only recomputes and compares it when
  `action_class == "irreversible"`.

### 2.3 `nonce` — single-use

- **Format:** 16 bytes (128-bit) from a CSPRNG, base64url without padding (~22 chars).
  Not a UUID — a raw CSPRNG value is unguessable and carries no structure to leak.
- **Uniqueness / single-use is enforced by the store, not the format** (§7).
- Doubles as the `bound_params` salt (§2.2).

### 2.4 `expires_at` / `not_before` — expiry semantics

- All timestamps tz-aware UTC, ISO-8601, **authenticated by the signature**.
- Reject when `now >= expires_at` (`ExpiredError`). Reject when
  `now < not_before - skew`.
- **Clock skew is real** (gateway host ≠ tool host): allow a small `skew` (default **5s**)
  on both edges. This is a calibration knob, keep it configurable.
- **Default TTL: 60s** (`expires_at = issued_at + 60s`); **15–30s for irreversible**
  actions, since those already pay a live-revocation round-trip (§3). Long enough for
  the agent→tool hop, short enough that a leaked receipt is near-useless — and it's
  single-use regardless.
- Consequence for the store: a seen-nonce only needs retention until `expires_at + skew`;
  after that a replay fails on expiry anyway. So the nonce table is **naturally bounded**
  = issuance-rate × TTL, and prunable (§7).

---

## 3. Verification order (fail-closed, offline-first)

Extends §3.3 and the current `verifier/sdk.py`. Order is deliberate: cheap **offline**
checks first, the possibly-networked revocation check next, the **stateful** nonce claim
**last** — so a receipt that was going to fail never burns its nonce or triggers a
network round-trip.

```
0. typ is supported (agenttic/tool-access-receipt@1)     — else reject   [offline]
1. signature verifies vs JWKS (kid → key)                — else Tampered [offline]
2. now ∈ [not_before - skew, expires_at)                 — else Expired  [offline]
3. action_hash == sha256(this tool's own {name,class,schema}) — else reject [offline]
4. if action_class == "irreversible":
     bound_params == sha256(salt=nonce, values=actual call args)  — else reject [offline]
5. passport not revoked:                                 — else Revoked
     - normal action    → cached passport status/CRL, short TTL (default 60s)  [cache]
     - irreversible      → LIVE status check, skip cache                       [network]
6. claim nonce single-use: INSERT (unique) → ok; IntegrityError → replay reject [stateful]
```

- **Any failure at any step → reject. No partial trust** (§3.3).
- Steps 0–4 need only the JWKS (fetched once) + the tool's own knowledge of its shape →
  **fully offline**, no Agenttic account per call.
- Step 6 is the only state mutation and is **atomic** via the store's unique constraint
  (§7). It runs after all validation so a bad receipt can't consume a nonce.
- **Nonce-claim vs. execution ordering:** claim the nonce, *then* execute; if execution
  fails, the nonce stays spent (fail-closed — don't let a failed irreversible call be
  retried under the same receipt). Callers that need at-least-once must get a fresh
  receipt. Flag this tradeoff to the pilot.

---

## 4. Action-shape vs. instance (the question the handover said not to paper over)

**The problem, restated.** `action_hash` binds tool + action_class + parameter *schema*,
deliberately not values. So within a receipt's validity window, `delete_customer(123)`
and `delete_customer(456)` hash **identically** — the receipt authorises the *shape*
`delete_customer(id: str)`, not the instance. Two distinct threats:

- **Replay** (a leaked/intercepted receipt reused): the single-use nonce + short expiry
  fully bound this. One use, seconds of life. **Sufficient.**
- **Substitution** (the agent — or a compromised delegation hop — legitimately holds a
  receipt issued for `delete(123)` but calls `delete(456)`): the nonce guarantees this
  happens **at most once**, not that it happens on the **right** instance. The tool
  cannot tell 123 from 456 from the action-hash alone.

**Is single-use sufficient?**

- For **reversible** actions (reads, idempotent writes, anything with rollback): **yes.**
  A single wrong-instance call is auditable (every call joins to `decision_id`) and
  undoable. Value-binding every receipt would throw away the "hashes not payloads"
  benefit for no real gain.
- For **irreversible** actions (payments, deletes, non-rollbackable writes): **no.** One
  wrong-instance execution is a real, unrecoverable harm the nonce does not prevent.

**What the schema needs (and this schema has):** the conditional `bound_params` field
(§2.2). It binds the receipt to the specific *values* of the identifying params **only
for `irreversible` actions**, as a **salted hash** — so it closes the substitution gap
without reintroducing value-binding-by-default and without putting plaintext values in
the receipt. Schema-bound by default; instance-bound exactly where instance-correctness
is load-bearing. `action_class` (authenticated inside `action_hash`) is what selects the
path, so an attacker can't downgrade an irreversible action to skip the check without
breaking the signature.

This is the honest answer: **single-use is enough for reversible actions and for replay;
it is not enough for the instance-correctness of irreversible actions, and the schema
carries a dedicated field to close exactly that case.**

---

## 5. Two §6 decisions (with evidence)

### FastAPI first — **confirmed.**
The backend is Python/FastAPI end to end; **there is no Node/Express backend** (no
root `package.json`/`server.js`). Passport issuance, the key manager
(`app.state.passport_keys`), the JWKS route, the enforce gateway, and the verifier are
all Python. Every server route is a FastAPI `APIRouter`
([server/routes/*.py](src/agenttic/server/routes)). The decorator's issuance side and
the reference tool-side check are Python; the JS verifier (`verifier/js/sdk.js`) already
exists for JS tools and gets **extended**, not authored fresh. **Build the FastAPI
decorator first.** The code agrees with the handover's own guess.

### Nonce store: reuse the append-only SQLite/Postgres registry — **confirmed, no Redis.**
The registry already has the exact primitive for atomic single-use:
`_append_only()` does INSERT-and-catch-`IntegrityError`-at-commit, converting a
unique-constraint race into a clean domain error
([sqlite_store.py:1215](src/agenttic/registry/sqlite_store.py)). A nonce table with
`UniqueConstraint(tenant_id, nonce)` gives **claim-by-insert**: first insert wins →
first use; second raises → replay. This is the same race the `already_seeded()` docstring
dissects (8 concurrent workers, one winner — [sqlite_store.py:52](src/agenttic/registry/sqlite_store.py)).

- **`EmailTokenRow` is already this pattern** — single-use, expiring, `UniqueConstraint(token)`,
  `used_at`, "safe to prune past expiry" ([sqlite_store.py:395](src/agenttic/registry/sqlite_store.py)).
  The nonce store is that row shape again (`ToolReceiptNonceRow`: `tenant_id`,
  `nonce` unique, `action_hash`, `expires_at`, `claimed_at`).
- **Contention:** SQLite is hardened WAL + `busy_timeout=5000` — concurrent readers, one
  serialized writer that *waits* rather than erroring
  ([sqlite_store.py:758](src/agenttic/registry/sqlite_store.py)). The unique constraint
  makes the claim correct **regardless** of concurrency; SQLite's single-writer only
  caps *throughput*, and Postgres (already supported, same code path) lifts that ceiling.
- **The one implementation rule:** the seen-nonce check must be **claim-by-insert**, never
  check-then-insert. Check-then-insert has a TOCTOU replay window — the precise bug
  `_append_only` was written to kill. The decorator must INSERT-and-catch, not SELECT-then-INSERT.
- **Growth is bounded** (§2.4): retention = TTL + skew; prune beyond. No unbounded table.

**No concrete reason for Redis.** Reuse the registry.

---

## 6. The bound on the guarantee (stated, not overclaimed)

**Where fail-closed actually terminates.**

The **enforcement** question — *did a valid, current, action-matched decision authorise
this exact call?* — fail-closes **at the tool** (§3). No valid receipt ⇒ no action. Strip
the SDK, remove the gateway from the path, replay a leaked receipt: the tool still won't
act, because the check lives in the tool's environment, not in the agent's honesty. That
part of §3.3 holds.

But a receipt only exists because the gateway **chose** to issue it (§3.5: Lane 1–3 runs,
and *if allowed* a receipt is minted). So a receipt attests, precisely:

> "Gateway `gateway_id`, applying policy `policy_hash` to an agent holding passport
> `passport_id`, decided to **allow** an action of this shape (and this instance, for
> irreversible actions), on behalf of human `principal`, consumed once before
> `expires_at`."

It does **not** attest that the action is *safe*, *correct*, or *wise*. The **soundness**
question — *was that decision the right decision?* — terminates **not at the tool** but at
the **gateway's policy and the passport's behavioral scope**. The receipt moves the trust
boundary from *"the agent honestly used the SDK"* to *"the policy `policy_hash` is sound,
the behavioral scope behind the passport is honest, and the gateway's signing key is
uncompromised."*

**The honest claim is bounded to: "no *ungoverned* action," not "no *unsafe* action."**
Every execution is traceable to a signed gateway decision under a named policy and a
scoped passport (`decision_id` joins to the audit `Receipt`/`EnforcementEvent`). That is
governance and non-repudiation — not a safety proof.

This is consistent with the platform's existing ethos and should be marketed the same
way: the Certification track shows untested dimensions as **NOT ASSESSED**, never a silent
pass; `passport/scope.py`'s `BehavioralScope` is *"a fence, not a badge"* — it ships the
edge of the evidence, the coverage holes, the not-measured coverpoints — Hard Rule 65
([passport/scope.py:1](src/agenttic/passport/scope.py)). A Tool Access Receipt is the
runtime enforcement counterpart of that fence: **it proves the call was governed by a
named, scoped, signed decision — and it stops there.** Do not sell it as proof the call
was safe.

---

## 7. Store sketch (schema-adjacent, not built)

`ToolReceiptNonceRow(SQLModel, table=True)` — `tenant_id` (indexed), `nonce`
(`UniqueConstraint(tenant_id, nonce)`), `action_hash`, `expires_at`, `claimed_at`.
Claim = `_append_only`-style INSERT-catch-`IntegrityError`. Prune `WHERE expires_at <
now - skew`. Same table shape as `EmailTokenRow`. **Not built in Step 1** — listed so the
verification order (§3 step 6) and the growth bound (§2.4) are concrete.

---

## 8. What Step 2 builds against this

- `@require_receipt(action=..., action_class=..., params_schema=..., bound_params=[...])`
  FastAPI decorator implementing §3's order; register the same `params_schema` with the
  gateway so `action_hash` matches.
- Extend `verifier/sdk.py` + `verifier/js/sdk.js` with the type/expiry/action/
  bound-params/nonce steps their current `verify_receipt` lacks (§0.3).
- ~~Gateway issuance: on a Lane 1–3 **allow**, mint a Tool Access Receipt alongside the
  existing audit `Receipt`, resolving `principal` from the delegation chain (net-new).~~
  **Done** — `ReceiptIssuer.issue_tool_access`, returned from
  `POST /api/enforce/tool-call` as an `Agent-Tool-Receipt` response header. Two
  deviations from the line above, both deliberate:
  *(a)* `principal` resolves from the **authenticated operator**, not the delegation
  chain — `verify_chain` roots at `{passport_id, agent_id}`, an agent, so no walk can
  reach a human today; `via` is the agent hop, depth one.
  *(b)* The audit `Receipt` is **not** minted alongside — `ReceiptIssuer.issue_receipt`
  still has no production caller. Separate gap.
  The tool's `params_schema` comes from a per-tool catalog at
  `enforcement.tool_access.tools` in `config.yaml`, holding the tool's MCP
  `inputSchema` verbatim. That catalog is not optional: `Decision.action_class` is
  `read|write|unknown` and cannot express `irreversible`.
- `ToolReceiptNonceRow` + claim-by-insert (§7).

None of that is in this deliverable. Schema only, as asked.
