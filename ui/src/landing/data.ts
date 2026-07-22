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

// ---- how-it-works: assistant picker × command tabs ------------------------
export type TabKey = "install" | "eval" | "mcp";
export interface Assistant {
  id: string;
  name: string;
  cmds: Record<TabKey, { prompt?: string; text: string; comment?: string }[]>;
}

const INSTALL = [
  { prompt: "$", text: "uv tool install agenttic" },
  { prompt: "$", text: "agenttic install", comment: "# add the skill" },
];
const MCP = [
  { prompt: "$", text: "agenttic serve --stdio", comment: "# one machine" },
  { prompt: "$", text: "agenttic serve --http :8700", comment: "# for a team" },
];

export const ASSISTANTS: Assistant[] = [
  { id: "claude-code", name: "Claude Code", cmds: {
    install: INSTALL,
    eval: [{ prompt: ">", text: "/agenttic eval ./my-agent", comment: "# inside Claude Code" }],
    mcp: MCP } },
  { id: "cursor", name: "Cursor", cmds: {
    install: INSTALL,
    eval: [{ prompt: ">", text: "@agenttic eval ./my-agent", comment: "# in the Cursor chat" }],
    mcp: MCP } },
  { id: "copilot", name: "Copilot", cmds: {
    install: INSTALL,
    eval: [{ prompt: "$", text: "agenttic eval ./my-agent", comment: "# from the terminal" }],
    mcp: MCP } },
  { id: "codex", name: "Codex", cmds: {
    install: INSTALL,
    eval: [{ prompt: "$", text: "agenttic eval ./my-agent" }],
    mcp: MCP } },
  { id: "gemini-cli", name: "Gemini CLI", cmds: {
    install: INSTALL,
    eval: [{ prompt: "$", text: "agenttic eval ./my-agent" }],
    mcp: MCP } },
  { id: "aider", name: "Aider", cmds: {
    install: INSTALL,
    eval: [{ prompt: "$", text: "agenttic eval ./my-agent" }],
    mcp: MCP } },
  { id: "other", name: "Other", cmds: {
    install: INSTALL,
    eval: [{ prompt: "$", text: "agenttic eval ./my-agent", comment: "# any shell" }],
    mcp: MCP } },
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
    { rowHeader: "License", cells: { us: "MIT core", bench: "Mixed", eye: "—" } },
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

// ---- toolkit --------------------------------------------------------------
export const TOOLKIT = [
  { code: "scorecard.json", h: "The data", p: "The whole evaluation as data — every case, span, score, and its provenance." },
  { code: "/agenttic eval", h: "A skill, not an app", p: "Installs into your assistant — Claude Code, Cursor, Copilot, Codex, Gemini CLI, Aider." },
  { code: "pass^k", h: "Reliability, not luck", p: "Run each case k times; see how often the agent succeeds consistently, not just once." },
  { code: "agenttic serve", h: "MCP server", p: "Exposes evaluation as a tool your assistant can call — stdio on one machine, HTTP for a team." },
  { code: "on-device", h: "Local by default", p: "The harness runs on your hardware. Bring your own model, or run Ollama." },
  { code: "ethos", h: "Values overlay", p: "An optional criteria group for integrity, restraint, and knowing when to defer — scored like any other." },
];

// ---- trust ----------------------------------------------------------------
export const TRUST = [
  { h: "On-device", p: "The harness, checks, and trace capture run on your hardware. The scorecard is a file on your disk, not rows in a hosted index." },
  { h: "No telemetry", p: "No usage pings, no crash reports, no analytics. There's nothing to opt out of, because nothing is sent." },
  { h: "MIT, auditable", p: "The entire core is MIT-licensed on GitHub. You don't have to trust this page; read what the code does." },
  { h: "Self-host the MCP server", p: "Serve evaluation over stdio on one machine, or over HTTP on your own infrastructure. We host nothing." },
];

// ---- faq ------------------------------------------------------------------
export const FAQ = [
  { q: "How is Agenttic different from a benchmark or a leaderboard?",
    a: "A public benchmark gives every agent the same fixed test and one number. Agenttic classifies your agent into an archetype, fits a rubric to what it actually does, and refuses to ship that rubric until it proves the rubric separates good agents from bad. Every score is traceable to a check, a trace, and a rationale — and it's private, so it can't be trained on the way public benchmarks are." },
  { q: "Is Agenttic free?",
    a: "Yes. The core is open source under the MIT license: the harness, the deterministic checks, the LLM-judge mechanism (bring your own model key), the reports, and the MCP server. No account, no card. A separate early-access layer — the rubric engine, the improvement loop, calibration at scale, and neutral certification — is Agenttic Assurance, for teams." },
  { q: "Does my agent or my data leave my machine?",
    a: "No. The harness, checks, and trace capture run locally. The only calls that leave your machine are the ones your judge and generator steps make to the model provider you configure — under your own keys — which you can point at a local model like Ollama to keep everything on-device. No telemetry, nothing uploaded to us." },
  { q: "How do you score subjective things like tone without it being arbitrary?",
    a: "Two ways. Anything checkable in code is a deterministic check. Anything qualitative is scored by an LLM judge calibrated against human reviewers — we measure the agreement (α) and show it, and until a judge is calibrated its scores are marked provisional, never counted as certain. A judge is never allowed to be more confident than the humans it's measured against." },
  { q: "What is pass^k, and why does it matter?",
    a: "pass^k is the probability an agent succeeds on a task across k independent tries, not just once. Agents that look deployable on a single run often fail when asked to do the same thing eight times. The gap between pass^1 and pass^8 is the flakiness number, and Agenttic names it in every report." },
  { q: "Can Agenttic evaluate an agent I didn't build?",
    a: "Yes — that's the point of the black-box adapter. Wrap any agent behind an HTTP endpoint and Agenttic scores it on the criteria that don't require internal traces, and says so honestly in the report. It's how a buyer evaluates a vendor's agent before deploying it." },
];
