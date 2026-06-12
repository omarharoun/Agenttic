import { useEffect, useState } from "react";
import { api } from "../api";

const STATE_COLOR: Record<string, string> = {
  succeeded: "var(--ok)", failed: "var(--fail)", cancelled: "var(--fail)",
  running: "var(--cat-input)", waiting_approval: "var(--wait)",
  interrupted: "var(--muted)",
};

export function ExecutionsPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [detail, setDetail] = useState<any | null>(null);

  const refresh = () => api.listExecutions().then(setRows);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="page">
      <div className="list-page">
        <h2>Executions</h2>
        <table className="data">
          <thead>
            <tr><th>execution</th><th>workflow</th><th>status</th>
                <th>started</th><th>nodes</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.execution_id}>
                <td style={{ fontFamily: "monospace" }}>{r.execution_id}</td>
                <td>{r.workflow_id}</td>
                <td style={{ color: STATE_COLOR[r.status] }}>{r.status}</td>
                <td>{new Date(r.started_at).toLocaleTimeString()}</td>
                <td>{Object.entries(r.node_states as Record<string, string>)
                  .map(([n, s]) => `${n}:${s}`).join("  ")}</td>
                <td>
                  <button onClick={() =>
                    api.getExecution(r.execution_id).then(setDetail)}>
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
        {detail && (
          <>
            <h2 style={{ marginTop: 22 }}>
              {detail.execution_id} — node outputs
            </h2>
            <pre className="doc">{JSON.stringify(detail.node_outputs, null, 2)}</pre>
          </>
        )}
      </div>
    </div>
  );
}
