export interface NodeTypeSpec {
  type: string;
  title: string;
  category: string;
  description: string;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  config_schema: {
    properties?: Record<string, any>;
    required?: string[];
  };
}

export interface WorkflowNode {
  node_id: string;
  type: string;
  label: string;
  position: { x: number; y: number };
  config: Record<string, any>;
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

/** A safety dimension as the scan reports it (plain-language). */
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

/** A live scan job (GET /api/scan/{id}). */
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
  certificate: any | null;
  cert_note: string | null;
  error: string | null;
}

export interface ScanPreview {
  dimensions: { criterion_id: string; label: string; critical: boolean }[];
  endpoint: { needs_key: boolean; note: string };
  demo: { needs_key: boolean; key_set: boolean; note: string };
}

/** The saved "Connect your agent" config (masked — never carries the secret). */
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

/** What the user enters to configure / test / save a connection. */
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
  mapping: { preset: string; request_field: string; response_path: string; model: string };
}

/** One ranked issue in an execution's Issues report (GET /executions/{id}/issues). */
export interface Issue {
  id: string;
  title: string;
  criterion_id: string | null;
  category: string;
  category_label: string;
  severity: "critical" | "high" | "medium" | "low";
  impact_rank: number;
  why: string;
  affected_n: number;
  n_measured: number;
  affected_share: number | null;
  evidence: {
    counts: Record<string, number>;
    cases: {
      test_id?: string; score?: number; scorer?: string; calibrated?: boolean;
      rationale?: string | null; prediction?: string; expected?: string;
    }[];
    criteria?: { criterion_id: string; description?: string; provisional: number }[];
    truncated: number;
  };
  suggested_fix: { capability: string; label: string; route: string; blurb: string };
  status: string;
}

export interface IssuesReport {
  status: string;
  issues: Issue[];
  summary: {
    total_issues: number;
    by_severity: Record<"critical" | "high" | "medium" | "low", number>;
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

const TOKEN_KEY = "ascore_token";

/** API token store (shared bearer key). EventSource can't send headers, so
 * SSE URLs carry it as ?token= via sseUrl(). */
export const auth = {
  get: (): string => localStorage.getItem(TOKEN_KEY) || "",
  set: (t: string) =>
    t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY),
};

function readCookie(name: string): string {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : "";
}

function authHeaders(method: string, extra: HeadersInit = {}): Record<string, string> {
  const h: Record<string, string> = { ...(extra as Record<string, string>) };
  const t = auth.get();
  if (t) h.Authorization = `Bearer ${t}`;  // bearer (CI/power users) takes precedence
  // CSRF double-submit for cookie-authenticated mutations
  if (!t && !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    const csrf = readCookie("ascore_csrf");
    if (csrf) h["X-CSRF-Token"] = csrf;
  }
  return h;
}

/** fetch with credentials (session cookie) + bearer/CSRF as applicable. */
function afetch(url: string, opts: RequestInit = {}) {
  const method = opts.method || "GET";
  return fetch(url, {
    ...opts,
    credentials: "include",                 // send the session cookie
    headers: authHeaders(method, opts.headers),
  });
}

/** Trigger a browser download for a fetched blob (used for PDF export). */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Append the token to an SSE URL (EventSource has no header API). */
export function sseUrl(path: string): string {
  const t = auth.get();
  if (!t) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = JSON.stringify((await res.json()).detail);
    } catch {
      /* keep status */
    }
    if (res.status === 401) detail = "401 unauthenticated — log in or set an API token";
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export interface Me { role: string; tenant: string; email: string | null; auth_method: string; }

// --- billing ---------------------------------------------------------------
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
  stripe_publishable_key?: string;   // NOT secret — safe client-side
}
export interface BillingOverview {
  billing_enabled: boolean;
  currency: string;
  credit_cent_value: number;
  balance_credits: number;
  balance_cents: number;
  balance_display: string;
  plan: { id: string; name: string; price_cents: number; interval: string; included_credits: number };
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
  meta: Record<string, any>;
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
  line_items: { description: string; quantity: number; unit_cents: number; amount_cents: number }[];
  description: string;
  issued_at: string;
}
export interface BillingProviderConfig {
  stripe: { configured: boolean; test_mode: boolean; publishable_key?: string };
  paypal: { configured: boolean; sandbox: boolean };
}

/** Live service-status rollup (Agenttic's OWN uptime — GET /api/status). */
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

export const api = {
  // --- auth / session ---
  me: () => afetch("/api/me").then((r) => json<Me>(r)),
  signup: (email: string, password: string) =>
    afetch("/api/auth/signup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }).then((r) => json<any>(r)),
  login: (email: string, password: string) =>
    afetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }).then((r) => json<any>(r)),
  logout: () => afetch("/api/auth/logout", { method: "POST" }).then((r) => json<any>(r)),
  verifyEmail: (token: string) =>
    afetch("/api/auth/verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }).then((r) => json<any>(r)),
  resendVerification: (email: string) =>
    afetch("/api/auth/resend-verification", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    }).then((r) => json<any>(r)),

  // --- settings: BYO Anthropic key (never returns the raw key) ---
  anthropicKeyStatus: () =>
    afetch("/api/settings/anthropic-key").then((r) =>
      json<{ set: boolean; masked: string | null; updated_at: string | null }>(r)),
  testAnthropicKey: (key: string) =>
    afetch("/api/settings/anthropic-key/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }).then((r) => json<{ valid: boolean; error: string | null }>(r)),
  setAnthropicKey: (key: string) =>
    afetch("/api/settings/anthropic-key", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }).then((r) => json<any>(r)),
  deleteAnthropicKey: () =>
    afetch("/api/settings/anthropic-key", { method: "DELETE" }).then((r) => json<any>(r)),

  // Personal API tokens (PATs) — programmatic REST access as the user's account.
  listTokens: () =>
    afetch("/api/settings/tokens").then((r) =>
      json<{ tokens: { id: number; name: string; masked: string; created_at: string; last_used_at: string | null }[] }>(r)),
  createToken: (name: string) =>
    afetch("/api/settings/tokens", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then((r) => json<{ id: number; name: string; token: string; masked: string; created_at: string }>(r)),
  revokeToken: (id: number) =>
    afetch(`/api/settings/tokens/${id}`, { method: "DELETE" }).then((r) => json<any>(r)),

  nodeTypes: () => afetch("/api/node-types").then((r) => json<NodeTypeSpec[]>(r)),
  listWorkflows: () => afetch("/api/workflows").then((r) => json<any[]>(r)),
  getWorkflow: (id: string) =>
    afetch(`/api/workflows/${id}`).then((r) =>
      json<{ workflow: WorkflowDoc; problems: string[] }>(r)),
  deleteWorkflow: (id: string) =>
    afetch(`/api/workflows/${id}`, { method: "DELETE" }),
  saveWorkflow: (wf: WorkflowDoc, dryRun = false) =>
    afetch(`/api/workflows${dryRun ? "?dry_run=true" : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(wf),
    }).then((r) => json<{ workflow_id: string; problems: string[]; saved: boolean }>(r)),
  startExecution: (workflowId: string) =>
    afetch(`/api/workflows/${workflowId}/executions`, { method: "POST" }).then(
      (r) => json<{ execution_id: string }>(r)),
  getExecution: (id: string) =>
    afetch(`/api/executions/${id}`).then((r) => json<any>(r)),
  executionResults: (id: string) =>
    afetch(`/api/executions/${id}/results`).then((r) => json<any>(r)),
  executionIssues: (id: string) =>
    afetch(`/api/executions/${id}/issues`).then((r) => json<IssuesReport>(r)),
  listExecutions: (workflowId?: string) =>
    afetch(`/api/executions${workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : ""}`)
      .then((r) => json<any[]>(r)),
  approve: (executionId: string) =>
    afetch(`/api/executions/${executionId}/approve`, { method: "POST" }).then(
      (r) => json<any>(r)),
  cancel: (executionId: string) =>
    afetch(`/api/executions/${executionId}/cancel`, { method: "POST" }).then(
      (r) => json<any>(r)),
  estimateWorkflow: (id: string) =>
    afetch(`/api/workflows/${id}/estimate`).then((r) => json<any>(r)),
  estimateSuite: (suiteId: string, agentId?: string) =>
    afetch(`/api/estimate?suite_id=${encodeURIComponent(suiteId)}` +
           (agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ""))
      .then((r) => json<any>(r)),
  listSuites: () => afetch("/api/suites").then((r) => json<any[]>(r)),
  suiteReview: (id: string) =>
    afetch(`/api/suites/${id}/review`).then((r) => (r.ok ? r.text() : "")),
  approveSuite: (id: string, version: number) =>
    afetch(`/api/suites/${id}/approve?version=${version}`, { method: "POST" }),
  listAgents: () => afetch("/api/agents").then((r) => json<any>(r)),
  listCatalog: (includeRetired = false) =>
    afetch(`/api/agents/catalog${includeRetired ? "?include_retired=true" : ""}`)
      .then((r) => json<{ agents: any[] }>(r)),
  registerAgent: (agent: Record<string, any>) =>
    afetch("/api/agents/catalog", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(agent),
    }).then((r) => json<any>(r)),
  retireAgent: (agentId: string) =>
    afetch(`/api/agents/catalog/${encodeURIComponent(agentId)}`, {
      method: "DELETE",
    }).then((r) => json<any>(r)),
  leaderboard: (suites: string[] = []) =>
    afetch(`/api/leaderboard${suites.length ? `?suites=${suites.join(",")}` : ""}`)
      .then((r) => json<any>(r)),
  // canonical standard benchmarking
  standardMetrics: () => afetch("/api/standard/metrics").then((r) => json<any>(r)),
  standardLeaderboard: () => afetch("/api/standard/leaderboard").then((r) => json<any>(r)),
  seedStandard: () => afetch("/api/standard/seed", { method: "POST" }).then((r) => json<any>(r)),
  standardDatasets: () => afetch("/api/standard/datasets").then((r) => json<any>(r)),
  ingestDataset: (id: string) =>
    afetch(`/api/standard/ingest/${id}`, { method: "POST" }).then((r) => json<any>(r)),
  runStandard: (body: { agent_id?: string; system_prompt?: string; k?: number }) =>
    afetch("/api/standard/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<any>(r)),
  // --- safety scan ("Scan my agent") — the consumer on-ramp -------------
  scanPreview: () =>
    afetch("/api/scan/preview").then((r) => json<ScanPreview>(r)),
  startScan: (body: {
    target: "endpoint" | "demo" | "connection"; url?: string;
    header_name?: string; header_value?: string; agent_name?: string;
  }) =>
    afetch("/api/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ scan_id: string; target: string; n_dimensions: number }>(r)),
  scanStatus: (scanId: string) =>
    afetch(`/api/scan/${encodeURIComponent(scanId)}`).then((r) => json<ScanJob>(r)),

  // --- "Connect your agent" — the reusable, safe webhook connection ------
  getConnection: () =>
    afetch("/api/connect").then((r) => json<ConnectionStatus>(r)),
  saveConnection: (body: ConnectionInput) =>
    afetch("/api/connect", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<ConnectionStatus>(r)),
  deleteConnection: () =>
    afetch("/api/connect", { method: "DELETE" }).then((r) => json<ConnectionStatus>(r)),
  testConnection: (body: ConnectionInput) =>
    afetch("/api/connect/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<ConnectionTestResult>(r)),
  setConnectionConsent: (consent: boolean) =>
    afetch("/api/connect/consent", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ consent }),
    }).then((r) => json<ConnectionStatus>(r)),

  // --- Copilot (in-app guide assistant; server-side key, SSE streaming) --
  copilotStatus: () =>
    afetch("/api/copilot/status").then((r) =>
      json<{ available: boolean; model: string }>(r)),

  // --- Safe Assistant (flagship consumer chat) --------------------------
  // The sibling backend is implementing these; the UI normalizes responses
  // (see assistant.ts) and falls back to a labelled local preview if absent.
  createAssistantSession: () =>
    afetch("/api/assistant/sessions", { method: "POST" }).then((r) => json<any>(r)),
  getAssistantSession: (id: string) =>
    afetch(`/api/assistant/sessions/${encodeURIComponent(id)}`).then((r) => json<any>(r)),
  sendAssistantMessage: (id: string, text: string) =>
    afetch(`/api/assistant/sessions/${encodeURIComponent(id)}/message`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then((r) => json<any>(r)),
  approveAssistantAction: (id: string, actionId: string, decision: "allow" | "deny") =>
    afetch(`/api/assistant/sessions/${encodeURIComponent(id)}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: actionId, decision }),
    }).then((r) => json<any>(r)),

  // --- agent safety certification ---------------------------------------
  // Public reads (unauthenticated) — back the /certified pages + badge.
  publicCertification: (id: string) =>
    fetch(`/api/public/certifications/${encodeURIComponent(id)}`)
      .then((r) => json<any>(r)),
  publicCertifiedDirectory: () =>
    fetch("/api/public/certifications").then((r) => json<any>(r)),
  // The Safe Assistant's REAL grade + cert id (latest valid cert), or a null
  // grade if none is issued — backs the honest seal on the public assistant
  // page + landing. Never a placeholder.
  assistantCertification: () =>
    fetch("/api/public/assistant/certification").then((r) => json<any>(r)),
  // Public, unauthenticated service-status rollup — backs the /status page.
  serviceStatus: () =>
    fetch("/api/status").then((r) => json<ServiceStatus>(r)),
  // Authenticated — issue from a scorecard, list, revoke.
  listCertifications: () =>
    afetch("/api/certifications").then((r) => json<any>(r)),
  issueCertification: (body: { scorecard_id: string; agent_name?: string }) =>
    afetch("/api/certifications", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<any>(r)),
  revokeCertification: (id: string) =>
    afetch(`/api/certifications/${encodeURIComponent(id)}`, { method: "DELETE" })
      .then((r) => json<any>(r)),

  listScorecards: () => afetch("/api/scorecards").then((r) => json<any[]>(r)),
  getScorecard: (id: string) =>
    afetch(`/api/scorecards/${id}`).then((r) => json<any>(r)),
  scorecardReport: (id: string) =>
    afetch(`/api/scorecards/${id}/report`).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.text();
    }),
  scorecardPdf: (id: string) =>
    afetch(`/api/scorecards/${id}/report.pdf`).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.blob();
    }),
  // --- A/B comparison (two variants, head-to-head on one suite) ---
  startAbRun: (body: {
    suite_id: string; version?: number | null;
    variant_a: Record<string, any>; variant_b: Record<string, any>;
  }) =>
    afetch("/api/ab/runs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ comparison_id: string }>(r)),
  listAbRuns: () => afetch("/api/ab/runs").then((r) => json<any[]>(r)),
  getAbRun: (id: string) => afetch(`/api/ab/runs/${id}`).then((r) => json<any>(r)),
  abReport: (id: string) => afetch(`/api/ab/runs/${id}/report`).then((r) => r.text()),
  abPdf: (id: string) =>
    afetch(`/api/ab/runs/${id}/report.pdf`).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.blob();
    }),

  // -- hardening loop (failure → regression suite → re-run → delta) --------
  hardeningCandidates: () =>
    afetch("/api/hardening/candidates").then((r) => json<{ candidates: any[] }>(r)),
  hardeningSuites: () =>
    afetch("/api/hardening/suites").then((r) => json<{ suites: any[] }>(r)),
  hardeningDetail: (id: string) =>
    afetch(`/api/hardening/suites/${encodeURIComponent(id)}`).then((r) => json<any>(r)),
  promoteFailures: (body: { scorecard_id: string; test_ids?: string[] | null;
                            source?: string }) =>
    afetch("/api/hardening/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<any>(r)),
  // live-monitor catches: below-threshold sampled production traces, promotable
  // into a needs-review regression suite (distinct from scorecard candidates).
  hardeningLiveCandidates: (agentId?: string) =>
    afetch("/api/hardening/live-candidates" +
      (agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "")
    ).then((r) => json<{ candidates: any[] }>(r)),
  promoteLiveFailures: (body: { agent_id: string; trace_ids?: string[] | null;
                                rubric_id?: string; threshold?: number }) =>
    afetch("/api/hardening/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "live", ...body }),
    }).then((r) => json<any>(r)),
  rerunRegression: (body: {
    regression_suite_id: string; variant?: string; url?: string;
    system_prompt?: string; model?: string; managed_agent_id?: string;
    environment_id?: string;
  }) =>
    afetch("/api/hardening/rerun", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<any>(r)),

  // -- prompt-optimizer (self-improving system prompt; OPRO/ProTeGi) --------
  startOptimize: (body: {
    agent_id?: string; suite_id: string; version?: number | null;
    baseline_prompt?: string; rounds?: number; candidates_per_round?: number;
    heldout_fraction?: number; seed?: number; variant?: string; model?: string;
    url?: string; max_agent_runs?: number;
  }) =>
    afetch("/api/optimize/runs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{
      run_id: string; projected_agent_runs: number; max_agent_runs: number;
      note: string;
    }>(r)),
  listOptimizeRuns: () =>
    afetch("/api/optimize/runs").then((r) => json<{ runs: any[] }>(r)),
  getOptimizeRun: (id: string) =>
    afetch(`/api/optimize/runs/${encodeURIComponent(id)}`).then((r) => json<any>(r)),

  // -- training camp (folded-in AgentCamp: run N episodes, grade, Wilson
  //    lower-bound accuracy, two-condition promotion gate, distillation export)
  campTasks: () =>
    afetch("/api/camps/tasks").then((r) =>
      json<{ tasks: { task_id: string; name: string }[]; modes: string[] }>(r)),
  listCamps: () =>
    afetch("/api/camps").then((r) => json<{ runs: any[] }>(r)),
  getCamp: (id: string) =>
    afetch(`/api/camps/${encodeURIComponent(id)}`).then((r) => json<any>(r)),
  startCamp: (body: {
    task_id?: string; mode?: string; episodes?: number; threshold?: number;
    min_episodes_for_gate?: number; seed?: number; model?: string;
    agent_id?: string;
  }) =>
    afetch("/api/camps", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<any>(r)),
  startImprove: (body: {
    task_id?: string; rounds?: number; episodes_per_round?: number;
    threshold?: number; holdout?: number; seed?: number; degenerate?: boolean;
  }) =>
    afetch("/api/camps/improve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<any>(r)),
  approveCamp: (id: string) =>
    afetch(`/api/camps/${encodeURIComponent(id)}/approve`, { method: "POST" })
      .then((r) => json<any>(r)),
  exportCampDistillation: (id: string) =>
    afetch(`/api/camps/${encodeURIComponent(id)}/distillation.jsonl`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.blob();
      }),

  // --- billing / subscription ---
  /** Public pricing catalog (no auth) — plans + free-credit offer + top-ups. */
  pricing: () => afetch("/api/pricing").then((r) => json<PricingCatalog>(r)),
  billingOverview: () => afetch("/api/billing").then((r) => json<BillingOverview>(r)),
  billingPlans: () => afetch("/api/billing/plans").then((r) => json<PricingCatalog>(r)),
  billingLedger: (limit = 50) =>
    afetch(`/api/billing/ledger?limit=${limit}`).then((r) =>
      json<{ entries: LedgerEntry[] }>(r)),
  billingInvoices: () =>
    afetch("/api/billing/invoices").then((r) => json<{ invoices: Invoice[] }>(r)),
  billingProviderConfig: () =>
    afetch("/api/billing/config").then((r) => json<BillingProviderConfig>(r)),
  /** URL for the printable invoice HTML (opened/downloaded in a new tab). */
  invoiceDownloadUrl: (invoiceId: string) =>
    `/api/billing/invoices/${invoiceId}/download`,
  /** Start a Stripe checkout (subscription or top-up); returns the redirect URL. */
  checkoutStripe: (body: { kind: "subscription" | "topup"; plan_id?: string; topup_id?: string }) =>
    afetch("/api/billing/checkout/stripe", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ url: string; id: string }>(r)),
  /** Start a PayPal checkout (subscription or top-up); returns the approval URL. */
  checkoutPaypal: (body: { kind: "subscription" | "topup"; plan_id?: string; topup_id?: string }) =>
    afetch("/api/billing/checkout/paypal", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ url: string; id: string }>(r)),

  listTraces: () => afetch("/api/traces").then((r) => json<any[]>(r)),
  getTrace: (id: string) => afetch(`/api/traces/${id}`).then((r) => json<any>(r)),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return afetch("/api/uploads", { method: "POST", body: fd }).then((r) =>
      json<{ file_path: string }>(r));
  },
  // Upload a requirement document (pdf/docx/txt/md); server extracts the text.
  extractDocument: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return afetch("/api/documents/extract", { method: "POST", body: fd }).then(
      (r) => json<{ filename: string; chars: number; text: string }>(r));
  },
};

/* ------------------------------------------------------------------------ *
   Copilot streaming client.

   The Copilot endpoint streams Server-Sent Events. EventSource can't POST, so
   we POST via fetch and parse the SSE frames off the response body. Frames are
   `event: <name>\ndata: <payload>\n\n`; the server escapes newlines/backslashes
   in the payload (see routes/copilot.py `_sse`), which we reverse here.
 * ------------------------------------------------------------------------ */

/** A tool-activity event (the agent using the platform API on your behalf). */
export interface CopilotToolEvent {
  tool: string;
  phase: "start" | "done";
  kind?: "read" | "write";
  ok?: boolean;
  summary?: string;
}

/** A write/cost action the agent proposes; the user must confirm before it runs. */
export interface CopilotApproval {
  tool: string;
  input: Record<string, any>;
  card: { title?: string; detail?: string; cost_note?: string; risk?: string };
}

export interface CopilotHandlers {
  onSession?: (info: { session_id: string; status: string }) => void;
  onToken: (text: string) => void;
  onTool?: (ev: CopilotToolEvent) => void;
  onApproval?: (a: CopilotApproval) => void;
  onDone?: (info: { session_id: string; status: string }) => void;
  onError?: (message: string) => void;
}

function unescapeSse(s: string): string {
  let out = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && i + 1 < s.length) {
      const n = s[++i];
      out += n === "n" ? "\n" : n; // \\ -> \, \n -> newline
    } else {
      out += s[i];
    }
  }
  return out;
}

async function streamCopilot(
  path: string, body: unknown, handlers: CopilotHandlers, signal?: AbortSignal,
): Promise<void> {
  const res = await afetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = `${res.status}`;
    try { detail = String((await res.json()).detail ?? detail); } catch { /* keep */ }
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let ev = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) ev = line.slice(7);
        else if (line.startsWith("data: ")) data = line.slice(6);
      }
      const payload = unescapeSse(data);
      if (ev === "token") handlers.onToken(payload);
      else if (ev === "tool") { try { handlers.onTool?.(JSON.parse(payload)); } catch { /* ignore */ } }
      else if (ev === "approval_required") { try { handlers.onApproval?.(JSON.parse(payload)); } catch { /* ignore */ } }
      else if (ev === "session") { try { handlers.onSession?.(JSON.parse(payload)); } catch { /* ignore */ } }
      else if (ev === "done") { try { handlers.onDone?.(JSON.parse(payload)); } catch { /* ignore */ } }
      else if (ev === "error") handlers.onError?.(payload);
    }
  }
}

/** Send a message to the agentic Copilot and stream its reasoning, tool activity,
 *  and any approval request. Pass session_id to continue a session. */
export function copilotChat(
  message: string, sessionId: string | null, handlers: CopilotHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamCopilot("/api/copilot/chat",
    { message, session_id: sessionId ?? undefined }, handlers, signal);
}

/** Confirm (approved=true) or decline (false) the agent's pending write action;
 *  streams the resumed turn. */
export function copilotApprove(
  sessionId: string, approved: boolean, handlers: CopilotHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamCopilot("/api/copilot/approve",
    { session_id: sessionId, approved }, handlers, signal);
}
