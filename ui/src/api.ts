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

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = JSON.stringify((await res.json()).detail);
    } catch {
      /* keep status */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  nodeTypes: () => fetch("/api/node-types").then((r) => json<NodeTypeSpec[]>(r)),
  listWorkflows: () => fetch("/api/workflows").then((r) => json<any[]>(r)),
  getWorkflow: (id: string) =>
    fetch(`/api/workflows/${id}`).then((r) =>
      json<{ workflow: WorkflowDoc; problems: string[] }>(r)),
  deleteWorkflow: (id: string) =>
    fetch(`/api/workflows/${id}`, { method: "DELETE" }),
  saveWorkflow: (wf: WorkflowDoc, dryRun = false) =>
    fetch(`/api/workflows${dryRun ? "?dry_run=true" : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(wf),
    }).then((r) => json<{ workflow_id: string; problems: string[] }>(r)),
  startExecution: (workflowId: string) =>
    fetch(`/api/workflows/${workflowId}/executions`, { method: "POST" }).then(
      (r) => json<{ execution_id: string }>(r)),
  getExecution: (id: string) =>
    fetch(`/api/executions/${id}`).then((r) => json<any>(r)),
  executionResults: (id: string) =>
    fetch(`/api/executions/${id}/results`).then((r) => json<any>(r)),
  listExecutions: () => fetch("/api/executions").then((r) => json<any[]>(r)),
  approve: (executionId: string) =>
    fetch(`/api/executions/${executionId}/approve`, { method: "POST" }).then(
      (r) => json<any>(r)),
  cancel: (executionId: string) =>
    fetch(`/api/executions/${executionId}/cancel`, { method: "POST" }).then(
      (r) => json<any>(r)),
  listSuites: () => fetch("/api/suites").then((r) => json<any[]>(r)),
  suiteReview: (id: string) =>
    fetch(`/api/suites/${id}/review`).then((r) => (r.ok ? r.text() : "")),
  approveSuite: (id: string, version: number) =>
    fetch(`/api/suites/${id}/approve?version=${version}`, { method: "POST" }),
  listScorecards: () => fetch("/api/scorecards").then((r) => json<any[]>(r)),
  scorecardReport: (id: string) =>
    fetch(`/api/scorecards/${id}/report`).then((r) => r.text()),
  listTraces: () => fetch("/api/traces").then((r) => json<any[]>(r)),
  getTrace: (id: string) => fetch(`/api/traces/${id}`).then((r) => json<any>(r)),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/uploads", { method: "POST", body: fd }).then((r) =>
      json<{ file_path: string }>(r));
  },
};
