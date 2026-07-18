/* SPEC-4 Step 17.2 — every API response is typed. The response-shape types
 * live in ./api/types.ts (mirrored field-for-field from the server's Pydantic
 * schemas). We re-export them here so existing `import { … } from "../api"`
 * call sites keep working unchanged. */
export * from "./api/types";
import type {
  // core schemas
  Scorecard, Trace, TestSuite, Rubric,
  // auth / settings
  Me, AuthResult, AnthropicKeyStatus, KeyTestResult, TokenList, CreatedToken,
  // workflows / executions
  NodeTypeSpec, WorkflowDoc, WorkflowSummary, WorkflowLoad, WorkflowSaveResult,
  StartExecutionResult, Execution, ExecutionResults, IssuesReport,
  ApproveExecutionResult, CancelExecutionResult, CostEstimate,
  // suites / agents / scorecards
  SuiteSummary, AgentsView, CatalogList, CatalogAgent, ScorecardSummary,
  // leaderboard / standard
  Leaderboard, StandardMetrics, StandardLeaderboard, StandardDatasets,
  // scan
  ScanPreview, StartScanResult, ScanJob, ScanFindingsDoc, PublicDemoPreview,
  // connect
  ConnectionStatus, ConnectionInput, ConnectionTestResult,
  // copilot / assistant
  CopilotStatus, AssistantSession,
  // certifications
  CertificationList, CertificationRecord, AssistantCertification,
  // A/B
  StartAbResult, AbRunSummary, AbComparison, AbRunDetail,
  // hardening
  HardeningCandidates, HardeningLiveCandidates, HardeningSuites, HardeningDetail,
  HardeningSuiteRef,
  // optimize
  StartOptimizeResult, OptimizeRunList, OptimizeRun,
  // camp
  CampTasks, CampRunList, CampRun,
  // billing / status
  PricingCatalog, BillingOverview, LedgerEntry, Invoice, BillingProviderConfig,
  CheckoutResult, ServiceStatus,
  // uploads
  UploadResult, ExtractResult,
  // copilot streaming
  CopilotHandlers, CopilotErrorInfo,
  // moat — lineage, calibration, escalations (SPEC-4 Step 20)
  AgentLineage, JudgeLineage, CalibrationReport, NextUnlabeled, LabelResult,
  EscalationInbox, EscalationRespondResult,
  // dynamic values
  JsonObject, JsonValue,
} from "./api/types";
// The public certification + directory render shapes live in ./cert (pure,
// unit-testable, no cycle back to api.ts) — reuse them so the console has ONE
// authoritative Certification type instead of a divergent copy.
import type { Certification, DirectoryEntry } from "./cert";

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

/** Narrow an unknown caught value to a human message — the typed replacement
 *  for the old `catch (e: any) { String(e?.message ?? e) }` idiom. Handles
 *  `Error`, the structured `{message}`/`{detail}` envelopes the server uses,
 *  and bare strings. */
export function errMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  if (e && typeof e === "object") {
    const o = e as Record<string, unknown>;
    const m = o.message ?? o.detail ?? o.error;
    if (typeof m === "string") return m;
  }
  return String(e);
}

/** Append the token to an SSE URL (EventSource has no header API). */
export function sseUrl(path: string): string {
  const t = auth.get();
  if (!t) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

/** A failed (or non-JSON) API call the UI can render gracefully. It always
 *  carries the HTTP `status` and a human `message`, and — critically — it is
 *  thrown INSTEAD OF a raw `JSON.parse` SyntaxError. A non-JSON body (a 502/504
 *  proxy HTML page, or an `/api/*` request that fell through to the SPA HTML
 *  shell and came back as `200 text/html`) used to blow up as
 *  "Unexpected Application Error! JSON.parse: unexpected character at line 1
 *  column 1", crashing the whole app via React Router's error boundary. Now it
 *  surfaces here as a typed, catchable error. */
export class ApiError extends Error {
  status: number;
  detail?: unknown;
  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Parse a JSON API response, tolerating ANY body. Both the ok and the error
 *  path funnel through here: a non-2xx response, or a 2xx whose body isn't JSON,
 *  yields a typed {@link ApiError} the caller can catch — never an uncaught
 *  SyntaxError. The body is read once as text so we can parse-or-fall-back
 *  regardless of what the server (or a proxy in front of it) actually sent. */
async function json<T>(res: Response): Promise<T> {
  const ctype = res.headers.get("content-type") || "";
  const body = await res.text().catch(() => "");
  // The parsed body is genuinely dynamic; keep it as a record of unknowns so we
  // can probe `.detail`/`.error` without an `any`.
  let data: Record<string, unknown> | undefined = undefined;
  // Parse when it's declared JSON, or looks like JSON — so a mislabeled JSON
  // body still works, while an HTML shell (`<!DOCTYPE …`) never does.
  if (body && (ctype.includes("application/json") || /^\s*[[{]/.test(body))) {
    try { data = JSON.parse(body); } catch { /* fall through to typed error */ }
  }

  if (!res.ok) {
    if (res.status === 401) {
      throw new ApiError("401 unauthenticated — log in or set an API token", 401,
                         data?.detail ?? data ?? body);
    }
    const detail: unknown = data?.detail ?? data?.error ?? (data === undefined ? body.trim() : undefined);
    const msg = typeof detail === "string" && detail
      ? detail
      : detail !== undefined ? JSON.stringify(detail) : `${res.status}`;
    throw new ApiError(msg, res.status, data?.detail ?? data ?? body);
  }

  // ok, but the body wasn't parseable JSON — the classic case is an `/api/*`
  // path that slipped through to the HTML SPA shell (200 text/html). An empty
  // body (e.g. a 204) is a legitimate "no content" and returns undefined.
  if (data === undefined) {
    if (res.status === 204 || body.trim() === "") return undefined as unknown as T;
    throw new ApiError(
      `Expected JSON but the server returned ${ctype || "an unparseable response"}`,
      res.status, body.slice(0, 200));
  }
  return data as T;
}

export const api = {
  // --- auth / session ---
  me: () => afetch("/api/me").then((r) => json<Me>(r)),
  signup: (email: string, password: string) =>
    afetch("/api/auth/signup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }).then((r) => json<AuthResult>(r)),
  login: (email: string, password: string) =>
    afetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }).then((r) => json<AuthResult>(r)),
  logout: () => afetch("/api/auth/logout", { method: "POST" }).then((r) => json<AuthResult>(r)),
  verifyEmail: (token: string) =>
    afetch("/api/auth/verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }).then((r) => json<AuthResult>(r)),
  resendVerification: (email: string) =>
    afetch("/api/auth/resend-verification", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    }).then((r) => json<AuthResult>(r)),

  // --- settings: BYO Anthropic key (never returns the raw key) ---
  anthropicKeyStatus: () =>
    afetch("/api/settings/anthropic-key").then((r) =>
      json<AnthropicKeyStatus>(r)),
  testAnthropicKey: (key: string) =>
    afetch("/api/settings/anthropic-key/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }).then((r) => json<KeyTestResult>(r)),
  setAnthropicKey: (key: string) =>
    afetch("/api/settings/anthropic-key", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }).then((r) => json<AuthResult>(r)),
  deleteAnthropicKey: () =>
    afetch("/api/settings/anthropic-key", { method: "DELETE" }).then((r) => json<AuthResult>(r)),

  // Personal API tokens (PATs) — programmatic REST access as the user's account.
  listTokens: () =>
    afetch("/api/settings/tokens").then((r) =>
      json<TokenList>(r)),
  createToken: (name: string) =>
    afetch("/api/settings/tokens", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then((r) => json<CreatedToken>(r)),
  revokeToken: (id: number) =>
    afetch(`/api/settings/tokens/${id}`, { method: "DELETE" }).then((r) => json<AuthResult>(r)),

  nodeTypes: () => afetch("/api/node-types").then((r) => json<NodeTypeSpec[]>(r)),
  listWorkflows: () => afetch("/api/workflows").then((r) => json<WorkflowSummary[]>(r)),
  getWorkflow: (id: string) =>
    afetch(`/api/workflows/${id}`).then((r) =>
      json<WorkflowLoad>(r)),
  deleteWorkflow: (id: string) =>
    afetch(`/api/workflows/${id}`, { method: "DELETE" }),
  saveWorkflow: (wf: WorkflowDoc, dryRun = false) =>
    afetch(`/api/workflows${dryRun ? "?dry_run=true" : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(wf),
    }).then((r) => json<WorkflowSaveResult>(r)),
  startExecution: (workflowId: string) =>
    afetch(`/api/workflows/${workflowId}/executions`, { method: "POST" }).then(
      (r) => json<StartExecutionResult>(r)),
  getExecution: (id: string) =>
    afetch(`/api/executions/${id}`).then((r) => json<Execution>(r)),
  executionResults: (id: string) =>
    afetch(`/api/executions/${id}/results`).then((r) => json<ExecutionResults>(r)),
  executionIssues: (id: string) =>
    afetch(`/api/executions/${id}/issues`).then((r) => json<IssuesReport>(r)),
  listExecutions: (workflowId?: string) =>
    afetch(`/api/executions${workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : ""}`)
      .then((r) => json<Execution[]>(r)),
  approve: (executionId: string) =>
    afetch(`/api/executions/${executionId}/approve`, { method: "POST" }).then(
      (r) => json<ApproveExecutionResult>(r)),
  cancel: (executionId: string) =>
    afetch(`/api/executions/${executionId}/cancel`, { method: "POST" }).then(
      (r) => json<CancelExecutionResult>(r)),
  estimateWorkflow: (id: string) =>
    afetch(`/api/workflows/${id}/estimate`).then((r) => json<CostEstimate>(r)),
  estimateSuite: (suiteId: string, agentId?: string) =>
    afetch(`/api/estimate?suite_id=${encodeURIComponent(suiteId)}` +
           (agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ""))
      .then((r) => json<CostEstimate>(r)),
  listSuites: () => afetch("/api/suites").then((r) => json<SuiteSummary[]>(r)),
  suiteReview: (id: string) =>
    afetch(`/api/suites/${id}/review`).then((r) => (r.ok ? r.text() : "")),
  approveSuite: (id: string, version: number) =>
    afetch(`/api/suites/${id}/approve?version=${version}`, { method: "POST" }),
  listAgents: () => afetch("/api/agents").then((r) => json<AgentsView>(r)),
  listCatalog: (includeRetired = false) =>
    afetch(`/api/agents/catalog${includeRetired ? "?include_retired=true" : ""}`)
      .then((r) => json<CatalogList>(r)),
  registerAgent: (agent: Record<string, JsonValue>) =>
    afetch("/api/agents/catalog", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(agent),
    }).then((r) => json<CatalogAgent>(r)),
  retireAgent: (agentId: string) =>
    afetch(`/api/agents/catalog/${encodeURIComponent(agentId)}`, {
      method: "DELETE",
    }).then((r) => json<AuthResult>(r)),
  leaderboard: (suites: string[] = []) =>
    afetch(`/api/leaderboard${suites.length ? `?suites=${suites.join(",")}` : ""}`)
      .then((r) => json<Leaderboard>(r)),
  // canonical standard benchmarking
  standardMetrics: () => afetch("/api/standard/metrics").then((r) => json<StandardMetrics>(r)),
  standardLeaderboard: () => afetch("/api/standard/leaderboard").then((r) => json<StandardLeaderboard>(r)),
  seedStandard: () => afetch("/api/standard/seed", { method: "POST" }).then((r) => json<StandardMetrics>(r)),
  standardDatasets: () => afetch("/api/standard/datasets").then((r) => json<StandardDatasets>(r)),
  ingestDataset: (id: string) =>
    afetch(`/api/standard/ingest/${id}`, { method: "POST" }).then((r) => json<StandardMetrics>(r)),
  runStandard: (body: { agent_id?: string; system_prompt?: string; k?: number }) =>
    afetch("/api/standard/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<StandardMetrics>(r)),
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
    }).then((r) => json<StartScanResult>(r)),
  scanStatus: (scanId: string) =>
    afetch(`/api/scan/${encodeURIComponent(scanId)}`).then((r) => json<ScanJob>(r)),
  scanFindings: (scanId: string) =>
    afetch(`/api/scan/${encodeURIComponent(scanId)}/findings`)
      .then((r) => json<ScanFindingsDoc>(r)),
  // Open demo — UNAUTHENTICATED (plain fetch, no auth header): runs the
  // reference agent live on the server's key, fresh results every run.
  publicDemoPreview: () =>
    fetch("/api/public/demo-scan/preview")
      .then((r) => json<PublicDemoPreview>(r)),
  startPublicDemo: (agentName = "") =>
    fetch("/api/public/demo-scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_name: agentName }),
    }).then((r) => json<StartScanResult>(r)),
  publicDemoStatus: (scanId: string) =>
    fetch(`/api/public/demo-scan/${encodeURIComponent(scanId)}`)
      .then((r) => json<ScanJob>(r)),
  publicDemoFindings: (scanId: string) =>
    fetch(`/api/public/demo-scan/${encodeURIComponent(scanId)}/findings`)
      .then((r) => json<ScanFindingsDoc>(r)),

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
      json<CopilotStatus>(r)),

  // --- Safe Assistant (flagship consumer chat) --------------------------
  // The sibling backend is implementing these; the UI normalizes responses
  // (see assistant.ts) and falls back to a labelled local preview if absent.
  createAssistantSession: () =>
    afetch("/api/assistant/sessions", { method: "POST" }).then((r) => json<AssistantSession>(r)),
  getAssistantSession: (id: string) =>
    afetch(`/api/assistant/sessions/${encodeURIComponent(id)}`).then((r) => json<AssistantSession>(r)),
  sendAssistantMessage: (id: string, text: string) =>
    afetch(`/api/assistant/sessions/${encodeURIComponent(id)}/message`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then((r) => json<AssistantSession>(r)),
  approveAssistantAction: (id: string, actionId: string, decision: "allow" | "deny") =>
    afetch(`/api/assistant/sessions/${encodeURIComponent(id)}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: actionId, decision }),
    }).then((r) => json<AssistantSession>(r)),

  // --- agent safety certification ---------------------------------------
  // Public reads (unauthenticated) — back the /certified pages + badge.
  publicCertification: (id: string) =>
    fetch(`/api/public/certifications/${encodeURIComponent(id)}`)
      .then((r) => json<Certification>(r)),
  publicCertifiedDirectory: () =>
    fetch("/api/public/certifications").then((r) => json<DirectoryEntry[]>(r)),
  // The Safe Assistant's REAL grade + cert id (latest valid cert), or a null
  // grade if none is issued — backs the honest seal on the public assistant
  // page + landing. Never a placeholder.
  assistantCertification: () =>
    fetch("/api/public/assistant/certification").then((r) => json<AssistantCertification>(r)),
  // Public, unauthenticated service-status rollup — backs the /status page.
  serviceStatus: () =>
    fetch("/api/status").then((r) => json<ServiceStatus>(r)),
  // Authenticated — issue from a scorecard, list, revoke.
  listCertifications: () =>
    afetch("/api/certifications").then((r) => json<CertificationList>(r)),
  issueCertification: (body: { scorecard_id: string; agent_name?: string }) =>
    afetch("/api/certifications", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<CertificationRecord>(r)),
  revokeCertification: (id: string) =>
    afetch(`/api/certifications/${encodeURIComponent(id)}`, { method: "DELETE" })
      .then((r) => json<AuthResult>(r)),

  listScorecards: () => afetch("/api/scorecards").then((r) => json<ScorecardSummary[]>(r)),
  getScorecard: (id: string) =>
    afetch(`/api/scorecards/${id}`).then((r) => json<Scorecard>(r)),
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
    variant_a: Record<string, JsonValue>; variant_b: Record<string, JsonValue>;
  }) =>
    afetch("/api/ab/runs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<StartAbResult>(r)),
  listAbRuns: () => afetch("/api/ab/runs").then((r) => json<AbRunSummary[]>(r)),
  getAbRun: (id: string) => afetch(`/api/ab/runs/${id}`).then((r) => json<AbRunDetail>(r)),
  abReport: (id: string) => afetch(`/api/ab/runs/${id}/report`).then((r) => r.text()),
  abPdf: (id: string) =>
    afetch(`/api/ab/runs/${id}/report.pdf`).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.blob();
    }),

  // -- hardening loop (failure → regression suite → re-run → delta) --------
  hardeningCandidates: () =>
    afetch("/api/hardening/candidates").then((r) => json<HardeningCandidates>(r)),
  hardeningSuites: () =>
    afetch("/api/hardening/suites").then((r) => json<HardeningSuites>(r)),
  hardeningDetail: (id: string) =>
    afetch(`/api/hardening/suites/${encodeURIComponent(id)}`).then((r) => json<HardeningDetail>(r)),
  promoteFailures: (body: { scorecard_id: string; test_ids?: string[] | null;
                            source?: string }) =>
    afetch("/api/hardening/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<HardeningSuiteRef>(r)),
  // live-monitor catches: below-threshold sampled production traces, promotable
  // into a needs-review regression suite (distinct from scorecard candidates).
  hardeningLiveCandidates: (agentId?: string) =>
    afetch("/api/hardening/live-candidates" +
      (agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "")
    ).then((r) => json<HardeningLiveCandidates>(r)),
  promoteLiveFailures: (body: { agent_id: string; trace_ids?: string[] | null;
                                rubric_id?: string; threshold?: number }) =>
    afetch("/api/hardening/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "live", ...body }),
    }).then((r) => json<HardeningSuiteRef>(r)),
  rerunRegression: (body: {
    regression_suite_id: string; variant?: string; url?: string;
    system_prompt?: string; model?: string; managed_agent_id?: string;
    environment_id?: string;
  }) =>
    afetch("/api/hardening/rerun", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<HardeningDetail>(r)),

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
    }).then((r) => json<StartOptimizeResult>(r)),
  listOptimizeRuns: () =>
    afetch("/api/optimize/runs").then((r) => json<OptimizeRunList>(r)),
  getOptimizeRun: (id: string) =>
    afetch(`/api/optimize/runs/${encodeURIComponent(id)}`).then((r) => json<OptimizeRun>(r)),

  // -- training camp (folded-in AgentCamp: run N episodes, grade, Wilson
  //    lower-bound accuracy, two-condition promotion gate, distillation export)
  campTasks: () =>
    afetch("/api/camps/tasks").then((r) =>
      json<CampTasks>(r)),
  listCamps: () =>
    afetch("/api/camps").then((r) => json<CampRunList>(r)),
  getCamp: (id: string) =>
    afetch(`/api/camps/${encodeURIComponent(id)}`).then((r) => json<CampRun>(r)),
  startCamp: (body: {
    task_id?: string; mode?: string; episodes?: number; threshold?: number;
    min_episodes_for_gate?: number; seed?: number; model?: string;
    agent_id?: string;
  }) =>
    afetch("/api/camps", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<CampRun>(r)),
  startImprove: (body: {
    task_id?: string; rounds?: number; episodes_per_round?: number;
    threshold?: number; holdout?: number; seed?: number; degenerate?: boolean;
  }) =>
    afetch("/api/camps/improve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<CampRun>(r)),
  approveCamp: (id: string) =>
    afetch(`/api/camps/${encodeURIComponent(id)}/approve`, { method: "POST" })
      .then((r) => json<CampRun>(r)),
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
    }).then((r) => json<CheckoutResult>(r)),
  /** Start a PayPal checkout (subscription or top-up); returns the approval URL. */
  checkoutPaypal: (body: { kind: "subscription" | "topup"; plan_id?: string; topup_id?: string }) =>
    afetch("/api/billing/checkout/paypal", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<CheckoutResult>(r)),

  listTraces: () => afetch("/api/traces").then((r) => json<Trace[]>(r)),
  getTrace: (id: string) => afetch(`/api/traces/${id}`).then((r) => json<Trace>(r)),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return afetch("/api/uploads", { method: "POST", body: fd }).then((r) =>
      json<UploadResult>(r));
  },
  // Upload a requirement document (pdf/docx/txt/md); server extracts the text.
  extractDocument: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return afetch("/api/documents/extract", { method: "POST", body: fd }).then(
      (r) => json<ExtractResult>(r));
  },

  // --- the "moat" surface (SPEC-4 Step 20) — the differentiators, made
  //     visible: config lineage + gate receipts, judge lineage, calibration +
  //     labeling, and the human-in-the-loop escalation inbox. ---------------

  /** The agent-config family tree: baseline → promoted/rejected children, each
   *  node carrying its FULL gate receipt verbatim. */
  agentLineage: (agentId: string) =>
    afetch(`/api/lineage/agents/${encodeURIComponent(agentId)}`)
      .then((r) => json<AgentLineage>(r)),
  /** A criterion's judge-config lineage (v1→vN) with before/after agreement. */
  judgeLineage: (criterionId: string) =>
    afetch(`/api/lineage/judges/${encodeURIComponent(criterionId)}`)
      .then((r) => json<JudgeLineage>(r)),
  /** Per-criterion calibration status + the open judge-optimization requests. */
  calibration: (suiteId?: string) =>
    afetch("/api/calibration" +
      (suiteId ? `?suite_id=${encodeURIComponent(suiteId)}` : ""))
      .then((r) => json<CalibrationReport>(r)),
  /** The next trace awaiting a human label for a criterion (labeling workspace). */
  nextUnlabeled: (criterionId: string, suiteId?: string) =>
    afetch(`/api/calibration/${encodeURIComponent(criterionId)}/next-unlabeled` +
      (suiteId ? `?suite_id=${encodeURIComponent(suiteId)}` : ""))
      .then((r) => json<NextUnlabeled>(r)),
  /** Append a human label on the shared {0, 0.5, 1} scale; returns updated status. */
  addLabel: (body: { trace_id: string; criterion_id: string; score: number }) =>
    afetch("/api/calibration/labels", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<LabelResult>(r)),
  /** The escalation inbox: pending questions + resolved history + pending_count. */
  escalations: () =>
    afetch("/api/escalations").then((r) => json<EscalationInbox>(r)),
  /** Resolve a pending escalation with the human's decision. */
  respondEscalation: (traceId: string, response: string) =>
    afetch(`/api/escalations/${encodeURIComponent(traceId)}/respond`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ response }),
    }).then((r) => json<EscalationRespondResult>(r)),
};

/* ------------------------------------------------------------------------ *
   Copilot streaming client.

   The Copilot endpoint streams Server-Sent Events. EventSource can't POST, so
   we POST via fetch and parse the SSE frames off the response body. Frames are
   `event: <name>\ndata: <payload>\n\n`; the server escapes newlines/backslashes
   in the payload (see routes/copilot.py `_sse`), which we reverse here.
 * ------------------------------------------------------------------------ */

/* CopilotToolEvent / CopilotApproval / CopilotErrorInfo / CopilotHandlers are
 * defined in ./api/types and re-exported from this module (see the top of the
 * file), so existing `import { CopilotApproval } from "../api"` call sites keep
 * working. */

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
  fetcher: (url: string, opts: RequestInit) => Promise<Response> = afetch,
): Promise<void> {
  const res = await fetcher(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    // The server refuses pre-flight (429/402/503) with a structured `detail`
    // ({code, message, action}) that mirrors the SSE `error` payload — carry it
    // through so the panel renders the same styled card either way.
    let raw: unknown = `${res.status}`;
    try { raw = (await res.json()).detail ?? raw; } catch { /* keep */ }
    const info: CopilotErrorInfo | undefined =
      raw && typeof raw === "object" && "code" in (raw as object)
        ? (raw as CopilotErrorInfo) : undefined;
    const msg = info?.message ?? (typeof raw === "string" ? raw : `${res.status}`);
    const err = new Error(msg) as Error & { status?: number; info?: CopilotErrorInfo };
    err.status = res.status;
    err.info = info;
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
      else if (ev === "error") {
        // structured {code,message,action}; tolerate a legacy bare-text payload
        let info: CopilotErrorInfo;
        try {
          const p = JSON.parse(payload);
          info = { code: p.code ?? "generic", message: p.message ?? payload,
                   action: p.action ?? "retry" };
        } catch { info = { code: "generic", message: payload, action: "retry" }; }
        handlers.onError?.(info);
      }
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

/* ------------------------------------------------------------------------ *
   Public (anonymous) Copilot streaming client — the /scan intake bot.

   Same SSE protocol and event parsing as the authed Copilot, but hitting the
   UNAUTHENTICATED /api/public/copilot/* routes with a PLAIN fetch: no bearer
   header, no session cookie, no CSRF. The public bot runs on the server's own
   key in the public-demo tenant with a strict 4-tool allowlist (preview_scan,
   start_demo_scan, get_scan_status, get_scan_findings). Pre-flight refusals
   (429/402/503/404) carry the same {code,message,action} detail as the authed
   client, so the same error cards render either way.
 * ------------------------------------------------------------------------ */

/** Is the public intake bot available on this server? available=false → the
 *  caller should fall back to the quick form and say the assistant is offline. */
export function publicCopilotStatus(): Promise<CopilotStatus> {
  return fetch("/api/public/copilot/status").then((r) =>
    json<CopilotStatus>(r));
}

/** Send a message to the public intake bot and stream its answer, tool activity,
 *  and any approval request. Omit/null sessionId to start a new anonymous
 *  session; pass the returned session_id to continue. No auth, no cookies. */
export function publicCopilotChat(
  message: string, sessionId: string | null, handlers: CopilotHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamCopilot("/api/public/copilot/chat",
    { message, session_id: sessionId ?? undefined }, handlers, signal, fetch);
}

/** Confirm (approved=true) or decline the public bot's pending demo scan; streams
 *  the resumed turn. Plain fetch — no auth. */
export function publicCopilotApprove(
  sessionId: string, approved: boolean, handlers: CopilotHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamCopilot("/api/public/copilot/approve",
    { session_id: sessionId, approved }, handlers, signal, fetch);
}
