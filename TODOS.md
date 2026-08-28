# TODOS

## Release

### Three unreleased CHANGELOG sections predate 3.0.0.0

**What:** `CHANGELOG.md` carries three `## Unreleased` sections besides the one
cut as 3.0.0.0, one of them marked BREAKING.

**Why:** Work that shipped to `master` has no version attached, so there is no
way to tell a consumer which release contains it. The BREAKING one in particular
needs a major version before anyone depends on the current behaviour.

**Context:** Found during `/ship` on 2026-08-27. The three are: "closure stops
counting what nobody measured (NUMBERS MOVE)", "the signing gate: a certificate
can no longer outrun its evidence (BREAKING)", and "Coverage-driven verification
(SPEC-13)". They arrived on `master` via merges without a version bump — the
repo had no VERSION file until 3.0.0.0. Decide whether they retro-fit into
earlier tags or roll into the next major.

**Effort:** M
**Priority:** P1
**Depends on:** None

## Completed

### The frontend does not build on master

**What:** Repair the `ui` build: two unclosed JSX tags, a build script that
calls an undefined `lint:tokens`, 16 eslint errors, and 21 failing vitest tests.

**Why:** `npm run build` fails before it reaches the typechecker, so the UI
cannot be built or deployed from `master` at all. Anyone touching the frontend
is blocked on this, and it hides any new breakage behind existing breakage.

**Context:** Found during `/ship` of `spec13/m46-claim-leg` on 2026-08-27. None
of it is caused by that branch, which changes zero `ui/` files. Verified against
a clean `master` at `6256f79`. Start with the two parse errors — everything in
`tsc` cascades from them:

- `ui/src/pages/CertifiedDirectoryPage.tsx:90` — `JSX element 'main' has no
  corresponding closing tag` (TS17008)
- `ui/src/pages/MethodologyPage.tsx:457` — `JSX element 'section' has no
  corresponding closing tag` (TS17008)

Those two account for all 38 `tsc --noEmit` errors across exactly two files.
Then define `lint:tokens` (referenced by `build`, never written) so
`npm run build` can proceed. Then the 21 vitest failures across 12 files — 5 of
those files fail to collect at all, so fix collection before counting real
assertion failures. `ui/package.json` gained a `verify` script in 3.0.0.0
(`npm run lint && tsc --noEmit && vitest run`) — that is the gate to get green.

**Effort:** L
**Priority:** P0
**Depends on:** None

**Completed:** v3.0.0.0 (2026-08-28) — branch `fix/ui-build`. Both unclosed JSX
tags closed; `lint:tokens` restored and the four raw hex values tokenised; the
scenario-run types and API that Step 17.2 dropped restored; imports repaired
across six files; `verdictScope` written; 14 `no-explicit-any` gate violations
typed. `npm run build`, `tsc --noEmit`, `npm run lint` and 512/512 vitest all
green. Landing bundle 134.5 -> 113.5 KB gz.

### Claim extraction may truncate on long agent outputs

**What:** `model_extractor` defaults to `max_tokens=2000` for structured claim
extraction over an arbitrary-length agent message.

**Why:** A verbose agent output can exceed the budget, truncating the JSON. That
raises `ClaimExtractionError`, which fails safe (the output renders as NOT
CHECKED rather than clean), but it silently reduces claim coverage on exactly
the long, chatty outputs most likely to contain a policy claim.

**Context:** `src/agenttic/verification/claim_extract.py:99`. Flagged during the
`/ship` review of 3.0.0.0 and consciously not fixed there — the failure mode is
safe, just lossy. Consider raising the default, or surfacing truncation
distinctly from other extraction failures so the loss is visible in the report.

**Effort:** S
**Priority:** P2
**Depends on:** None

**Completed:** v3.0.0.0 (2026-08-28) — branch `fix/claim-truncation`. Ceiling
raised 2000 -> 16000, and `stop_reason` is now read before the content so a
truncated or declined response is named as such instead of being reported as
an unparseable claim list.
