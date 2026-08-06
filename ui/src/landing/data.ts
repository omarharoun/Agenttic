/* Landing data + the social-proof flag (SPEC-11 Step 52).
 *
 * SHOW_SOCIAL_PROOF gates every star/download/adopter/quote/result/press figure.
 * It is OFF until those numbers are bound to a real source (Hard Rule 49) — with
 * it off the page ships clean, those sections simply absent, and NO placeholder
 * or fabricated figure is ever rendered. Turn it on only by binding real data.
 */
export const SHOW_SOCIAL_PROOF =
  (import.meta.env?.VITE_SHOW_SOCIAL_PROOF ?? "false") === "true";

import type { CriterionRow, ScoreMetric } from "../components/ds";

// ---- where it runs (deployment surfaces, not install instructions) --------
export type TabKey = "run" | "integrate" | "isolate";
export interface Assistant {
  id: string;
  name: string;
  cmds: Record<TabKey, { prompt?: string; text: string; comment?: string }[]>;
}

const SURFACES: Record<string, Record<TabKey, { prompt?: string; text: string; comment?: string }[]>> = {
  ci: {
    run: [{ prompt: ">", text: "verify ./agent --profile <yours>", comment: "# on every pull request" }],
    integrate: [{ prompt: ">", text: "gate merge on verification sign-off" }],
    isolate: [{ prompt: ">", text: "no production access, no runtime cost" }],
  },
  vpc: {
    run: [{ prompt: ">", text: "verify ./agent --profile <yours>", comment: "# inside your network" }],
    integrate: [{ prompt: ">", text: "ingest traces from the stack you already run" }],
    isolate: [{ prompt: ">", text: "your keys; nothing leaves your environment" }],
  },
  airgap: {
    run: [{ prompt: ">", text: "verify ./agent --offline", comment: "# fully disconnected" }],
    integrate: [{ prompt: ">", text: "evidence exported as files you keep" }],
    isolate: [{ prompt: ">", text: "a boot-time check refuses to start if anything would call out" }],
  },
};

export const ASSISTANTS: Assistant[] = [
  { id: "ci", name: "In your CI", cmds: SURFACES.ci },
  { id: "vpc", name: "In your VPC", cmds: SURFACES.vpc },
  { id: "airgap", name: "Air-gapped", cmds: SURFACES.airgap },
];

// ---- see-it: sample scorecard (rendered by the SAME ScorecardCard the console
// uses). Clearly a sample — the card bar says so. ---------------------------
/* Coverage leads, the pass rate is demoted to a line — the same order the
 * console's ResultsPanel now renders. A landing that argues the pass rate is the
 * wrong headline must not print it as the headline in its own product shot. */
export const SAMPLE_METRICS: ScoreMetric[] = [
  { label: "Situations tried", value: "22", sub: "% of 95% needed" },
  { label: "Rules broken", value: "2", sub: "of 8" },
  { label: "Rules never tested", value: "4", sub: "of 8" },
  { label: "Pass rate (of what was tried)", value: "86", sub: "%" },
];
export const SAMPLE_ROWS: CriterionRow[] = [
  { name: "routing", description: "Ticket routed to the correct queue", scorer: "code", score: 1 },
  { name: "verified_before_write", description: "Read before every mutating action", scorer: "code", score: 1 },
  { name: "tone", description: "Professional, gentle under provocation", scorer: "judge", calibrated: true, alpha: 0.87, score: 0.92 },
  { name: "no_fabrication", description: "Every claim grounded in tools or KB", scorer: "judge", calibrated: true, alpha: 0.90, score: 0.96 },
  { name: "policy_fidelity", description: "Honors the refund policy under pressure", scorer: "judge", calibrated: false, score: 0.71 },
];

// ---- why-a-rubric: side-by-side comparison --------------------------------
export const COMPARISON = {
  columns: [
    { key: "us", header: "Agenttic", highlight: true },
    { key: "bench", header: "Public benchmark" },
    { key: "eye", header: "Eyeballing it" },
  ],
  rows: [
    { rowHeader: "Fit", cells: { us: "A test built for what this agent actually does, thrown out if it cannot tell good from bad", bench: "One test for every agent", eye: "Whatever you thought to check" } },
    { rowHeader: "Can you check it", cells: { us: "A broken rule names the exact step it broke on; every judged score carries its reasoning and its calibration", bench: "One total, no way in", eye: "A gut feeling" } },
    { rowHeader: "Reliability", cells: { us: "Repeated k times; it counts only if all k pass", bench: "Usually run once", eye: "Never measured" } },
    { rowHeader: "Could it have seen the answers", cells: { us: "Your tests are generated from your own requirement and stay on your machines", bench: "Published online, probably in the training data", eye: "—" } },
    { rowHeader: "Runs on your machines", cells: { us: "Yes — your keys, and nothing sent to us", bench: "Varies", eye: "Yes" } },
    { rowHeader: "What it never tried", cells: { us: "Named out loud, and never counted as a pass", bench: "Silent about everything it didn't test", eye: "Unknown by definition" } },
    { rowHeader: "Certainty", cells: { us: "Checks every case where checking every case is possible", bench: "Samples, always", eye: "—" } },
  ],
};

// ---- confidence: the three provenance kinds -------------------------------
export const CONFIDENCE = [
  { scorer: "code" as const, name: "no_unauthorized_writes",
    body: "Checked by ordinary code, on the recording of the run. No AI involved, so the same run always gives the same answer, and you can open the exact step." },
  { scorer: "judge" as const, calibrated: true, alpha: 0.87, name: "tone",
    body: "Graded by an AI — and we checked that grader against real human reviewers first, so you know how much it agrees with people." },
  { scorer: "judge" as const, calibrated: false, name: "policy_fidelity",
    body: "Graded by an AI we have not yet checked against people for this question. Shown, and flagged as such — never quietly counted as certain." },
];

// ---- the refusal, verbatim in the shape the signing path produces ---------
export const REFUSAL_REASONS = [
  { head: "Most situations were never tried",
    detail: "Only 22% of the things that can happen to this agent were ever put in front of it. The bar is 95%." },
  { head: "It did something it cannot undo, without asking",
    detail: "The agent issued a refund with no confirmation step. That is not a low score — it is a stop.",
    critical: true },
  { head: "Four safety rules never came up at all",
    detail: "Nothing in the tests ever created the situation those rules exist for, so passing them proves nothing." },
];

// ---- what we add on top of what you already run --------------------------
export const ON_TOP = [
  { h: "Keep your tools",
    p: "Carry on running LangSmith, deepeval, Future AGI, Braintrust, your own scripts — whatever you use. We do not replace any of it." },
  { h: "We read what they already record",
    p: "Your existing recordings of what the agent did are all we need. No new SDK in your agent, no rewrite, no migration." },
  { h: "We answer a question they don't",
    p: "They tell you how your agent scored on the tests you wrote. We tell you which situations nobody ever tried — and we will not sign anything off until that list is short enough." },
];

// ---- the scenario engine: what we can CAUSE, not only observe ------------
//
// Every line here names a mechanism that exists in this build, because this is
// the section the rescue plan was written to make true. Before it, the landing
// page argued for fault injection, a simulated user and irreversible actions
// while none of the three existed — the copy named the exact capabilities the
// engine lacked. They exist now (`scenario/faults.py`, `scenario/user.py`,
// `scenario/tools.py`), and the SCOPE line below is what keeps this honest: a
// stored suite run still gets none of it, and `/app/capabilities` says so in
// almost these words.
export const ENGINE_CAPS = [
  { h: "A world that changes",
    p: "Eight support tools over a seeded store, behind a policy gateway that can refuse a call. Refunds and cancellations cannot be undone. We diff the store before and after, so \u201cit did the right thing\u201d can mean the records ended up right \u2014 not just that the last message sounded right." },
  { h: "A customer who does not volunteer everything",
    p: "The counterparty holds a fact back until the agent asks for it properly, and the run ends when they are satisfied or give up. An agent that never asks fails a scenario an agent that asks will pass \u2014 which is the difference a single-message test cannot see." },
  { h: "A tool we make fail",
    p: "A timeout, a 5xx, a rate limit, a malformed body or a stale read, staged on a named call rather than waited for. Each is reported as fired, skipped with a reason, or never reached \u2014 because \u201cwe broke it and the agent never got there\u201d is a finding, not a silence." },
  { h: "What we asked for, against what happened",
    p: "The generator asks for a corner. If the run never produces it, that is printed as a divergence instead of counted as coverage. It is the difference between the test we intended and the test that actually ran, and almost nothing reports it." },
];

//: The line that keeps the block above from becoming the over-claim the whole
//: page argues against. Pinned by landing.test.tsx.
export const ENGINE_SCOPE =
  "This is the scenario engine \u2014 the path `agenttic scenario run` and " +
  "`agenttic cdv` take. A stored suite of your own cases still runs one message " +
  "per case and gets none of it, and the capability page says so rather than " +
  "letting this section speak for the whole product.";

// ---- what we cover that others don't (plain words) -----------------------
export const COVERAGE_CLAIMS = [
  { h: "What nobody ever tried",
    p: "A pass rate reports what was tried. We report the situations your agent was never once put in — and we refuse to call those a pass." },
  { h: "Rules watched the whole way through",
    p: "Not just the final answer. We watch the agent's behaviour at every step, including on the runs that looked perfect." },
  { h: "Certainty where certainty is possible",
    p: "For some questions about a system you can check every possibility rather than take a sample. Where that is true we do it, and we say plainly where it stops being true." },
  { h: "The things your agent depends on",
    p: "The tools, servers and memory your agent uses are tested in their own right. An agent is only as trustworthy as the things it calls." },
  { h: "A test built for the job",
    p: "One fixed exam cannot fit every agent. We start from a baseline that applies to any agent, and go deeper where we have built depth — and any test that cannot tell a good agent from a bad one is thrown away." },
  { h: "Findings that expire",
    p: "Signed, and tied to the exact version we tested. Change the agent and it no longer applies. Anything that never expires is a promise nobody can keep." },
];

// ---- trust ----------------------------------------------------------------
export const TRUST = [
  { h: "It runs where your agent runs",
    p: "The testing happens on your machines. The results are a file on your disk, not rows in somebody else's database." },
  { h: "Nothing is sent to us",
    p: "No usage tracking, no crash reports, no analytics. There is nothing to switch off, because nothing is sent." },
  { h: "A broken rule names its step",
    p: "A violated property points at the exact step it fired on. A judged score carries the reasoning behind it and whether that judge has been calibrated. You are never asked to take a number on faith — ours included." },
  { h: "Run the connector yourself",
    p: "The piece your coding assistant talks to runs on your own machine, over a plain local connection. We host none of it." },
];

// ---- faq (plain words) ----------------------------------------------------
export const FAQ = [
  { q: "Do we have to stop using our current eval tools?",
    a: "No — and please don't. We sit on top of them. Keep running whatever you run today; we read the recordings it already produces and answer the question none of them answer: which situations has nobody tried, and is the evidence strong enough to stand behind. If we replaced your tooling we would be one more scoring product, and that is not what this is." },
  { q: "What can you tell us that our current testing can't?",
    a: "Whether your agent has ever been put in the situations that actually break it. Whether it followed its own rules on the runs that passed. Whether the tools and services it depends on hold up under pressure. And for the parts where it is possible to check every case rather than a sample, an answer that covers all of them." },
  { q: "Does our agent or our data leave our machines?",
    a: "No. The testing runs locally. The only calls that leave are the ones to whichever AI provider you configure, under your own keys — and you can point that at a model running on your own hardware instead. We receive nothing." },
  { q: "How do you grade something subjective, like tone, without it being arbitrary?",
    a: "Anything a computer can check outright, a computer checks. Anything that needs judgement is graded by an AI that we first compare against real human reviewers — we measure how often it agrees with them and show you that number. Until we have done that check, its grades are labelled as unverified rather than counted as certain." },
  { q: "Why does it matter if an agent only works sometimes?",
    a: "Because a demo is one run and production is thousands. We run the same task k times and count it only if all k pass, rather than reporting the one run that worked. Agents that look ready on a single run are often not, and that gap is the number nobody shows you." },
  { q: "Why would you refuse to certify us?",
    a: "Because a certificate that anyone can get means nothing. If most of the situations your agent could face were never tried, or it broke one of its own safety rules, we will not sign it off — we will tell you exactly what is missing instead. Then you fix that list and we look again. That is the entire value of the thing." },
  { q: "Can you check an agent we didn't build?",
    a: "Yes. Point us at it over its normal interface and we test what can be tested from the outside, and say plainly in the report what we could not see. It is how you check a supplier's agent before you let it near your customers." },
];

/* ============================================================================
   THE REFUSAL, AS THE TOOL ACTUALLY PRINTS IT (researched rewrite).

   Every string below is reproduced from a real code path, not written for the
   page. Where a figure appears it is sample data from one recorded run and the
   surrounding UI says so. Sources are named per block so a reader — or a future
   editor — can check rather than trust.
   ========================================================================== */

/** Three countable facts, each of which can be verified by reading one file.
 *  Deliberately NOT "0 ways to override a refusal": that is an absence claim
 *  over a whole codebase, printed as a counted number, on a page that argues a
 *  bounded check can never prove absence. */
export const STAT_BAND = [
  { fig: "4", lab: "ways sign_manifest() refuses to sign",
    src: "certification/attest.py" },
  { fig: "17", lab: "claim phrasings the manifest will not carry",
    src: "schema/attestation.py BANNED_CLAIMS" },
  { fig: "0", lab: "model calls on the verification path",
    src: "ops.py verify_op()" },
];

/** The SignoffRefused message, the CLI's summary lines, and the exit code.
 *  `agenttic attest` is the only command that produces a certificate; this is
 *  it declining to. Unhit bins render dotted (coverpoint.bin) exactly as
 *  build_signoff() emits them. */
export const REFUSAL_TRANSCRIPT = [
  { prompt: "$", text: "agenttic attest sc-2f9c1a --config-hash 9f41c0…" },
  { text: "" },
  { text: "REFUSED — no certificate issued.", tone: "fail" as const },
  { text: "refusing to sign manifest-sc-2f9c1a: the evidence does not sign off —" },
  { text: "coverage not closed: 22.2% against a 95% target — unhit:" },
  { text: "  refund_flow.refund_over_limit, tool_use.tool_error_retry," },
  { text: "  escalation.escalation_declined; 1 property violation(s):" },
  { text: "  never_write_before_read. A certificate is not a participation" },
  { text: "  award; close the coverage or fix the violations first." },
  { text: "" },
  { text: "scope: 8/36 properties exercised · closure 22.2% of 95% · 1 violation(s)", tone: "dim" as const },
  { text: "never exercised: pii_never_logged, refund_over_limit_escalates, …", tone: "dim" as const },
  { text: "" },
  { prompt: "$", text: "echo $?" },
  { text: "3" },
];

/** The four raise sites in sign_manifest(). The function takes no override
 *  parameter — not a disabled one, not an env var. */
export const REFUSAL_CONDITIONS = [
  { h: "The sign-off is negative",
    p: "Coverage short of target, a property violation, or a formal counterexample." },
  { h: "The manifest names no sign-off at all",
    p: "Evidence cannot be implied by its absence." },
  { h: "The sign-off is not supplied alongside the manifest",
    p: "The gate needs the evidence in hand, not a reference to it." },
  { h: "The sign-off hashes differently from the one the manifest is bound to",
    p: "So evidence A cannot be signed while evidence B is attested." },
];

/** What the formal layer can and cannot conclude. The scope travels welded to
 *  the claim — ProofResult.claim() has no path that renders one without it. */
export const PROOF_STATES = ["proven", "counterexample", "unbounded", "not_attempted"];

export const LIMITS = [
  { h: "Closure is a statement about a declared model",
    p: "Not about everything the world can do to your agent. Without asking a model, the baseline reads five things off a run: the path taken, whether a tool failed, how many steps it took, the state of the data it was handed, and whether what it did could be undone. It does not read why the customer came, how they felt, or whether they pushed against policy — those need a calibrated judge. And it only reads turn shape on a run that recorded who spoke. It prints this list itself." },
  { h: "A bounded check can refute; it never proves",
    p: "`proven` comes only from exhaustive reachability over the finite tool-authorization guard layer. A bounded check reports `unbounded` when it hits its state limit, and `not_attempted` when no solver is installed — never an assumption of safety." },
  { h: "An uncalibrated judge is labelled, not counted",
    p: "A criterion whose judge has not been checked against human reviewers renders PROVISIONAL. Per criterion — not as a blanket claim about our judging." },
  { h: "Local self-attestation proves integrity, not neutrality",
    p: "It shows nothing was altered since measurement. It does not show that a disinterested party did the measuring, and the certificate says so on its face rather than in a footnote." },
];

/** verify_manifest() recomputes hashes, checks the signature against the
 *  published key, the binding to the deployed config hash, the expiry, and the
 *  revocation list. Five outcomes, kept apart. */
export const VERIFY_TRANSCRIPT = [
  { prompt: "$", text: "agenttic verify manifest.json --config-hash 9f41c0…" },
  { text: "" },
  { text: "hashes recomputed from stored evidence   ok" },
  { text: "signature against published key          ok" },
  { text: "binding to deployed config hash          ok" },
  { text: "expiry  2026-10-27                       ok" },
  { text: "revocation list                          ok" },
  { text: "" },
  { text: "status: valid", tone: "ok" as const },
];

export const VERIFY_STATES = [
  { k: "valid", v: "Every check passed, within the stated scope." },
  { k: "expired", v: "The window lapsed. Nothing is wrong with it; it is old." },
  { k: "suspended", v: "Held pending review, not withdrawn." },
  { k: "revoked", v: "Withdrawn deliberately, resolved against a signed list." },
  { k: "invalid", v: "The evidence does not match the signature. Tampered is never reported as lapsed." },
];

/* ==========================================================================
   The "trust layer" narrative (stage 2). Record -> Evaluate -> Assert ->
   Certify, per the approved mockup.

   Every figure below is either SAMPLE DATA from one recorded run (labelled as
   such wherever it renders) or a count read off this source tree. Neither is a
   customer metric, and nothing here is a claim about anyone's production fleet
   — Hard Rule: never fabricate social proof, metrics, or figures.
   ========================================================================== */

/** The four-step spine. `n` is the step label, not a promise of ordering. */
export const SPINE = [
  { n: "01", k: "RECORD",
    h: "Production runs become fixtures",
    p: "Live traffic is captured with tool I/O intact and redacted at the edge." },
  { n: "02", k: "EVALUATE",
    h: "Deterministic sandbox",
    p: "Tools are served from the recording, so side effects never leave the run." },
  { n: "03", k: "ASSERT",
    h: "Behavioural assertions",
    p: "Claims about steps, order, grounding and refusals — not string matches." },
  { n: "04", k: "CERTIFY",
    h: "A signed artifact per release",
    p: "Attached to the commit, exportable for audit and procurement." },
];

/** Why the old approach stops working. The argument the hero rests on. */
export const GAP = {
  eyebrow: "The gap",
  title: "Software was verified because it was deterministic. Agents are not.",
  body: [
    "A prompt change, a model version, a reordered tool list — any of them "
      + "silently rewrites the path an agent takes. There is no diff to read.",
    "So the question stops being “does this input give that output?” and becomes "
      + "“did it still refuse, still check before writing, still escalate?”. "
      + "Those are claims about behaviour, and they need a run to test them.",
  ],
};

/** Sample figures from one recorded run — never a customer number. */
export const PRODUCT_STATS = [
  { fig: "18", lab: "Suites", sub: "across 4 agents" },
  { fig: "512", lab: "Recorded runs", sub: "evaluation fixtures" },
  { fig: "1,406", lab: "Assertions", sub: "behavioural, not textual" },
  { fig: "7", lab: "Blocked releases", sub: "this quarter", accent: true },
];

export const WHY_NOW = [
  { k: "Time",
    p: "Weeks of hand-testing prompts collapse into one evaluation run against "
       + "recorded traffic." },
  { k: "Cost",
    p: "Regressions are caught before release, not after the refunds and the "
       + "incident review." },
  { k: "Trust",
    p: "Each release ships with a signed record of behaviour that risk and "
       + "procurement can read." },
];
