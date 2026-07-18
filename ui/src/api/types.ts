/* ============================================================================
 * SPEC-4 Step 17.2 — the typed API layer.
 *
 * Every response the console consumes is described here, field-for-field from
 * the server's Pydantic schemas (the source of truth):
 *
 *   - Scorecard / RunScore / CriterionScore  ← schema/scorecard.py
 *   - Trace / Span / SpanKind                ← schema/trace.py (0.3.0)
 *   - TestCase / TestSuite                   ← schema/testcase.py
 *   - Rubric / Criterion                     ← schema/rubric.py
 *   - HumanFeedback                          ← schema/feedback.py
 *   - JudgeConfig / JudgeOptimizationRequest ← schema/judge_config.py, judge_request.py
 *   - AgentConfigRow / JudgeConfigRow (lineage) ← registry/sqlite_store.py
 *
 * plus the composite/derived response shapes the server routes assemble on top
 * of these (executions list/detail/results/issues, scorecard report rows,
 * leaderboard rows, calibration/standard metrics, hardening, A/B, optimize,
 * training-camp). Pydantic `Literal[...]` maps to a TS union; a Python
 * `X | None` field maps to `X | null`; an optional-with-default container maps
 * to a required field (the server always serializes it).
 *
 * Values that are genuinely dynamic (a test case's `input`/`expected`, a span's
 * `attributes`, a config `payload`) are typed `JsonValue`/`JsonObject` rather
 * than `any`, so callers must narrow before use.
 * ==========================================================================*/

/** A JSON value — the honest type for a genuinely-dynamic server field. */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

/* ---------------------------------------------------------------------------
 * Core schemas — mirror the Pydantic models field-for-field.
 * ------------------------------------------------------------------------- */

/** schema/scorecard.py :: CriterionScore */
export interface CriterionScore {
  criterion_id: string;
  score: number; // binary {0,1} | three_point {0,0.5,1}
  scorer: "code" | "judge" | "fi";
  calibrated: boolean;
  judge_rationale: string | null;
  cost_usd: number;
}

/** schema/scorecard.py :: RunScore */
export interface RunScore {
  trace_id: string;
  test_id: string;
  criterion_scores: CriterionScore[];
  passed: boolean;
  cost_usd: number;
  scoring_cost_usd: number;
  latency_ms: number;
  steps: number;
  scoring_error: string | null;
}

/** schema/scorecard.py :: Scorecard (computed fields n_scored/… always serialized) */
export interface Scorecard {
  scorecard_id: string;
  agent_id: string;
  suite_id: string;
  suite_version: number;
  rubric_id: string;
  rubric_version: number;
  run_scores: RunScore[];
  task_success_rate: number;
  mean_cost_usd: number;
  total_cost_usd: number;
  total_scoring_cost_usd: number;
  p95_latency_ms: number;
  per_criterion_means: Record<string, number>;
  errored_test_ids: string[];
  visibility_tier: VisibilityTier;
  created_at: string;
  // computed_field — travel with every serialized scorecard
  n_scored: number;
  n_passed: number;
  success_wilson_low: number;
  success_wilson_high: number;
}

export type VisibilityTier = "glass_box" | "black_box";

/** schema/trace.py :: SpanKind (0.3.0 — includes escalation) */
export type SpanKind =
  | "llm_call"
  | "tool_call"
  | "retrieval"
  | "agent_decision"
  | "error"
  | "final_output"
  | "escalation";

/** schema/trace.py :: Span */
export interface Span {
  span_id: string;
  parent_id: string | null;
  kind: SpanKind;
  name: string;
  start_time: string;
  end_time: string;
  input: JsonObject;
  output: JsonObject;
  error: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  attributes: JsonObject;
}

/** schema/trace.py :: Trace */
export interface Trace {
  trace_id: string;
  agent_id: string;
  agent_config_hash: string;
  test_case_id: string | null;
  spans: Span[];
  visibility: VisibilityTier;
  final_output: string;
  total_cost_usd: number;
  total_latency_ms: number;
  total_steps: number;
  source: string;
  escalated: boolean;
  schema_version: string;
}

/** schema/testcase.py :: TestCase */
export interface TestCase {
  test_id: string;
  suite_id: string;
  version: number;
  task_description: string;
  input: JsonObject;
  expected: JsonObject | null;
  tags: string[];
  rubric_id: string;
}

/** schema/testcase.py :: TestSuite */
export interface TestSuite {
  suite_id: string;
  version: number;
  business_context: string;
  test_ids: string[];
  approved: boolean;
  dataset_provenance: string | null;
}

/** schema/rubric.py :: Criterion */
export interface Criterion {
  criterion_id: string;
  description: string;
  scorer: "code" | "judge" | "fi";
  scale: "binary" | "three_point";
  check_ref: string | null;
  fi_metric: string | null;
  anchors: JsonObject;
  tags: string[];
}

/** schema/rubric.py :: Rubric */
export interface Rubric {
  rubric_id: string;
  version: number;
  criteria: Criterion[];
  weights: Record<string, number>;
}

/** schema/feedback.py :: HumanFeedback (SPEC-2) */
export type FeedbackSource = "reviewer" | "end_user" | "escalation";
export type FeedbackKind =
  | "approval"
  | "correction"
  | "rating"
  | "escalation_decision";
export interface HumanFeedback {
  feedback_id: string;
  trace_id: string;
  agent_id: string;
  source: FeedbackSource;
  kind: FeedbackKind;
  criterion_id: string | null;
  rating: number | null;
  corrected_output: string | null;
  rationale: string;
  created_at: string;
}

/** schema/judge_config.py :: JudgeConfig (SPEC-3) */
export type JudgeConfigStatus = "candidate" | "active" | "rejected" | "retired";
export interface JudgeConfig {
  judge_config_id: string;
  version: number;
  criterion_id: string;
  system_prompt: string;
  instruction_template: string;
  few_shot_examples: JsonObject[];
  parent_id: string | null;
  changelog: string;
  status: JudgeConfigStatus;
  created_at: string;
}

/** schema/judge_request.py :: JudgeOptimizationRequest (SPEC-3) */
export type JudgeOptimizationRequestStatus = "open" | "cleared";
export interface JudgeOptimizationRequest {
  request_id: string;
  criterion_id: string;
  suite_id: string;
  reason: string;
  status: JudgeOptimizationRequestStatus;
  created_at: string;
  cleared_at: string | null;
}

/** registry/sqlite_store.py :: AgentConfigRow — the learning promotion ledger
 *  (SPEC-2 Step 14). `payload`/`scorecard_ids` are JSON-encoded strings. */
export type AgentConfigStatus = "promoted" | "rejected" | "pending_approval";
export interface AgentConfigRow {
  id: number | null;
  tenant_id: string;
  agent_id: string;
  agent_config_hash: string;
  parent_hash: string;
  diff_summary: string;
  scorecard_ids: string; // JSON list of scorecard ids
  status: AgentConfigStatus;
  reason: string;
  approved_by: string;
  created_at: string;
  payload: string; // config/changelog JSON
}

/** registry/sqlite_store.py :: JudgeConfigRow — judge lineage row (SPEC-3). */
export interface JudgeConfigRow {
  id: number | null;
  tenant_id: string;
  judge_config_id: string;
  criterion_id: string;
  version: number;
  status: JudgeConfigStatus;
  created_at: string;
  payload: string;
}

/* ---------------------------------------------------------------------------
 * Auth / session / settings.
 * ------------------------------------------------------------------------- */

export interface Me {
  role: string;
  tenant: string;
  email: string | null;
  auth_method: string;
}

/** Generic {ok:true, …} acknowledgement returned by many mutating endpoints. */
export interface AuthResult {
  ok?: boolean;
  status?: string;
  role?: string;
  tenant?: string;
  email?: string | null;
  detail?: string;
  message?: string;
  verified?: boolean;
  [key: string]: unknown;
}

export interface AnthropicKeyStatus {
  set: boolean;
  masked: string | null;
  updated_at: string | null;
}
export interface KeyTestResult {
  valid: boolean;
  error: string | null;
}
export interface ApiToken {
  id: number;
  name: string;
  masked: string;
  created_at: string;
  last_used_at: string | null;
}
export interface TokenList {
  tokens: ApiToken[];
}
export interface CreatedToken {
  id: number;
  name: string;
  token: string; // shown once
  masked: string;
  created_at: string;
}

/* ---------------------------------------------------------------------------
 * Workflows / node graph / executions.
 * ------------------------------------------------------------------------- */

export interface NodeTypeSpec {
  type: string;
  title: string;
  category: string;
  description: string;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  config_schema: {
    properties?: Record<string, JsonObject>;
    required?: string[];
  };
}

export interface WorkflowNode {
  node_id: string;
  type: string;
  label: string;
  position: { x: number; y: number };
  config: JsonObject;
  retries?: number;
  continue_on_error?: boolean;
}

export interface WorkflowEdge {
  edge_id: string;
  source: string;
  source_port: string;
  target: string;
  target_port: string;
}

export interface WorkflowDoc {
  workflow_id: string;
  name: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

/** A row in GET /api/workflows (server/store.py list_workflows). */
export interface WorkflowSummary {
  workflow_id: string;
  name: string;
  n_nodes?: number;
  node_count?: number;
  updated_at?: string;
  [key: string]: unknown;
}

export interface WorkflowLoad {
  workflow: WorkflowDoc;
  problems: string[];
}
export interface WorkflowSaveResult {
  workflow_id: string;
  problems: string[];
  saved: boolean;
}

/** An execution lifecycle row / detail (store.get_execution / list). Node
 *  output payloads are dynamic. */
export type ExecutionStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "succeeded"
  | "failed"
  | "cancelled";
export interface Execution {
  execution_id: string;
  workflow_id: string;
  status: ExecutionStatus | string;
  created_at?: string;
  updated_at?: string;
  started_at?: string;
  finished_at?: string;
  node_outputs?: Record<string, Record<string, JsonValue>>;
  node_states?: Record<string, string>;
  error?: string | null;
  error_reason?: string | null;
  [key: string]: unknown;
}

/** One scorecard summary row inside execution results (routes/executions.py
 *  _assemble_results). */
export interface ExecutionScorecardSummary {
  node_id: string;
  scorecard_id: string;
  agent_id: string;
  suite_id: string;
  suite_version: number;
  task_success_rate: number;
  n_scored: number;
  n_passed: number;
  success_wilson_low: number;
  success_wilson_high: number;
  mean_cost_usd: number;
  total_cost_usd: number;
  total_scoring_cost_usd: number;
  cached: boolean;
  p95_latency_ms: number;
  per_criterion_means: Record<string, number>;
  errored_test_ids: string[];
  visibility_tier: VisibilityTier;
}

/** One per-case row inside execution results. */
export interface ExecutionCaseRow {
  node_id: string;
  test_id: string;
  passed: boolean;
  scoring_error: string | null;
  prediction: string;
  expected: JsonValue | null;
  cost_usd: number | null;
  scoring_cost_usd: number | null;
  steps: number | null;
  latency_ms: number | null;
  criteria: {
    criterion_id: string;
    score: number;
    scorer: string;
    calibrated: boolean;
    rationale: string | null;
  }[];
}

export interface ExecutionResults {
  status: string;
  scorecards: ExecutionScorecardSummary[];
  cases: ExecutionCaseRow[];
}

export interface StartExecutionResult {
  execution_id: string;
}
export interface ApproveExecutionResult {
  approved: { suite_id: string; version: number };
}
export interface CancelExecutionResult {
  cancelled: string;
}

/** The projected-cost body (cost.py :: CostEstimate.model_dump()). */
export interface EstimateBody {
  n_cases?: number;
  agent_variant?: string;
  agent_model?: string | null;
  agent_calls_per_case?: number;
  n_judge_criteria?: number;
  judge_model?: string | null;
  projected_agent_usd?: number;
  projected_judge_usd?: number;
  projected_usd?: number;
  assumptions?: JsonObject;
  notes?: string[];
  [key: string]: unknown;
}

/** The pre-run budget context (budget.py :: budget_context). */
export interface BudgetContext {
  tenant?: string;
  max_run_cost_usd?: number;
  max_daily_cost_usd?: number;
  quota_daily_usd?: number | null;
  quota_monthly_usd?: number | null;
  spent_today_usd?: number;
  spent_month_usd?: number;
  projected_usd?: number;
  would_exceed_run?: boolean;
  would_exceed_daily?: boolean;
  would_exceed_quota_daily?: boolean;
  would_exceed_quota_monthly?: boolean;
  warn_only?: boolean;
  [key: string]: unknown;
}

/** GET /api/workflows/{id}/estimate and GET /api/estimate — the projected cost
 *  plus the budget context the UI shows before a run (routes/cost.py). */
export interface CostEstimate {
  estimate?: EstimateBody;
  budget?: BudgetContext;
  [key: string]: unknown;
}


/* ---------------------------------------------------------------------------
 * Issues report (GET /executions/{id}/issues).
 * ------------------------------------------------------------------------- */

export type IssueSeverity = "critical" | "high" | "medium" | "low";

export interface Issue {
  id: string;
  title: string;
  criterion_id: string | null;
  category: string;
  category_label: string;
  severity: IssueSeverity;
  impact_rank: number;
  why: string;
  affected_n: number;
  n_measured: number;
  affected_share: number | null;
  evidence: {
    counts: Record<string, number>;
    cases: {
      test_id?: string;
      score?: number;
      scorer?: string;
      calibrated?: boolean;
      rationale?: string | null;
      prediction?: string;
      expected?: string;
    }[];
    criteria?: {
      criterion_id: string;
      description?: string;
      provisional: number;
    }[];
    truncated: number;
  };
  suggested_fix: {
    capability: string;
    label: string;
    route: string;
    blurb: string;
  };
  status: string;
}

export interface IssuesReport {
  status: string;
  issues: Issue[];
  summary: {
    total_issues: number;
    by_severity: Record<IssueSeverity, number>;
    n_scored: number;
    n_passed: number;
    n_errored: number;
    pass_rate: number | null;
    pass_wilson_low: number | null;
    pass_wilson_high: number | null;
    headline: string;
    clean: boolean;
  };
}

/* ---------------------------------------------------------------------------
 * Suites / agents / scorecards catalog.
 * ------------------------------------------------------------------------- */

export interface SuiteSummary {
  suite_id: string;
  version: number;
  business_context?: string;
  approved?: boolean;
  n_cases?: number;
  dataset_provenance?: string | null;
  [key: string]: unknown;
}

/** A row in the agents view (GET /api/agents) — declared + measured info. */
export interface AgentRow {
  agent_id: string;
  variant?: string;
  name?: string;
  visibility?: VisibilityTier | string;
  n_scorecards?: number;
  latest_score?: number | null;
  declared?: boolean;
  managed_agent_id?: string;
  sources?: string[];
  n_traces?: number;
  suites?: string[];
  last_seen?: string | null;
  [key: string]: unknown;
}

/** GET /api/agents — the console reads `.agents` (plus other rollup keys). */
export interface AgentsView {
  agents: AgentRow[];
  warning?: string;
  [key: string]: unknown;
}

/** A catalog (declared) agent (GET /api/agents/catalog). */
export interface CatalogAgent {
  agent_id: string;
  variant?: string;
  name?: string;
  endpoint_url?: string;
  retired?: boolean;
  description?: string;
  version?: number;
  url?: string;
  managed_agent_id?: string;
  model?: string;
  [key: string]: unknown;
}
export interface CatalogList {
  agents: CatalogAgent[];
}

/** A scorecard summary row (GET /api/scorecards). */
export interface ScorecardSummary {
  scorecard_id: string;
  agent_id: string;
  suite_id: string;
  suite_version?: number;
  task_success_rate?: number | null;
  n_scored?: number;
  n_passed?: number;
  n_runs?: number;
  n_errored?: number;
  success_wilson_low?: number;
  success_wilson_high?: number;
  mean_cost_usd?: number | null;
  total_cost_usd?: number | null;
  total_scoring_cost_usd?: number | null;
  p95_latency_ms?: number | null;
  visibility_tier?: VisibilityTier;
  cached?: boolean;
  created_at?: string;
  [key: string]: unknown;
}

/* ---------------------------------------------------------------------------
 * Leaderboard / standard benchmarking / calibration.
 * ------------------------------------------------------------------------- */

export interface CertificationBadge {
  tier: string;
  attestation: string;
  status: string;
  dossier_id: string;
}

/** A leaderboard row (leaderboard.compute_leaderboard agents[]). */
export interface LeaderboardRow {
  agent_id: string;
  variant?: string;
  rank?: number;
  index_score?: number | null;
  composite?: number | null;
  task_success_rate?: number | null;
  mean_cost_usd?: number | null;
  p95_latency_ms?: number | null;
  n_suites?: number;
  n_scored?: number;
  certification?: CertificationBadge | null;
  per_suite?: Record<string, unknown>;
  // -- leaderboard.compute_leaderboard row fields (routes/leaderboard) --
  index?: number;
  agent_type?: string;
  coverage?: number;
  total_suites?: number;
  all_in_cost_per_case_usd?: number | null;
  visibility_tier?: string;
  n_errored?: number;
  n?: number;
  n_cases?: number;
  [key: string]: unknown;
}

/** A standard-benchmark leaderboard row (ops.standard_index_op) — the canonical
 *  Agenttic Index with its per-metric components + which suites fed it. */
export interface StandardLeaderboardRow {
  agent_id: string;
  index: number;
  components?: Record<string, number | null>;
  suites_run?: string[];
  n_cases?: number | null;
  rounds?: number;
  [key: string]: unknown;
}

export interface Leaderboard {
  agents: LeaderboardRow[];
  weights?: Record<string, number>;
  suites?: string[];
  [key: string]: unknown;
}

/** A dataset descriptor (rows of GET /api/standard/datasets `.datasets`). */
export interface StandardDataset {
  dataset_id: string;
  suite_id?: string;
  name?: string;
  citation?: string;
  license?: string;
  source_url?: string;
  gated?: boolean;
  requires_execution_harness?: boolean;
  caveat?: string;
  present?: boolean;
  [key: string]: unknown;
}
export interface StandardDatasets {
  datasets: StandardDataset[];
}

/** GET /api/standard/metrics — coverage + rollup. */
export interface StandardMetrics {
  [key: string]: unknown;
}

/** GET /api/standard/leaderboard. */
export interface StandardLeaderboard {
  agents?: StandardLeaderboardRow[];
  note?: string;
  [key: string]: unknown;
}

/* ---------------------------------------------------------------------------
 * Safety scan ("Scan my agent").
 * ------------------------------------------------------------------------- */

export interface ScanCheck {
  criterion_id: string;
  label: string;
  status: "pending" | "pass" | "warn" | "fail";
  passed: boolean | null;
  detail: string;
  percent?: number;
  critical: boolean;
}

export interface ScanResult {
  scorecard_id: string;
  agent_id: string;
  grade: string;
  composite_score: number;
  grade_capped: boolean;
  cap_reason: string;
  dimensions: ScanCheck[];
  missing_required: string[];
  n_cases: number;
  errored: number;
  cost_usd: number;
}

/** A saved/issued certificate blob attached to a scan job. */
export interface Certificate {
  certificate_id?: string;
  cert_id?: string;
  agent_name?: string;
  grade?: string;
  issued_at?: string;
  [key: string]: unknown;
}

export interface ScanJob {
  scan_id: string;
  target: string;
  agent_name: string;
  status: "running" | "done" | "error";
  phase: string;
  progress: number;
  n_cases: number;
  cases_done: number;
  checks: ScanCheck[];
  result: ScanResult | null;
  certificate: Certificate | null;
  cert_note: string | null;
  error: string | null;
}

export interface ScanPreview {
  dimensions: { criterion_id: string; label: string; critical: boolean }[];
  endpoint: { needs_key: boolean; note: string };
  demo: { needs_key: boolean; key_set: boolean; note: string };
}

export interface ScanFinding {
  test_id: string;
  criterion_id: string | null;
  category: string;
  description: string;
  probe_input: string;
  injected_content: string;
  agent_output: string;
  tool_calls: { name: string; input: string }[];
  passed: boolean | null;
  verdict: "passed" | "refused" | "gap" | "error";
  detail: string;
  tags: string[];
  source: string;
  scoring: string;
}

export interface ScanFindingsDoc {
  scan_id: string;
  agent_name: string;
  target: string;
  status: string;
  available: boolean;
  note?: string;
  scorecard_id?: string;
  suite_id?: string;
  agent_id?: string;
  agent_config_hash?: string | null;
  visibility?: string;
  n_probes?: number;
  n_gaps?: number;
  n_passed?: number;
  n_errored?: number;
  findings: ScanFinding[];
}

export interface StartScanResult {
  scan_id: string;
  target: string;
  n_dimensions: number;
}
export interface PublicDemoPreview {
  available: boolean;
  dimensions: ScanPreview["dimensions"];
}

/* ---------------------------------------------------------------------------
 * "Connect your agent".
 * ------------------------------------------------------------------------- */

export interface ConnectionStatus {
  connected: boolean;
  agent_name?: string;
  endpoint_url?: string;
  preset?: "openai" | "generic" | "custom";
  request_field?: string;
  response_path?: string;
  model?: string;
  auth_header_name?: string;
  auth_set?: boolean;
  auth_masked?: string;
  consent?: boolean;
  consent_at?: string | null;
  updated_at?: string | null;
}

export interface ConnectionInput {
  endpoint_url: string;
  agent_name?: string;
  preset?: "openai" | "generic" | "custom";
  request_field?: string;
  response_path?: string;
  model?: string;
  auth_header_name?: string;
  auth_header_value?: string;
  consent?: boolean;
}

export interface ConnectionTestResult {
  ok: boolean;
  reply: string;
  error: string | null;
  mapping: {
    preset: string;
    request_field: string;
    response_path: string;
    model: string;
  };
}

/* ---------------------------------------------------------------------------
 * Certifications (public + authed).
 * ------------------------------------------------------------------------- */

/** An authed certification record (GET /api/certifications rows /
 *  POST /api/certifications result). Distinct from the *public* render shape
 *  `Certification` in ../cert.ts — this one is the operator-side ledger view,
 *  with many optional fields the console reads defensively (`grade`, `id`,
 *  `cert_id` via certIdOf, tier/attestation). */
export interface CertificationRecord {
  id?: string;
  certification_id?: string;
  cert_id?: string;
  dossier_id?: string;
  agent_id?: string;
  agent_name?: string;
  scorecard_id?: string;
  grade?: string | null;
  tier?: string;
  attestation?: string;
  status?: string;
  composite_score?: number | null;
  issued_at?: string;
  expires_at?: string | null;
  revoked?: boolean;
  revoked_at?: string | null;
  superseded?: boolean;
  config_hash?: string;
  [key: string]: unknown;
}
export interface CertificationList {
  certifications: CertificationRecord[];
  [key: string]: unknown;
}
/** GET /api/public/assistant/certification — real grade + cert id, or null grade. */
export interface AssistantCertification {
  grade: string | null;
  certification_id?: string | null;
  cert_id?: string | null;
  status?: string;
  gradeable?: boolean;
  agent_id?: string;
  [key: string]: unknown;
}

/* ---------------------------------------------------------------------------
 * Copilot status + assistant sessions.
 * ------------------------------------------------------------------------- */

export interface CopilotStatus {
  available: boolean;
  model: string;
}

/** An assistant chat session (normalized in assistant.ts). */
export interface AssistantSession {
  session_id: string;
  status?: string;
  messages?: JsonValue[];
  [key: string]: unknown;
}

/* ---------------------------------------------------------------------------
 * A/B comparison.
 * ------------------------------------------------------------------------- */

export interface StartAbResult {
  comparison_id: string;
}

/** A row in GET /api/ab/runs (sqlite_store.list_ab_runs). */
export interface AbRunSummary {
  comparison_id: string;
  status?: string;
  suite_id?: string;
  error?: string | null;
  created_at?: string;
  label_a?: string | null;
  label_b?: string | null;
  winner?: string | null;
  verdict?: string | null;
  success_rate_a?: number | null;
  success_rate_b?: number | null;
  n_paired?: number;
  [key: string]: unknown;
}

/** One flipped case in an A/B comparison (schema/ab.py :: FlippedCase). */
export interface AbFlippedCase {
  test_id: string;
  a_passed?: boolean;
  b_passed?: boolean;
  direction?: string;   // "gain" | "loss"
  [key: string]: unknown;
}

/** One per-criterion delta row (schema/ab.py :: CriterionComparison). */
export interface AbCriterionDelta {
  criterion_id: string;
  mean_a?: number | null;
  mean_b?: number | null;
  delta?: number | null;
  direction?: string;   // "A" | "B" | "tie"
  p_value?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  significant?: boolean;
  n?: number;
  [key: string]: unknown;
}

/** McNemar paired-test result (agenttic.stats.McNemarResult.to_dict()). */
export interface AbMcNemar {
  b?: number;
  c?: number;
  p_value?: number | null;
  significant?: boolean;
  underpowered?: boolean;
  test?: string;
  [key: string]: unknown;
}

/** One variant's agent-under-test config (schema/ab.py :: ABVariant). */
export interface AbVariant {
  label?: string;
  variant?: string;
  agent_id?: string;
  model?: string;
  system_prompt?: string;
  url?: string;
  managed_agent_id?: string;
  environment_id?: string;
  [key: string]: unknown;
}

/** The paired-comparison artifact (schema/ab.py :: ABComparison). */
export interface AbComparison {
  comparison_id?: string;
  suite_id?: string;
  suite_version?: number;
  rubric_id?: string;
  rubric_version?: number;
  label_a?: string;
  label_b?: string;
  variant_a?: AbVariant;
  variant_b?: AbVariant;
  scorecard_a_id?: string;
  scorecard_b_id?: string;
  n_paired?: number;
  excluded_test_ids?: string[];
  success_rate_a?: number | null;
  success_rate_b?: number | null;
  success_delta?: number | null;
  mcnemar?: AbMcNemar;
  per_criterion?: AbCriterionDelta[];
  flipped_cases?: AbFlippedCase[];
  mean_cost_a?: number | null;
  mean_cost_b?: number | null;
  total_cost_a?: number | null;
  total_cost_b?: number | null;
  p95_latency_a?: number | null;
  p95_latency_b?: number | null;
  winner?: string | null;   // "A" | "B" | "tie"
  verdict?: string;
  created_at?: string;
  [key: string]: unknown;
}

/** Live per-variant progress while an A/B run executes (ab_manager.progress). */
export interface AbProgress {
  variant?: string;
  event?: string;
  done?: number | null;
  total?: number | null;
  message?: string;
  [key: string]: unknown;
}

/** GET /api/ab/runs/{id} — run status + the comparison artifact (null while
 *  running) + live progress (sqlite_store.get_ab_run + ab_manager.progress). */
export interface AbRunDetail {
  comparison_id: string;
  suite_id?: string;
  status?: string;
  error?: string | null;
  created_at?: string;
  comparison?: AbComparison | null;
  progress?: AbProgress | null;
  [key: string]: unknown;
}

/* ---------------------------------------------------------------------------
 * Hardening loop.
 * ------------------------------------------------------------------------- */

/** A promotable failure candidate (GET /api/hardening/candidates). */
export interface HardeningCandidate {
  scorecard_id?: string;
  agent_id?: string;
  suite_id?: string;
  suite_version?: number;
  test_id?: string;
  task_success_rate?: number | null;
  n_failing?: number;
  n_errored?: number;
  n_failures?: number;
  created_at?: string;
  [key: string]: unknown;
}
export interface HardeningCandidates {
  candidates: HardeningCandidate[];
}

/** A below-threshold live production catch (GET /api/hardening/live-candidates). */
export interface HardeningLiveCandidate {
  trace_id?: string;
  agent_id?: string;
  score?: number | null;
  mean_score?: number | null;
  failing_criteria?: string[];
  input_reconstructed?: boolean;
  already_promoted?: boolean;
  final_output?: string;
  rubric_id?: string;
  created_at?: string;
  [key: string]: unknown;
}
export interface HardeningLiveCandidates {
  candidates: HardeningLiveCandidate[];
}

/** A regression-suite summary row (GET /api/hardening/suites). */
export interface HardeningSuiteSummary {
  regression_suite_id: string;
  suite_id?: string;
  agent_id?: string;
  source?: string;
  source_suite_id?: string;
  n_cases?: number;
  runs?: number;
  latest_delta?: HardeningDeltaSummary | null;
  status?: string;
  created_at?: string;
  [key: string]: unknown;
}
export interface HardeningSuites {
  suites: HardeningSuiteSummary[];
}

/** The result of a promote / rerun call — a reference to the (created or
 *  bumped) regression suite, plus any progress note. */
export interface HardeningSuiteRef {
  regression_suite_id?: string;
  suite_id?: string;
  version?: number;
  n_cases?: number;
  n_promoted?: number;
  added?: string[];
  skipped_duplicates?: string[];
  started?: boolean;
  note?: string;
  [key: string]: unknown;
}

/** The improved/regressed/same/new/errored tallies of a regression delta. */
export type HardeningDeltaSummary = Record<string, number>;

/** One per-case row of the latest regression delta. */
export interface HardeningDeltaCase {
  test_id: string;
  prev_passed?: boolean | null;
  now_passed?: boolean | null;
  status?: string;
  [key: string]: unknown;
}

/** The latest regression delta vs the prior re-run (McNemar-tested). */
export interface HardeningDelta {
  task_success_rate?: number | null;
  prev_task_success_rate?: number | null;
  success_delta?: number | null;
  summary?: HardeningDeltaSummary | null;
  mcnemar?: { significant?: boolean; [key: string]: JsonValue | undefined } | null;
  per_case: HardeningDeltaCase[];
  [key: string]: unknown;
}
export interface HardeningHistoryEntry {
  scorecard_id: string;
  suite_version?: number;
  task_success_rate?: number | null;
  n_cases?: number;
  errored?: number;
  created_at?: string;
  [key: string]: unknown;
}
export interface HardeningDetail {
  regression_suite_id?: string;
  suite_id?: string;
  agent_id?: string;
  source_suite_id?: string | null;
  version?: number;
  cases: HardeningDetailCase[];
  history: HardeningHistoryEntry[];
  latest_delta?: HardeningDelta | null;
  [key: string]: unknown;
}
export interface HardeningDetailCase {
  test_id: string;
  task_description?: string;
  origin?: string;
  provenance?: { why?: string; [key: string]: JsonValue | undefined } | null;
  [key: string]: unknown;
}

/* ---------------------------------------------------------------------------
 * Prompt optimizer.
 * ------------------------------------------------------------------------- */

export interface StartOptimizeResult {
  run_id: string;
  projected_agent_runs: number;
  max_agent_runs: number;
  note: string;
}

/** A row in GET /api/optimize/runs. */
export interface OptimizeRunSummary {
  run_id: string;
  status?: string;
  suite_id?: string;
  agent_id?: string;
  created_at?: string;
  best_score?: number | null;
  best_train_rate?: number | null;
  baseline_train_rate?: number | null;
  overfit_gap?: number | null;
  [key: string]: unknown;
}
export interface OptimizeRunList {
  runs: OptimizeRunSummary[];
}

/** One regressed criterion attached to a rejected candidate. */
export interface OptimizeRegression {
  criterion_id: string;
  [key: string]: JsonValue | undefined;
}
/** One candidate prompt in an optimizer round. */
export interface OptimizeCandidate {
  index?: number;
  prompt?: string;
  score?: number | null;
  accepted?: boolean;
  success_delta?: number | null;
  reason?: string;
  regressions?: OptimizeRegression[];
  [key: string]: unknown;
}
/** One optimizer round (rounds[] of the run artifact). */
export interface OptimizeRound {
  round?: number;
  baseline_train_rate?: number | null;
  failing_criteria?: string[];
  candidates?: OptimizeCandidate[];
  best_score?: number | null;
  [key: string]: unknown;
}
/** One point on the optimizer score curve (lineage[]). */
export interface OptimizeLineagePoint {
  version?: number;
  train_success_rate?: number | null;
  heldout_success_rate?: number | null;
  [key: string]: unknown;
}
/** The optimizer run artifact (the settled result the console renders). */
export interface OptimizeArtifact {
  improved?: boolean;
  best_version?: number;
  baseline_prompt?: string;
  best_prompt?: string;
  baseline_train_rate?: number | null;
  best_train_rate?: number | null;
  baseline_heldout_rate?: number | null;
  best_heldout_rate?: number | null;
  overfit_gap?: number | null;
  n_train?: number;
  n_heldout?: number;
  n_agent_runs?: number;
  total_cost_usd?: number | null;
  methodology?: string;
  lineage?: OptimizeLineagePoint[];
  rounds?: OptimizeRound[];
  degenerate?: boolean;
  [key: string]: unknown;
}
/** A live progress event streamed while an optimizer run is still working. */
export interface OptimizeProgress {
  event?: "cost_projection" | "propose" | "candidate" | "round_done" | string;
  round?: number;
  index?: number;
  accepted?: boolean;
  reason?: string;
  projected_agent_runs?: number;
  failing_criteria?: string[];
  [key: string]: unknown;
}
/** GET /api/optimize/runs/{id} — the run detail the console renders. The
 *  settled artifact lands on `run`; `progress` carries live-run events. */
export interface OptimizeRun {
  run_id?: string;
  status?: string;
  suite_id?: string;
  agent_id?: string;
  error?: string | null;
  run?: OptimizeArtifact | null;
  progress?: OptimizeProgress | null;
  [key: string]: unknown;
}

/* ---------------------------------------------------------------------------
 * Training camp (folded-in AgentCamp).
 * ------------------------------------------------------------------------- */

export type CampStatus = "queued" | "running" | "succeeded" | "failed";

export interface CampTask {
  task_id: string;
  name: string;
}
export interface CampTasks {
  tasks: CampTask[];
  modes: string[];
}

/** The promotion gate (service.evaluate_gate). */
export interface CampGate {
  promoted?: boolean;
  floor_met?: boolean;
  human_approved?: boolean;
  enough_data?: boolean;
  reasons?: string[];
  [key: string]: unknown;
}

/** One improve-loop round row. */
export interface CampRound {
  round: number;
  champion_gen?: number;
  champion_rate?: number | null;
  challenger_gen?: number;
  challenger_rate?: number | null;
  accepted?: boolean;
  note?: string;
  [key: string]: unknown;
}

/** One review-queue entry (champion's remaining failures). */
export interface CampReviewItem {
  message?: string;
  agent_action?: { action?: string; [key: string]: JsonValue | undefined };
  correct?: { action?: string; [key: string]: JsonValue | undefined };
  [key: string]: unknown;
}

/** One recorded graded episode. */
export interface CampEpisode {
  episode_id: string;
  passed?: boolean;
  inputs?: { message?: string; [key: string]: JsonValue | undefined };
  action?: { action?: string; [key: string]: JsonValue | undefined };
  score?: number | null;
  [key: string]: unknown;
}

/** The improve-loop report block. */
export interface CampReportBlock {
  degenerate?: boolean;
  final_champion_gen?: number | null;
  final_holdout_rate?: number | null;
  final_holdout_wilson?: number | null;
  halted_reason?: string | null;
  [key: string]: unknown;
}

/** A camp run (CampStore row). Covers list rows and the enriched detail. */
export interface CampRun {
  run_id: string;
  kind: "single" | "improve" | string;
  status: CampStatus | string;
  task_id?: string;
  mode?: string;
  phase?: string;
  error?: string | null;
  threshold?: number | null;
  pass_rate?: number | null;
  wilson_lower_95?: number | null;
  episodes?: number;
  passes?: number;
  total_episodes?: number;
  episodes_completed?: number;
  gate?: CampGate;
  report?: CampReportBlock;
  rounds?: CampRound[];
  review_queue?: CampReviewItem[];
  episode_sample?: CampEpisode[];
  episode_count?: number;
  distillation_count?: number;
  approved_by?: string;
  approved_at?: string;
  [key: string]: unknown;
}
export interface CampRunList {
  runs: CampRun[];
}

/* ---------------------------------------------------------------------------
 * Billing / pricing / status.
 * ------------------------------------------------------------------------- */

export interface BillingPlan {
  id: string;
  name: string;
  price_cents: number;
  interval: string;
  included_credits: number;
  features?: string[];
  highlight?: boolean;
}
export interface BillingTopup {
  id: string;
  name: string;
  price_cents: number;
  credits: number;
}
export interface PricingCatalog {
  currency: string;
  free_trial_credits: number;
  credit_cent_value: number;
  plans: BillingPlan[];
  topups: BillingTopup[];
  stripe_publishable_key?: string;
}
export interface BillingOverview {
  billing_enabled: boolean;
  currency: string;
  credit_cent_value: number;
  balance_credits: number;
  balance_cents: number;
  balance_display: string;
  plan: {
    id: string;
    name: string;
    price_cents: number;
    interval: string;
    included_credits: number;
  };
  status: string;
  provider: string;
  current_period_end: string | null;
  usage_by_reason: Record<string, number>;
}
export interface LedgerEntry {
  entry_id: string;
  kind: "grant" | "debit";
  credits: number;
  reason: string;
  model: string;
  meta: JsonObject;
  created_at: string;
}
export interface Invoice {
  invoice_id: string;
  number: string;
  provider: string;
  status: string;
  currency: string;
  subtotal_cents: number;
  tax_cents: number;
  total_cents: number;
  credits_granted: number;
  line_items: {
    description: string;
    quantity: number;
    unit_cents: number;
    amount_cents: number;
  }[];
  description: string;
  issued_at: string;
}
export interface BillingProviderConfig {
  stripe: { configured: boolean; test_mode: boolean; publishable_key?: string };
  paypal: { configured: boolean; sandbox: boolean };
}
export interface CheckoutResult {
  url: string;
  id: string;
}

export type HealthState = "operational" | "degraded" | "down" | "unknown";
export interface ComponentHealth {
  name: string;
  status: HealthState;
  latency_ms: number | null;
  detail: string;
  last_checked: string;
}
export interface ServiceStatus {
  status: HealthState;
  version: string | null;
  build: string | null;
  started_at: string;
  uptime_seconds: number;
  checked_at: string;
  components: ComponentHealth[];
}

/* ---------------------------------------------------------------------------
 * Misc uploads.
 * ------------------------------------------------------------------------- */

export interface UploadResult {
  file_path: string;
}
export interface ExtractResult {
  filename: string;
  chars: number;
  text: string;
}

/* ---------------------------------------------------------------------------
 * Copilot streaming events (moved verbatim from api.ts).
 * ------------------------------------------------------------------------- */

export interface CopilotToolEvent {
  tool: string;
  phase: "start" | "done";
  kind?: "read" | "write";
  ok?: boolean;
  summary?: string;
}
export interface CopilotApproval {
  tool: string;
  input: JsonObject;
  card: { title?: string; detail?: string; cost_note?: string; risk?: string };
}
export interface CopilotErrorInfo {
  code: string;
  message: string;
  action?: "retry" | "upgrade" | "none";
}
export interface CopilotHandlers {
  onSession?: (info: { session_id: string; status: string }) => void;
  onToken: (text: string) => void;
  onTool?: (ev: CopilotToolEvent) => void;
  onApproval?: (a: CopilotApproval) => void;
  onDone?: (info: { session_id: string; status: string }) => void;
  onError?: (err: CopilotErrorInfo) => void;
}
