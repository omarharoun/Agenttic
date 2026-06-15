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

function authHeaders(extra: HeadersInit = {}): Record<string, string> {
  const t = auth.get();
  const h: Record<string, string> = { ...(extra as Record<string, string>) };
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

/** fetch with the bearer token attached. */
function afetch(url: string, opts: RequestInit = {}) {
  return fetch(url, { ...opts, headers: authHeaders(opts.headers) });
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
    if (res.status === 401) detail = "401 — set a valid API token (🔑 in the nav)";
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
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
  listExecutions: () => afetch("/api/executions").then((r) => json<any[]>(r)),
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
  listScorecards: () => afetch("/api/scorecards").then((r) => json<any[]>(r)),
  scorecardReport: (id: string) =>
    afetch(`/api/scorecards/${id}/report`).then((r) => r.text()),
  listTraces: () => afetch("/api/traces").then((r) => json<any[]>(r)),
  getTrace: (id: string) => afetch(`/api/traces/${id}`).then((r) => json<any>(r)),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return afetch("/api/uploads", { method: "POST", body: fd }).then((r) =>
      json<{ file_path: string }>(r));
  },
};
