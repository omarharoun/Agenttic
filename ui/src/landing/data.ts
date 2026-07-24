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
export const SAMPLE_METRICS: ScoreMetric[] = [
  { label: "Task success", value: "86", sub: "% ±4" },
  { label: "Consistency · pass^8", value: "41", sub: "%" },
  { label: "Mean cost", value: "$0.021" },
  { label: "p95 latency", value: "1.9", sub: "s" },
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
    { rowHeader: "Fit", cells: { us: "A rubric fitted to the agent's archetype, proven to discriminate", bench: "One test for all agents", eye: "Whatever you thought to check" } },
    { rowHeader: "Provenance", cells: { us: "Every score traces to a check, trace, and rationale", bench: "An opaque aggregate", eye: "A gut feeling" } },
    { rowHeader: "Reliability", cells: { us: "pass^k — consistency across k runs, not luck once", bench: "Usually single-run", eye: "Unmeasured" } },
    { rowHeader: "Contamination", cells: { us: "Private suites, per-tenant canaries", bench: "Public repos, likely trained on", eye: "—" } },
    { rowHeader: "On-device", cells: { us: "Yes; your model key, no telemetry", bench: "Varies", eye: "Yes" } },
    { rowHeader: "Scope", cells: { us: "States what was never exercised, and refuses to call that a pass", bench: "Silent about everything it didn't test", eye: "Unknown by definition" } },
    { rowHeader: "Proof", cells: { us: "Decides exhaustively where a question is decidable", bench: "Samples, always", eye: "—" } },
  ],
};

// ---- confidence: the three provenance kinds -------------------------------
export const CONFIDENCE = [
  { scorer: "code" as const, name: "no_unauthorized_writes",
    body: "A code check passed on the trace, at a step you can open. No model in the loop; the same input always gives the same result." },
  { scorer: "judge" as const, calibrated: true, alpha: 0.87, name: "tone",
    body: "An LLM judge scored it, and that judge agrees with human reviewers at a measured α against a known human ceiling." },
  { scorer: "judge" as const, calibrated: false, name: "policy_fidelity",
    body: "Scored by a judge not yet calibrated against humans on this criterion. Shown, flagged, never quietly counted as certain." },
];

// ---- what we cover that others don't (broad headlines only) --------------
export const COVERAGE_CLAIMS = [
  { h: "What was never exercised",
    p: "Every other tool reports what passed. We report the situations your agent was never once put in — and refuse to call that a pass." },
  { h: "Properties, watched throughout",
    p: "Not just the final answer. Behaviour is held to its rules across the whole run, including the runs that scored perfectly." },
  { h: "Proof where proof is possible",
    p: "Some questions about a system are decidable. For those we don't sample and hope — we decide, for every path, and say plainly where that stops applying." },
  { h: "The supply chain, not just the agent",
    p: "The tools, servers and memory your agent depends on are tested as subjects in their own right. An agent is only as trustworthy as what it calls." },
  { h: "A test built for your agent",
    p: "Not one fixed exam for everything. A suite fitted to what your agent actually does — and rejected outright unless it can tell a good agent from a bad one." },
  { h: "Evidence with an expiry date",
    p: "Signed, scoped, revocable, and bound to the exact version tested. It states what was measured and what was not. Agents drift; unbounded claims are a lie with a long fuse." },
];

// ---- trust ----------------------------------------------------------------
export const TRUST = [
  { h: "On-device", p: "The harness, checks, and trace capture run on your hardware. The scorecard is a file on your disk, not rows in a hosted index." },
  { h: "No telemetry", p: "No usage pings, no crash reports, no analytics. There's nothing to opt out of, because nothing is sent." },
  { h: "Evidence, not assertions", p: "Every number opens to the run behind it. You are never asked to take a score on faith — including ours." },
  { h: "Self-host the MCP server", p: "Serve evaluation over stdio on one machine, or over HTTP on your own infrastructure. We host nothing." },
];

// ---- faq ------------------------------------------------------------------
export const FAQ = [
  { q: "How is this different from a benchmark or a leaderboard?",
    a: "A benchmark hands every agent the same fixed test and returns one number, and says nothing at all about what it never tried. We build the test around your agent, measure how much of the space it actually reached, and lead with what is still unexercised. Your suites stay private, so they cannot be trained against." },
  { q: "What can you test that our current evaluation can't?",
    a: "The things a pass rate is structurally unable to express. Whether your agent was ever put in the situations that actually break it. Whether it held its properties on the runs that passed. Whether the tools and servers it depends on behave under pressure. And, for the parts of the system where the question is decidable, an answer that holds for every path rather than for the cases someone happened to write." },
  { q: "Does my agent or my data leave my machine?",
    a: "No. The harness, checks, and trace capture run locally. The only calls that leave your machine are the ones your judge and generator steps make to the model provider you configure — under your own keys — which you can point at a local model like Ollama to keep everything on-device. No telemetry, nothing uploaded to us." },
  { q: "How do you score subjective things like tone without it being arbitrary?",
    a: "Two ways. Anything checkable in code is a deterministic check. Anything qualitative is scored by an LLM judge calibrated against human reviewers — we measure the agreement (α) and show it, and until a judge is calibrated its scores are marked provisional, never counted as certain. A judge is never allowed to be more confident than the humans it's measured against." },
  { q: "What is pass^k, and why does it matter?",
    a: "pass^k is the probability an agent succeeds on a task across k independent tries, not just once. Agents that look deployable on a single run often fail when asked to do the same thing eight times. The gap between pass^1 and pass^8 is the flakiness number, and Agenttic names it in every report." },
  { q: "How do we get access?",
    a: "Agenttic is sold as an engagement, not a download. We scope the agent, stand the verification up against it, and hand back evidence your risk function can read. Start with a briefing." },
  { q: "Can you evaluate an agent we didn't build?",
    a: "Yes — that's the point of the black-box adapter. Wrap any agent behind an HTTP endpoint and Agenttic scores it on the criteria that don't require internal traces, and says so honestly in the report. It's how a buyer evaluates a vendor's agent before deploying it." },
];
