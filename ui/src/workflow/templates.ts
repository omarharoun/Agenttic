import type { WorkflowDoc, WorkflowNode } from "../api";

/* The fixed benchmarking pipeline, expressed as guided steps. Each step maps to
 * a backend node type (src/ascore/server/nodes.py). Node IDs are stable and
 * equal the step id, so the guided view can find a step's node by id and the
 * wiring below is deterministic. The scoring step keeps id "score" but its
 * node type flips between "score" (LLM judge) and "fi_eval" (Future AGI) — both
 * expose identical ports ({run} → {scored}), so the edges never change. */

export const TRIAGE_PROMPT =
  "You are the ticket-triage step of a support workflow. You receive a JSON " +
  'object with a "ticket" field. Classify it into exactly one queue: billing ' +
  "(payments, charges, refunds, invoices), technical (crashes, errors, bugs, " +
  "login/password issues), or general (everything else). Consult the knowledge " +
  "base routing_rules with lookup_kb when a ticket is ambiguous. Your FINAL " +
  "message must be ONLY the queue name — one lowercase word: billing, " +
  "technical, or general.";

export interface StepDef {
  id: string;        // stable node_id
  ntype: string;     // backend node type (scoring step may flip to fi_eval)
  num: number;       // 1-based position in the full pipeline
  icon: string;
  title: string;
  blurb: string;
  cta?: string;      // emphasised prompt shown when the step is empty
  optional?: boolean;
}

export const STEPS: StepDef[] = [
  { id: "business_doc", ntype: "business_doc", num: 1, icon: "✎",
    title: "Business requirement",
    blurb: "Describe the workflow or task you want to benchmark — paste the requirements or upload a brief.",
    cta: "Add a business requirement" },
  { id: "generator", ntype: "generator", num: 2, icon: "⚗",
    title: "Benchmark generator",
    blurb: "Turn the requirement into a draft test suite: tasks, criteria and cases." },
  { id: "human_gate", ntype: "human_gate", num: 3, icon: "⊘",
    title: "Human gate",
    blurb: "Review the generated suite and approve it before anything runs." },
  { id: "agent", ntype: "agent", num: 4, icon: "◈",
    title: "Agent under test",
    blurb: "The agent being evaluated — a reference prompt, a black-box HTTP endpoint, or a managed agent." },
  { id: "run_suite", ntype: "run_suite", num: 5, icon: "▶",
    title: "Run suite",
    blurb: "Execute every case against the agent and capture full traces." },
  { id: "score", ntype: "score", num: 6, icon: "≋",
    title: "Score",
    blurb: "Grade every run — deterministic checks plus a tiered LLM judge per criterion." },
  { id: "scorecard", ntype: "scorecard", num: 7, icon: "▦",
    title: "Scorecard",
    blurb: "Aggregate the run scores into an immutable, shareable scorecard." },
  { id: "report", ntype: "report", num: 8, icon: "❏",
    title: "Report",
    blurb: "Produce a client-ready Markdown report with the regression diff." },
  { id: "monitor", ntype: "monitor", num: 9, icon: "◉", optional: true,
    title: "Live monitor",
    blurb: "Watch live traffic for drift against this batch baseline." },
];

export const stepById = (id: string) => STEPS.find((s) => s.id === id)!;

/** Every possible link in the pipeline: [source, sourcePort, target, targetPort].
 *  Edges are emitted only when both endpoints are present in the workflow. */
const WIRES: [string, string, string, string][] = [
  ["business_doc", "doc", "generator", "doc"],
  ["generator", "suite", "human_gate", "suite"],
  ["human_gate", "suite", "run_suite", "suite"],
  ["agent", "agent", "run_suite", "agent"],
  ["run_suite", "run", "score", "run"],
  ["score", "scored", "scorecard", "scored"],
  ["scorecard", "scorecard", "report", "scorecard"],
  ["scorecard", "scorecard", "monitor", "scorecard"],
];

export function buildEdges(presentIds: Set<string>) {
  return WIRES
    .filter(([s, , t]) => presentIds.has(s) && presentIds.has(t))
    .map(([source, source_port, target, target_port], i) => ({
      edge_id: `e${i}`, source, source_port, target, target_port,
    }));
}

function positionFor(step: StepDef) {
  // a readable left-to-right layout for the advanced/canvas + replay views
  return { x: 40 + step.num * 210, y: step.id === "agent" ? 330 : 150 };
}

export interface Template {
  key: string;
  name: string;
  tagline: string;
  icon: string;
  stepIds: string[];                          // which steps this template includes
  configs: Record<string, Record<string, any>>;
}

export const TEMPLATES: Template[] = [
  {
    key: "benchmark",
    name: "Benchmark an existing agent",
    tagline: "You already have a suite. Point an agent at it and get a scorecard.",
    icon: "◈",
    stepIds: ["agent", "run_suite", "score", "scorecard", "report"],
    configs: {
      agent: { variant: "reference", agent_id: "agent-under-test", system_prompt: TRIAGE_PROMPT },
      run_suite: { suite_id: "pilot-support-triage" },
      score: { pass_threshold: 0.7 },
    },
  },
  {
    key: "full",
    name: "Generate a benchmark from a requirement",
    tagline: "Start from a business doc. Generate a suite, approve it, then score.",
    icon: "⚗",
    stepIds: ["business_doc", "generator", "human_gate", "agent", "run_suite", "score", "scorecard", "report"],
    configs: {
      business_doc: { text: "" },
      generator: { suite_id: "generated-suite", cases_per_task: 5 },
      agent: { variant: "reference", agent_id: "workflow-under-test", system_prompt: TRIAGE_PROMPT },
      score: { pass_threshold: 0.7 },
    },
  },
];

/** Materialise a template into a backend workflow document. */
export function buildDoc(t: Template, workflowId: string, name: string): WorkflowDoc {
  const ids = new Set(t.stepIds);
  const nodes: WorkflowNode[] = t.stepIds.map((id) => {
    const step = stepById(id);
    return {
      node_id: id, type: step.ntype, label: "",
      position: positionFor(step), config: t.configs[id] ?? {},
    };
  });
  return { workflow_id: workflowId, name, nodes, edges: buildEdges(ids) };
}

/** Re-derive node positions + edges after a structural change (add/remove a
 *  step, flip the scoring engine). Preserves each node's config + type. */
export function rewire(nodes: WorkflowNode[]): { nodes: WorkflowNode[]; edges: any[] } {
  const ids = new Set(nodes.map((n) => n.node_id));
  const placed = nodes.map((n) => {
    const step = STEPS.find((s) => s.id === n.node_id);
    return step ? { ...n, position: positionFor(step) } : n;
  });
  return { nodes: placed, edges: buildEdges(ids) };
}
