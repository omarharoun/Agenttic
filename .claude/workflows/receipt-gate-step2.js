export const meta = {
  name: 'receipt-gate-step2',
  description: 'Build the receipt-gated tool middleware (spec §5, in-process decorator only), then adversarially try to break it',
  whenToUse: 'Implementing or re-verifying Agenttic verification-spec §5 — the @require_receipt FastAPI decorator, its fail-closed verification pipeline, the demo protected action, and the revocation-blocks-within-TTL proof.',
  phases: [
    { title: 'Token', detail: 'ToolAccessReceipt model, action_hash/bound_params hashing, minimal issuance' },
    { title: 'Gate', detail: 'fail-closed verification pipeline, revocation cache, nonce store, @require_receipt' },
    { title: 'Demo + Proof', detail: 'demo delete_customer endpoint; the revocation-within-TTL test' },
    { title: 'Attack', detail: 'four adversaries sweep disjoint slices of the bypass surface' },
    { title: 'Confirm', detail: 'independently refute each claimed bypass before acting on it' },
    { title: 'Repair', detail: 'fix confirmed bypasses and failing tests until green' },
    { title: 'Audit', detail: 'completeness critic against the definition of done' },
  ],
}

const CONTEXT = `Read .claude/agents/receipt-gate-context.md FIRST — it is the reconciled build contract and overrides your own reading of the spec docs. Then read RECEIPT-SCHEMA.md for the exact token layout.`

// Role instructions live as files rather than registered agent types, so they
// resolve at run time regardless of when the session's agent registry loaded.
const BUILDER = `${CONTEXT}\nYou are the receipt-gate-implementer: read .claude/agents/receipt-gate-implementer.md and follow it as your operating instructions.`
const BREAKER = `${CONTEXT}\nYou are the receipt-gate-adversary: read .claude/agents/receipt-gate-adversary.md and follow it as your operating instructions.`

const REPORT = {
  type: 'object',
  required: ['files', 'symbols', 'assumptions'],
  properties: {
    files: { type: 'array', items: { type: 'string' }, description: 'path — one line on what it contains' },
    symbols: { type: 'array', items: { type: 'string' }, description: 'public symbol with full signature' },
    assumptions: { type: 'array', items: { type: 'string' }, description: 'assumption made, and what breaks if wrong' },
    contradictions: { type: 'array', items: { type: 'string' }, description: 'anything found that contradicts the context file' },
  },
}

const ATTACKS = {
  type: 'object',
  required: ['attacks'],
  properties: {
    attacks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'outcome', 'test'],
        properties: {
          name: { type: 'string' },
          outcome: { enum: ['blocked', 'BYPASSED'] },
          test: { type: 'string', description: 'file::test_function that performs the attack' },
          stopped_at: { type: 'string', description: 'for blocked: the file:line that rejected it' },
          gap: { type: 'string', description: 'for BYPASSED only: the file:line that should have stopped it and did not' },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['real', 'reasoning'],
  properties: {
    real: { type: 'boolean', description: 'true only if the bypass genuinely executes the protected action' },
    reasoning: { type: 'string' },
    fix: { type: 'string', description: 'if real: the smallest change that closes it' },
  },
}

// ---------------------------------------------------------------- Token
phase('Token')
const token = await agent(`${BUILDER}

Build the Tool Access Receipt token layer. You own exactly two files — create no others:
- src/agenttic/gate/__init__.py — re-export the public surface
- src/agenttic/gate/receipt.py

It must contain:
1. A pydantic model for the token, typ "agenttic/tool-access-receipt@1", fields exactly per RECEIPT-SCHEMA.md §1. bound_params / bound_param_names are required iff action_class == "irreversible", optional otherwise. A signing_input() that returns every field except "signature" — mirror the existing Receipt.signing_input() convention in src/agenttic/schema/passport.py.
2. compute_action_hash(tool, action_class, params_schema) — sha256_hex over {"tool","action_class","params_schema"} per RECEIPT-SCHEMA.md §2.1. Reuse certification.hashing.sha256_hex; do NOT write your own canonicaliser.
3. compute_bound_params(nonce, values) — salted sha256 per §2.2, salt is the receipt's own nonce.
4. new_nonce() — 16 CSPRNG bytes, base64url, unpadded. secrets module.
5. issue_tool_access_receipt(...) — mints and Ed25519-signs a token with a PassportKeyManager. Keep the signature explicit and small: this is the minimum needed to drive the demo and the tests, NOT the full gateway integration. Do not modify passport/receipts.py or the ReceiptIssuer.

Absolute constraints: do not modify the existing Receipt model. Use certification.hashing.canonical_json + the passport Ed25519 keys and nothing else — the canonicalization trap in the context file is real and silent.

Write no tests; a later stage owns those. Do not create src/agenttic/gate/middleware.py — another agent owns it.`,
  { label: 'gate/receipt.py', schema: REPORT })

log(`token layer: ${(token?.files || []).length} files`)
for (const c of token?.contradictions || []) log(`⚠ contradiction: ${c}`)

// ---------------------------------------------------------------- Gate
phase('Gate')
const gate = await agent(`${BUILDER}

The token layer is done. It exposes:
${(token?.symbols || []).map(s => '  ' + s).join('\n')}

Build the verification pipeline and the decorator. You own exactly one file: src/agenttic/gate/middleware.py. Do not edit src/agenttic/gate/receipt.py — read it, import from it.

It must contain:

1. Distinct exceptions, subclassing the existing agenttic.verifier.sdk.VerifyError hierarchy where one already fits (TamperedError, ExpiredError, RevokedError, UnknownKeyError all exist — reuse them, do not redefine). Add only what is genuinely new: an action-mismatch error and a replay error.

2. NonceStore — a Protocol with a single claim(nonce, expires_at) -> bool, plus InMemoryNonceStore implementing it. CLAIM-BY-INSERT, never check-then-insert: the check and the insert must be one atomic operation under a lock, because check-then-insert has a TOCTOU replay window that a concurrent test will find. Prune entries past expiry. One '# ponytail:' comment naming the single-process ceiling and that a registry-backed store lifts it. Do NOT add a SQLModel table.

3. RevocationCache — passport_id -> (status, fetched_at), TTL configurable, default 60s. Two entry points that must behave differently: a cached lookup for normal actions, and a live lookup for irreversible ones that BYPASSES the cache on read AND does not populate it on write. Populating it would let one irreversible check warm a stale entry for a later normal call — that is a real bypass, not a style point. Reuse agenttic.verifier.sdk.check_status for the fetch; take an injectable fetcher.

4. verify_tool_receipt(...) — the seven ordered steps from the context file, exactly in that order, fail-closed. Steps 0-4 offline, step 5 the revocation check, step 6 the nonce claim LAST so a receipt that was going to fail never burns its nonce or triggers a network call. Take an injectable now() clock and an injectable status fetcher — mandatory, the required revocation test cannot be written without them. Any unexpected exception inside this function is a rejection, never a pass-through.

5. require_receipt(action, action_class, params_schema, bound_params=None, ...) — the FastAPI decorator. Reads the receipt from a request header (reuse the base64-of-JSON encoding from verifier/header.py; name the header consistently with the existing "Agent-Passport" convention). Rejects with 403 and a reason that does not leak internals. On success the wrapped endpoint runs. For irreversible actions it recomputes bound_params from the ACTUAL call arguments and compares. Must work on a normal async FastAPI endpoint without breaking its signature or its response model.

Then run: python -c "import agenttic.gate.middleware" to confirm it imports clean.`,
  { label: 'gate/middleware.py', schema: REPORT })

for (const c of gate?.contradictions || []) log(`⚠ contradiction: ${c}`)

const BUILT = `Already built:
src/agenttic/gate/receipt.py
${(token?.symbols || []).map(s => '  ' + s).join('\n')}
src/agenttic/gate/middleware.py
${(gate?.symbols || []).map(s => '  ' + s).join('\n')}`

// ------------------------------------------------------- Demo + Proof
phase('Demo + Proof')
const [demo, proof] = await parallel([
  () => agent(`${BUILDER}

${BUILT}

Build the demo protected tool. You own exactly one file: examples/receipt_gated_tool.py. Do NOT wire this into src/agenttic/server/app.py — a fake delete_customer route must not exist in the production app; this is a standalone FastAPI app that tests import.

It must contain:
- a standalone FastAPI app with a DELETE/POST delete_customer endpoint decorated with @require_receipt(action="delete_customer", action_class="irreversible", ...), and one read endpoint at action_class="read" so both sides of the revocation cache/live split are demonstrable
- a build_demo_app(...) factory taking the injectable clock, status fetcher, nonce store and key manager, so tests can drive it deterministically
- a short module docstring showing the end-to-end loop: mint receipt -> present it -> executes; no receipt -> 403

Keep it small and readable — this is the thing a prospective adopter reads first. Write no tests.`,
    { label: 'examples/demo tool', phase: 'Demo + Proof', schema: REPORT }),

  () => agent(`${BUILDER}

${BUILT}

Write the one test that must not merely assert but DEMONSTRATE: revoking a passport blocks a call inside the 60s revocation-cache TTL window. You own exactly one file: tests/test_gate_revocation.py. Do not edit any existing test.

The demo app lives at examples/receipt_gated_tool.py (being written in parallel — read it once it exists; if it does not yet, poll briefly, then work against build_demo_app(clock, status_fetcher, nonce_store, key_manager)).

The test must make BOTH halves of spec §3.3 step 2 visible, using a fake clock and a status fetcher that counts its calls:
1. passport active -> normal (read) action succeeds; status fetched once and cached
2. passport is revoked
3. still inside the 60s TTL: assert the normal action STILL passes off the stale cache, and assert the fetcher was not called again — this is the honest cost of caching, and the test should state it in a comment rather than hide it
4. inside the same TTL window: the irreversible delete_customer action is BLOCKED — assert 403, assert the fetcher WAS called (the live path), and assert the cache was not populated by that live check
5. advance the clock past the TTL: the normal action now blocks too, and the fetcher is called again
6. assert the customer was never actually deleted in the blocked cases — the demo endpoint should expose enough state to prove the side effect did not happen. A 403 with the side effect still applied is the failure mode this whole task exists to prevent.

Follow the repo's pytest conventions from tests/test_passport.py and tests/test_receipts.py: module-level config, a small _setup() helper, PassportKeyManager(cfg, private_key=generate_key()), tempfile-backed registry if you need one. No new fixtures in conftest.py.

Run it. Report the actual pytest output, pass or fail — if it fails, say so with the output rather than reporting success.`,
    { label: 'tests/revocation proof', phase: 'Demo + Proof', schema: REPORT }),
])

for (const c of [...(demo?.contradictions || []), ...(proof?.contradictions || [])]) log(`⚠ contradiction: ${c}`)

// ---------------------------------------------------------------- Attack
phase('Attack')
const SLICES = [
  { n: 1, focus: 'no receipt at all (missing/empty/malformed header, base64 that is not JSON, JSON that is not an object); signature attacks (flipped byte, field mutated after signing, foreign Ed25519 key, unknown kid, rotation-overlap key confusion); cross-protocol confusion (present an existing audit Receipt or a Passport as a tool access receipt)' },
  { n: 2, focus: 'expiry and clock (expired, not_before in the future, exact skew boundaries on both edges, an extended expires_at which must fail at signature before it ever reaches the expiry check); action mismatch (receipt for read_customer replayed at delete_customer, same tool different params_schema, action_class downgraded from irreversible to write to skip both the live revocation check and the bound-params check)' },
  { n: 3, focus: 'instance substitution (receipt issued for delete_customer(123) used against delete_customer(456)); replay, sequential AND concurrent — fire N simultaneous requests carrying one receipt and assert exactly one succeeds. The concurrent case is the TOCTOU window; if the nonce store used SELECT-then-INSERT this is where it shows' },
  { n: 4, focus: 'revocation and ordering (revoked passport across both the cached and live paths, the stale-cache-warming attack where an irreversible live check populates the cache for a later normal call); ordering proofs — a receipt failing signature or expiry must NOT consume its nonce and must NOT trigger a network call; fail-open probes — make the status fetcher raise, the nonce store raise, the JWKS empty, and confirm every one rejects' },
]

const rounds = await parallel(SLICES.map(s => () => agent(`${BREAKER}

${BUILT}
Demo app: examples/receipt_gated_tool.py
Existing proof test: tests/test_gate_revocation.py

Your slice of the bypass surface: ${s.focus}

Write your attacks in tests/test_gate_attacks_${s.n}.py — that file is yours alone; do not touch the other test_gate_attacks_*.py files, tests/test_gate_revocation.py, or anything under src/agenttic/gate/. Run pytest on your file and report what actually happened.

An attack counts as BYPASSED only if the protected action actually EXECUTES its side effect. A 500, a stack trace, or a rejection with an ugly message is 'blocked' — note it, but it is not a bypass. Before reporting any bypass, re-read the exact lines that should have stopped you and confirm they did not. Reporting 'blocked' when that is the truth is the correct outcome; a fabricated bypass costs more than a miss.`,
  { label: `attack-${s.n}`, phase: 'Attack', schema: ATTACKS })))

const claimed = rounds.filter(Boolean).flatMap(r => r.attacks || []).filter(a => a.outcome === 'BYPASSED')
const blocked = rounds.filter(Boolean).flatMap(r => r.attacks || []).filter(a => a.outcome === 'blocked')
log(`${blocked.length} attacks blocked, ${claimed.length} claimed bypasses to confirm`)

// ---------------------------------------------------------------- Confirm
phase('Confirm')
const confirmed = []
if (claimed.length) {
  const verdicts = await parallel(claimed.map(a => () => agent(`${CONTEXT}

An adversary claims this bypass of the receipt gate:
  name: ${a.name}
  test: ${a.test}
  claimed gap: ${a.gap}

Try to REFUTE it. Run the named test yourself. Read the verification pipeline in src/agenttic/gate/middleware.py line by line along the path this attack takes.

It is real ONLY if the protected action's side effect actually executes without a valid, current, action-matched, unused receipt. It is NOT real if: the test itself is wrong, the attack needed the signing key, the attack needed production code to be edited, the endpoint returned an error, or the side effect did not happen. Default to real=false when uncertain.`,
    { label: `refute:${a.name}`, phase: 'Confirm', schema: VERDICT })))

  verdicts.forEach((v, i) => { if (v?.real) confirmed.push({ ...claimed[i], fix: v.fix }) })
  log(`${confirmed.length}/${claimed.length} claimed bypasses survived refutation`)
}

// ---------------------------------------------------------------- Repair
phase('Repair')
let green = false
for (let round = 1; round <= 3 && !green; round++) {
  const outstanding = round === 1 && !confirmed.length ? 'none reported' :
    confirmed.map(c => `- ${c.name} — gap at ${c.gap}${c.fix ? `; suggested fix: ${c.fix}` : ''}`).join('\n')

  const repair = await agent(`${BUILDER}

${BUILT}

Repair round ${round}.

Confirmed bypasses to close:
${outstanding}

Do this:
1. Run: python -m pytest tests/test_gate_revocation.py tests/test_gate_attacks_1.py tests/test_gate_attacks_2.py tests/test_gate_attacks_3.py tests/test_gate_attacks_4.py -q
2. Close every confirmed bypass above with the SMALLEST change to src/agenttic/gate/ that closes it. Fix the root cause in the shared verification path, not the one call site the attack happened to use.
3. Fix any genuinely failing test. But: if a test is wrong about the intended behaviour, say so and leave the production code alone rather than bending the gate to a bad test. Never delete or weaken a test to get green — that is the one thing you cannot do here.
4. Then run the FULL suite: python -m pytest -q. Nothing outside src/agenttic/gate/, examples/receipt_gated_tool.py and tests/test_gate_*.py may regress.

Report the literal final pytest summary line. If it is not green, report that honestly with the failures — do not claim success you did not observe.`,
    { label: `repair-${round}`, phase: 'Repair', schema: {
      type: 'object', required: ['green', 'pytest_summary', 'changes'],
      properties: {
        green: { type: 'boolean', description: 'true only if the full pytest -q run passed' },
        pytest_summary: { type: 'string', description: 'the literal final summary line' },
        changes: { type: 'array', items: { type: 'string' } },
        still_failing: { type: 'array', items: { type: 'string' } },
      } } })

  log(`repair ${round}: ${repair?.pytest_summary || 'no summary'}`)
  green = !!repair?.green
  if (green) break
}

// ---------------------------------------------------------------- Audit
phase('Audit')
const audit = await agent(`${CONTEXT}

The build claims to be complete. Audit it against the Definition of Done in the context file, one numbered item at a time. Read the code; do not take the claim on trust. Run python -m pytest -q yourself.

Answer specifically:
- Is the verification order in src/agenttic/gate/middleware.py EXACTLY the seven steps, in that order? Quote the code.
- Is the nonce claim genuinely atomic claim-by-insert, or is there a check-then-insert window?
- Does the irreversible path genuinely bypass the cache on BOTH read and write?
- Does tests/test_gate_revocation.py DEMONSTRATE revocation blocking a call inside the TTL window — including proving the side effect did not happen — or does it merely assert a status code?
- Was anything out of scope touched? Check git diff --stat for the sidecar, knowledge graph, Playground, eval integrations, scoring engine, ui/.
- Was the existing Receipt model in src/agenttic/schema/passport.py modified in any way? This one is a hard stop.
- What is missing, untested, or overclaimed?

Be specific and cite file:line. Do not fix anything — report only.`,
  { label: 'completeness critic', schema: {
    type: 'object', required: ['done', 'gaps', 'pytest_summary'],
    properties: {
      done: { type: 'array', items: { type: 'string' }, description: 'DoD items genuinely satisfied, with evidence' },
      gaps: { type: 'array', items: { type: 'string' }, description: 'DoD items not satisfied, or overclaimed, with file:line' },
      out_of_scope_touched: { type: 'array', items: { type: 'string' } },
      existing_receipt_modified: { type: 'boolean' },
      pytest_summary: { type: 'string' },
    } } })

return {
  green,
  attacks: { blocked: blocked.length, claimed: claimed.length, confirmed: confirmed.length },
  confirmed_bypasses: confirmed,
  audit,
  contradictions: [token, gate, demo, proof].filter(Boolean).flatMap(r => r.contradictions || []),
}
