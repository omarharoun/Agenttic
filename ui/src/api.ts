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
  listScorecards: () => afetch("/api/scorecards").then((r) => json<any[]>(r)),
  scorecardReport: (id: string) =>
    afetch(`/api/scorecards/${id}/report`).then((r) => r.text()),
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
