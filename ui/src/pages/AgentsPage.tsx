import { useEffect, useState } from "react";
import { api } from "../api";

const SOURCE_COLOR: Record<string, string> = {
  scored: "var(--ok)", traced: "var(--cat-input)",
  live: "var(--wait)", managed: "var(--cat-benchmark)",
};

/** Agents the platform has observed. The agent set is open-ended — any
 * endpoint/config is a new agent — so this is discovery, not a fixed catalog:
 * it lists whatever has run, plus deployed managed agents. */
export function AgentsPage() {
  const [data, setData] = useState<any | null>(null);
  useEffect(() => { api.listAgents().then(setData).catch(() => setData(null)); }, []);
  if (!data) return <div className="page"><div className="list-page">…</div></div>;
  const { agents, warning } = data;

  return (
    <div className="page">
      <div className="list-page">
        <h2>Agents</h2>
        <p style={{ color: "var(--muted)", marginTop: -6 }}>
          Discovered from runs — the agent set is open-ended, so this lists
          every agent that has been run (scored or just traced){warning
            ? "" : ", plus deployed managed agents"}. Not a fixed catalog.
        </p>
        {warning && (
          <p style={{ color: "var(--wait)", fontSize: 12 }}>⚠ {warning}</p>
        )}
        {agents.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>
            No agents yet — run a workflow and they appear here automatically.
          </p>
        ) : (
          <table className="data">
            <thead>
              <tr><th>agent</th><th>sources</th><th>scorecards</th>
                  <th>traces</th><th>suites</th><th>last seen</th></tr>
            </thead>
            <tbody>
              {agents.map((a: any) => (
                <tr key={a.agent_id}>
                  <td>{a.agent_id}
                    {a.managed_agent_id && (
                      <div style={{ color: "var(--muted)", fontSize: 11,
                                    fontFamily: "monospace" }}>
                        {a.managed_agent_id}</div>
                    )}
                  </td>
                  <td>
                    {a.sources.map((s: string) => (
                      <span key={s} style={{
                        color: SOURCE_COLOR[s] ?? "var(--muted)",
                        border: `1px solid ${SOURCE_COLOR[s] ?? "var(--border)"}`,
                        borderRadius: 999, padding: "1px 7px", marginRight: 4,
                        fontSize: 11 }}>{s}</span>
                    ))}
                  </td>
                  <td>{a.n_scorecards}</td>
                  <td>{a.n_traces}</td>
                  <td>{a.suites.join(", ") || "—"}</td>
                  <td>{a.last_seen
                    ? new Date(a.last_seen).toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
