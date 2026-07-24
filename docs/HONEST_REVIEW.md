# Agenttic — Honest Review (UI/UX + Spec Conformance)

*A skeptical, adversarial review. Written to be useful, not flattering. Praise is
kept short; the words are spent on what's weak, half-built, inconsistent, or not
yet credible. Every claim is grounded in a file or route that was actually read.*

Date: 2026-07-02 · Scope: repo at `/home/omar/agenttic` (backend `src/ascore`, frontend `ui/src`, docs). Live public pages (`agenttic.io`) return 403 to automated fetch, so the authed console was reviewed from code.

---

## 1. The one-paragraph verdict

The **engine is more credible than the storefront.** Under the hood, Agenttic has a genuinely rigorous, unusually *honest* scoring/stats core — anchored one-criterion-per-call judge, Krippendorff's α calibration, Wilson bounds, McNemar + bootstrap, ECE, deterministic HMAC certificates, a real hardening loop, encrypted BYO-key multi-tenancy. The design system (`ui/src/theme.css`, 1,729 lines) is a real editorial system, not a template. **But** the product's north-star — "the trusted reference whose one defensible asset is the *credibility of the numbers*" — is undercut in three structural ways: (1) the authed app opens on an **n8n-style workflow canvas** ("Workflows"), which is an identity crisis for a benchmark authority; (2) the surfaces that carry the numbers to users — scorecards, results history, leaderboard, public certificate — **show bare percentages and grades without the confidence intervals, sample sizes, or methodology caveats the deeper pages compute and the docs admit**; and (3) the flagship promise, a **"verifiable" certificate, is symmetric-HMAC self-attestation** dressed as public verifiability. The rigor exists; it is inconsistently surfaced, and in the one place credibility is monetized (the badge) the claim outruns the mechanism. This is fixable, and most of the fixes are surfacing/relabeling, not rebuilds.

**Headline grade:** Engine B+/A−. Storefront/credibility surface C+. The gap between them *is* the problem.

---

## 2. What it's supposed to be (from the real docs, not the brief)

- `SPEC.md` is the original **10-step build spec**: a "UVM-style verification testbench where the DUT is an AI agent" — schema → adapters → harness → deterministic checks + LLM judge → calibration → registry → generator → live monitoring → reporting. Hard rules include *"Binary or three-point scales only. No 1–10 scoring,"* *"Judge model and agent-under-test model must differ,"* *"Agent mistakes are data: never retry them,"* *"Provisional (uncalibrated) scores are always labeled as such"* (`SPEC.md:267-278`). Notably, the spec says **"No web UI in MVP. CLI + JSON/Markdown reports only"** (`SPEC.md:35`) — the entire UI is post-spec scope.
- `CAPABILITIES.md` is the current product map, and it is refreshingly candid: *"the seeded standard suites implement these methodologies on Agenttic's own seed data — they are not the public datasets and don't reproduce any paper's numbers. The Index is empty until you run an agent with your own Anthropic key. SWE-bench is scored by an offline proxy, not its official Docker resolve-rate"* (`CAPABILITIES.md:40-44`).
- `docs/INDEX.md`, `docs/CERTIFICATION.md`, `docs/SAFE_ASSISTANT.md`, `docs/DEPLOYMENT_SAFETY.md` describe the safety-certification wedge, the A–F rubric (HMAC issuance, config_hash pinning, revocation, honesty gate), and the reference assistant.

The yardstick, then, is the founder's phased thesis: **Phase 0** credibility foundation → **Phase 1** one verifiable wedge on established public benchmarks → **Phase 2** the hardening loop + one excellent dashboard → **Phase 3** publish/become the reference → **Phase 4** more agent types; plus BYO-key multi-tenancy, the public safety-cert wedge, and the Training Camp promotion-gate layer.

---

## 3. Inventory — what's actually built

### 3.1 Backend (141 Python modules, 19 route files)

| Area | Verdict | Evidence & the single most damning gap |
|---|---|---|
| **Scoring core** (judge, checks, calibration, engine) | **REAL** | One-criterion-per-call judge with scale + pass/fail anchors + trajectory evidence, strict-JSON + one retry, judge≠agent enforced (`scoring/judge.py:76-160,110-118`). Calibration is genuine: human-label CSV, exact-match binary + Krippendorff's α interval, provisional flagging below threshold (`scoring/calibration.py:38-97`). **Gap: no shipped human-label corpus** — calibration is a capability, not a demonstrated fact. "The judge is calibrated" is currently unproven. |
| **Stats** | **REAL** | Wilson lower bound (`camp/trainer.py:24`), McNemar exact/χ²-cc + paired bootstrap CIs with underpowered flag (`stats.py:64,135`), ECE (`metrics/calibration.py:13`). This is the strongest part of the codebase. |
| **Standard benchmark track / datasets** | **PARTIAL** | Real adapters parse actual BFCL v3 / τ-bench / AgentHarm / AgentDojo / InjecAgent / AssistantBench / GAIA / SWE-bench records, but default to *tiny vendored samples* (SWE-bench **7** cases, GAIA **6**, AssistantBench **16**); the 4 `std-*` suites are Agenttic's own seed data. **Gap: the code wedge has no real resolve-rate** — `harness_available()` hard-returns `False`, `resolve_rate()` raises (`metrics/swebench_resolve.py:56,70`); safety scoring is black-box lexical refusal-marker/target-token matching (`metrics/canonical_checks.py:31-45,184-207`), not the real attack environments. |
| **Certification** | **REAL, with one asterisk** | Deterministic bands + critical-failure cap (`certification.py:143-244`), HMAC-SHA256 over canonical JSON (`:269-290`), config_hash pinned from a real run trace and refused if absent (`server/certifications.py:26-40`), revocation stored outside the signed payload, honesty gate. **Gap: not third-party verifiable — HMAC is symmetric,** so only the issuing server (holding `AGENTTIC_SECRET_KEY`) can verify. A **dev fallback secret `"ascore-dev-insecure-secret"`** exists (`certification.py:266`). |
| **Hardening loop** | **REAL** | Versioned, append-only, fingerprint-deduped regression suites with provenance (`hardening.py:118-215`); re-run + McNemar delta classifying improved/regressed/same/new (`:459-522`); live catches left unapproved so the human gate must clear them — refuses to fabricate ground truth (`:322-442`). The most fully realized differentiator. |
| **Training Camp** | **PARTIAL** | Real promotion architecture: frozen holdout never used for training (`camp/holdout.py`), Wilson-lower-bound floor + mandatory human sign-off, deny-by-default (`camp/gate.py:41-80`), champion/challenger ratchet with collapse guard (`camp/improve.py:214-279`). **Gap: nothing trains a real model.** "Training" is symbolic token→action rule mining (`camp/improve.py:71-111`) over a synthetic `MockSupportEnv` (`camp/environment.py:43`); browser/Android envs are honest `NotImplementedError` stubs (`:72-110`). The gate is production-grade; the learner is a demo. |
| **Scan / Connect / Safe Assistant** | **REAL (seed/lexical caveat)** | Scan reuses the cert rubric and returns `gradeable:false` rather than an F when all cases error (`scan.py:86-111`) — honest. SSRF-guarded black-box adapter (`connect.py:280`). Assistant's five defenses are wired into the loop (`assistant/agent.py`, `guard.py`). **Gap: injection/refusal scoring and the guard neutralizer are regex/lexical** — defeatable by novel phrasing; a badge earned on ~a dozen seed probes overstates coverage. |
| **Optimizer & A/B** | **REAL** | Genuine OPRO/ProTeGi reflective search with paired-significance acceptance, per-criterion regression veto, held-out overfit guard (`optimizer.py:1-92`); A/B computes over the paired subset + McNemar/bootstrap (`ab.py:135-190`). |
| **Multi-tenancy / BYO key** | **REAL** | Fernet encryption at rest, key masked on read, `tenant_run_clients` raises 400 rather than ever falling back to a platform key (`server/keys.py:111-124`); registry tenant-scoped throughout. **Gap: same dev-fallback-secret footgun** if `AGENTTIC_SECRET_KEY` is unset in prod. |

### 3.2 Frontend surface

Public: Landing, Scan, Assistant, Methodology, Certified directory + public Certificate, API docs, auth pages. Console (behind auth, 12 sidebar items + 2 external links): Workflows (Editor), Runs, Results, Compare, Leaderboard, Certification, Training Camp, Hardening, Optimize, Agents, Resources, Settings.

---

## 4. UI/UX critique (the core of this review)

### 4.1 Information architecture & navigation — **the biggest structural problem**

- **The authed app opens on a workflow canvas.** `/app` index renders `EditorPage` (`AppShell.tsx:145`), labelled **"Workflows"** in the sidebar (`AppShell.tsx:104`). A benchmark *authority* should open on results/leaderboard/a scorecard — not a blank n8n graph with a node palette, ports, edges, and workflow JSON import/export (`EditorPage.tsx:242-257`). The Guided flow inside it is genuinely good (`workflow/GuidedFlow.tsx`), but "Advanced" mode leaks the execution engine's internals to end users. This is a leftover from the "visual workflow builder" era and it's the first thing a logged-in user sees.
- **Too many nav items, flat hierarchy.** 12 sidebar destinations + "API docs" + "Safe assistant" (`AppShell.tsx:104-117`), each a differently-themed emoji, no grouping. Workflows/Runs/Results/Compare/Leaderboard/Certification/Training Camp/Hardening/Optimize/Agents/Resources/Settings is a *pile of pages*, not an IA. Several are power-user/admin surfaces (Agents, Resources, Optimize, Training Camp) sitting at the same level as the core loop. There's no notion of "here's the one thing to do."
- **Two products wearing one skin.** The public site is a consumer **"Agent Safety Certification"** funnel (`LandingPage.tsx`: "Is your AI agent safe to ship?"), while the console is a benchmarking/ML-eval workbench. The reliability thesis (SWE-bench/τ-bench/BFCL/tool-calling) barely appears in the public IA — the landing page markets only the 4 safety checks. A visitor cannot tell this is a *reliability + safety* reference; they see a free safety scanner.

### 4.2 Visual design & credibility

- **Credit where due (briefly):** `theme.css` is a real design system — Clay accent, Newsreader serif for headlines/figures, Hanken Grotesk UI, JetBrains Mono for IDs/numbers, a full type/spacing scale, dark-default + a warm-cream light variant, a single consistent focus ring, `prefers-reduced-motion` honored, skeletons/empty states as shared primitives (`components/ui.tsx`). At the CSS level this does *not* read as templated internal tooling. Compare/Optimize/Hardening/GuidedFlow are genuinely well-made.
- **But credibility cracks at the data layer, not the pixel layer.** The pages that carry *numbers* betray the "internal tool" origins: `ExecutionsPage.tsx:91` renders `JSON.stringify(node_outputs)` in a `<pre>`; `ResourcesPage.tsx:126` dumps raw trace spans; `AgentsPage`/`ResourcesPage` are essentially DB-table browsers with bare mono IDs and raw config forms. A reference authority's artifacts should never be a `JSON.stringify`.
- **Concrete CSS bug:** the Connect-panel error states reference undefined tokens `--bad`, `--bad-soft`, `--bad-border` (`theme.css:1389,1399`) — the defined tokens are `--fail*`. So the "connection test failed" and connect-error messages render with invalid/transparent color. This is on a public trust surface (the scan page's Connect panel).

### 4.3 Data presentation & honesty — **the credibility keystone, and it's inconsistent**

The thesis is "credibility of the numbers." The app computes rigorous statistics but frequently strips them before display:

- **Headline accuracy shown as a bare `%` with no n, no CI, no sample size** on the surfaces users actually read: Results history (`ResultsHistoryPage.tsx:82` → `Math.round(rate*100)%`), the scorecard headline (`panels/ResultsPanel.tsx:41-44`), Resources scorecards (`ResourcesPage.tsx:91`), the public certificate per-dimension bars, and the **public leaderboard Index has no confidence interval at all** — the Wilson bound is computed and shown *only* in Training Camp. So the most public, most authority-defining number is the least hedged.
- **The `0.7` pass threshold is a hard-coded magic number** repeated inline (`workflow/templates.ts:131`, `panels/ResultsPanel.tsx:41`) with no tooltip/methodology explaining what "pass" means or that it's configurable. "72% pass" is meaningless without it.
- **`?? 0` fabricates data:** missing cost/latency render as authoritative `$0.0000` / `0ms` instead of "—" (`panels/ResultsPanel.tsx:139-143`, `ComparePage.tsx:143-153`).
- **The Methodology page over-claims verifiability.** It says the certificate *"is cryptographically signed, so the grade and scores… can be verified as issued by Agenttic and unaltered"* (`MethodologyPage.tsx:369-390`) and the Certificate page echoes it — but verification is a server-computed boolean `cert.signature_verified` (`CertificatePage.tsx:144-146,188`); the signature is never exposed and the secret is symmetric. **A third party cannot verify anything; they can only trust the issuer's self-report.** For a product whose moat is verifiable numbers, this is the single most important honesty gap.
- **The SWE-bench-proxy caveat is hidden from users.** `CAPABILITIES.md:43` admits it; the Methodology page lists datasets but never discloses that SWE-bench is an offline proxy.
- **A 14-probe scan mints the same seal/grade/certificate as a rigorous k=3 canonical run** (`ScanExperience.tsx:428` says "~14 short safety prompts") with no rigor label distinguishing them. A drive-by scan and a full run produce visually identical "Agenttic Safety Certified" badges.
- **"Secret-leak resistance" is advertised as a graded dimension** (Methodology, `cert.ts` labels) yet is **not among the six weighted Index metrics** in the catalog — implying a test that isn't actually in the score.

Where honesty *is* done right, it's excellent and should be the template: ComparePage's McNemar + "too few to conclude" underpowered state (`ComparePage.tsx:161`), OptimizePage's train-vs-heldout overfit gap, HardeningPage's "live catches never fabricate ground truth," and TrainingCampPage using the **Wilson lower bound (not the point estimate) as the gate**. The app already knows how to be honest — it just doesn't do it on the scorecard/leaderboard/certificate.

### 4.4 Key flows

- **Onboarding / BYO-key:** solid. The `AppShell` key-nudge banner (`AppShell.tsx:131-142`) and `SettingsPage` test-before-save with encryption reassurance are clear.
- **Scan → grade → certify:** complete and does not dead-end (`ScanExperience.tsx` GradedActions 436-472, with a no-cert fallback and a good auth/key/other error taxonomy). Good.
- **Read a scorecard:** weakest core flow — see §4.3 (bare %, no CI, hard-coded threshold, JSON dumps in adjacent pages).
- **Get a certification:** the in-app issue/embed/revoke flow (`CertificationsPage.tsx`) is complete; the *claim* about what the badge proves is the problem, not the flow.
- **Run a camp:** TrainingCampPage is complete and honest, but dense power-user jargon (ratchet, holdout, collapse guard) one nav-click from consumer-voice pages.
- **Dead ends / stubs visible to users:** Billing is an explicit **"Billing coming soon"** with `$—/mo` and a disabled button (`SettingsPage.tsx:261-265`); landing pricing is **"Soon" / "Join the waitlist"** (`LandingPage.tsx:26-30,164`); AgentsPage renders a `managed` variant branch (`:102-117`) that the `<select>` can never produce (`:74-78`) — dead/unreachable UI.

### 4.5 Consistency

Mostly one design language (shared `PageHeader`/`EmptyState`/`Skeleton`/`status-chip`/`data` tables). Drift points: SettingsPage rolls its own `Card`/`Spinner` instead of the shared primitives; OptimizePage skips `PageHeader` during load and leans on ML jargon ("OPRO/ProTeGi" in the subtitle). **Training Camp does NOT drift** — it correctly matches the console (not the marketing) language. The real inconsistency is *tonal*: consumer voice (Scan/Assistant/Landing) vs ML-researcher console, with no bridge.

### 4.6 Mobile / responsive / accessibility

Better than expected. Real breakpoints at 860/640/560/880px collapse the sidebar to a scrollable top bar, stack the settings nav, single-column the grids, and reflow the score-strip (`theme.css:911-948,1028-1034,1295-1301,1501-1510,1719-1729`). A11y: one consistent `:focus-visible` ring across interactive elements (`:771-787`), `.sr-only`, `aria-busy` skeletons, `prefers-reduced-motion`. Gaps: heavy reliance on **emoji as semantic icons** in nav/features (no `aria-label`), status is often conveyed by **color alone** (delta chips, idx bars, status dots) which is a contrast/colorblind risk, and the true-black `#000` bg with `--faint:#6e6a64` micro-labels is below WCAG AA for small text.

---

## 5. Spec conformance verdict, by phase

| Phase | Stands up? | Why |
|---|---|---|
| **0 — Credibility foundation** (trustworthy scoring, calibration) | **Mostly** | The machinery is all real and correct (anchored judge, Krippendorff, Wilson, McNemar, bootstrap, ECE). Two holes: **no shipped human-label corpus** proving the judge is actually calibrated, and the UI **strips the uncertainty** it computes on the surfaces that matter most. Phase 0's whole point is trustworthy numbers *shown* trustworthily — half-done. |
| **1 — One verifiable wedge on public benchmarks** | **Partial** | Real dataset adapters + defensible scoring exist, but they run tiny seed samples, and the intended **code wedge (SWE-bench) has zero execution/resolve-rate** — it's a proxy. There is no wedge with reproduced public numbers yet. |
| **2 — Hardening loop + adversarial/red-team + economic + one dashboard** | **Partial** | Hardening promotion/rerun/delta is real and strong; per-run cost is tracked. But "adversarial" is **lexical seed probes, not real red-team**, "economic scorecard" is just cost, and there is **no single excellent dashboard** — there are 12 pages, and the default one is a workflow canvas. |
| **3 — Publish / become the reference** (leaderboard, methodology, verifiable badges) | **Not yet** | Methodology + leaderboard + certificate pages exist and look the part, but **certs aren't third-party verifiable** (symmetric HMAC), the Methodology **over-claims** it, there are **no reproduced public numbers** to publish, and the leaderboard shows bare ranks without CIs. Credibility-gated — and the gate isn't cleared. |
| **4 — More agent types** | **No** | Browser/Android environments are honest `NotImplementedError` stubs; only text/tool agents run. |

---

## 6. Prioritized recommendations

### Quick wins (surfacing / relabeling / bug fixes — days, not weeks)

1. **Stop stripping the uncertainty you already compute.** Show `n`, the pass/fail/errored split, and the **Wilson interval** next to every headline `%` — scorecard, results history, leaderboard, and each certificate dimension. The functions exist (`stats.py`, `camp/trainer.py`); wire them into `ResultsPanel`, `ResultsHistoryPage`, `LeaderboardPage`, `CertificatePage`. This is the single highest-leverage credibility fix.
2. **Relabel the certificate honestly** OR make it real. Cheapest: change "cryptographically verifiable / verified as issued and unaltered" to **"issuer-signed, tamper-evident"**, expose the signature string + a "verify" endpoint, and say plainly that verification is against Agenttic's server. (The real fix is #3 below.)
3. **Disclose the caveats the docs already admit** on the public Methodology page: SWE-bench is an offline proxy; standard suites run on seed data, not the public datasets; distinguish a 14-probe **Scan grade** from a full canonical run (add a "rigor: quick scan" vs "full suite" label on the seal/certificate).
4. **Remove the magic number.** Surface the `0.7` pass threshold as a labelled, tooltipped, configurable value; never render "pass" without it.
5. **Fix the concrete bugs:** undefined `--bad*` CSS tokens (`theme.css:1389,1399`); replace `?? 0` cost/latency fallbacks with "—"; delete the unreachable `managed` branch in `AgentsPage`; stop dumping raw `JSON.stringify` in Executions/Resources (render a real span/step view or hide behind a "raw" toggle).
6. **Fix the brand-domain confusion.** `cert.ts:122` falls back to `https://agenttic.ai` (a *different, unrelated Spanish-language product*) while ApiDocs uses `agenttic.io`. Pick one canonical host and use it everywhere — this matters most on the certificate/verify surface where the URL *is* the trust.
7. **Hide the unfinished corners** or gate them behind a flag: "Billing coming soon," landing "waitlist" pricing.

### Bigger reworks (weeks — do after the quick wins)

8. **Fix the front door.** Make `/app` open on a **results/leaderboard dashboard**, rename "Workflows," and demote the canvas/Advanced mode to an opt-in "power" tool. Collapse the 12-item nav into ~4 groups (Benchmark · Improve · Certify · Manage). This resolves the identity crisis and is Phase 2's missing "one excellent dashboard."
9. **Make one wedge actually reproduce public numbers.** Pick tool-calling (BFCL/τ-bench) or code (SWE-bench). For real credibility, SWE-bench needs the execution harness that's currently stubbed (`swebench_resolve.py`), or pivot the public claim to the tool-calling wedge and prove it against the full public split. Without one reproduced public number, Phase 1/3 don't clear.
10. **Make the certificate third-party verifiable for real:** asymmetric signing (Ed25519), publish the public key, and give the public cert page an offline/independent "verify" affordance. This is the moat; symmetric HMAC can't be it.
11. **Ship the calibration corpus.** Hand-label ≥30–50 traces per active criterion, run the (already-built) calibration report, and show calibrated-vs-provisional status on every score. Until then, the honest move is to label the judge scores **provisional** in the UI — which the spec's Hard Rule 6 already requires.
12. **Turn "adversarial" into real red-team.** The lexical refusal/injection heuristics should be backed by (or clearly downgraded to a screen for) the actual AgentDojo/InjecAgent environments before the safety grade is marketed as authoritative.

---

## 7. Bottom line for the founder

You have built the hard part — a rigorous, honest scoring/stats/hardening engine that most competitors fake. The risk isn't the math; it's that **the credibility you've earned in the engine isn't reaching the surfaces where users judge you**, and in the one place it's productized (the "verifiable" badge) the claim is ahead of the mechanism. Nothing here requires throwing work away. Surface the uncertainty, tell the truth the docs already tell, fix the front door, and make one certificate genuinely verifiable — do those and the "trusted reference" thesis becomes defensible. Ship the marketing of "the reference" only *after* one wedge reproduces one public number with its interval shown.
