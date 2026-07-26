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
    { rowHeader: "Can you check it", cells: { us: "Every number opens to the exact moment in the run", bench: "One total, no way in", eye: "A gut feeling" } },
    { rowHeader: "Reliability", cells: { us: "Does it work all eight times, or just once", bench: "Usually run once", eye: "Never measured" } },
    { rowHeader: "Could it have seen the answers", cells: { us: "Your tests stay private, and we plant markers to detect leaks", bench: "Published online, probably in the training data", eye: "—" } },
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

// ---- what we cover that others don't (plain words) -----------------------
export const COVERAGE_CLAIMS = [
  { h: "What nobody ever tried",
    p: "Every other tool reports what passed. We report the situations your agent was never once put in — and we refuse to call that a pass." },
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
  { h: "Every number opens",
    p: "Each result links to the exact moment in the run behind it. You are never asked to take a score on faith — ours included." },
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
    a: "Because a demo is one run and production is thousands. We run the same task eight times and report how often it works every single time, not just once. Agents that look ready on a single run often fall apart on the eighth, and that gap is the number nobody shows you." },
  { q: "Why would you refuse to certify us?",
    a: "Because a certificate that anyone can get means nothing. If most of the situations your agent could face were never tried, or it broke one of its own safety rules, we will not sign it off — we will tell you exactly what is missing instead. Then you fix that list and we look again. That is the entire value of the thing." },
  { q: "Can you check an agent we didn't build?",
    a: "Yes. Point us at it over its normal interface and we test what can be tested from the outside, and say plainly in the report what we could not see. It is how you check a supplier's agent before you let it near your customers." },
];
