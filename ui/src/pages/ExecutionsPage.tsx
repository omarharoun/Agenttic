import { useEffect, useState } from "react";
import { api } from "../api";
import { ReplayCanvas } from "../canvas/ReplayCanvas";
import { DataView, EmptyState, PageHeader, RawToggle, Skeleton } from "../components/ui";
import { IssuesReport } from "../components/IssuesReport";
import { ResultsPanel } from "../panels/ResultsPanel";
import { useFlowStore } from "../store";

const STATE_COLOR: Record<string, string> = {
  succeeded: "var(--ok)", failed: "var(--fail)", cancelled: "var(--fail)",
  completed_with_errors: "var(--wait)",
  running: "var(--cat-input)", waiting_approval: "var(--wait)",
  interrupted: "var(--muted)",
};

export function ExecutionsPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [detail, setDetail] = useState<any | null>(null);
  const [results, setResults] = useState<any | null>(null);

  const inspect = (executionId: string) => {
    api.getExecution(executionId).then(setDetail);
    api.executionResults(executionId).then(setResults).catch(() => setResults(null));
  };
  const catalog = useFlowStore((s) => s.catalog);
  const setCatalog = useFlowStore((s) => s.setCatalog);

  const refresh = () => api.listExecutions().then((r) => { setRows(r); setLoaded(true); });
  useEffect(() => {
    if (!Object.keys(catalog).length) api.nodeTypes().then(setCatalog);
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader title="Runs"
                    subtitle="Every run you've scored — live progress, then the issues it surfaced (worst-first) with the full scoreboard behind them." />
        {!loaded ? <Skeleton rows={6} /> : rows.length === 0 ? (
          <EmptyState icon="▶" title="No runs yet"
                      hint="Start a New evaluation and hit Run — it'll show up here with live progress." />
        ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th>execution</th><th>workflow</th><th>status</th>
                  <th>started</th><th>nodes</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.execution_id}>
                  <td className="mono">{r.execution_id}</td>
                  <td>{r.workflow_id}</td>
                  <td>
                    <span className={`status-chip ${r.status}`}>{r.status.replace(/_/g, " ")}</span>
                    {r.error_reason && (
                      <div className="run-reason" title={r.error ?? r.error_reason}>
                        {r.error_reason}
                      </div>
                    )}
                  </td>
                  <td>{new Date(r.started_at).toLocaleTimeString()}</td>
                  <td>{Object.entries(r.node_states as Record<string, string>)
                    .map(([n, s]) => `${n}:${s}`).join("  ")}</td>
                  <td>
                    <button onClick={() => inspect(r.execution_id)}>
                      inspect
                    </button>
                    {r.status === "waiting_approval" && (
                      <button className="approve" style={{ marginLeft: 6 }}
                              onClick={() => api.approve(r.execution_id).then(refresh)}>
                        approve
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
        {detail && (
          <>
            <h2 style={{ marginTop: 22 }}>
              {detail.execution_id} <span style={{
                color: STATE_COLOR[detail.status] }}>({detail.status})</span>
            </h2>
            <ReplayCanvas execution={detail} />
            {results && (results.cases?.length || results.scorecards?.length) ? (
              <div style={{ maxWidth: 820, marginTop: 14 }}>
                {/* Issues first — the hero of a result. The full scoreboard follows. */}
                <IssuesReport executionId={detail.execution_id} />
                <details className="results-raw" style={{ marginTop: 16 }}>
                  <summary>Full scoreboard — every case, pass or fail</summary>
                  <ResultsPanel results={results} />
                </details>
              </div>
            ) : null}
            <h2 style={{ marginTop: 18 }}>Node outputs</h2>
            {Object.keys(detail.node_outputs ?? {}).length === 0 ? (
              <p className="muted-sm">No node outputs recorded for this run.</p>
            ) : (
              <div className="node-outputs">
                {Object.entries(detail.node_outputs as Record<string, unknown>).map(
                  ([nodeId, out]) => (
                    <div className="no-card" key={nodeId}>
                      <div className="no-head mono">{nodeId}</div>
                      <DataView value={out} />
                      <RawToggle value={out} />
                    </div>
                  ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
