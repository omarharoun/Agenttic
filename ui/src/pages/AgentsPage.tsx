import { useEffect, useState } from "react";
import { api } from "../api";

const SOURCE_COLOR: Record<string, string> = {
  scored: "var(--ok)", traced: "var(--cat-input)",
  live: "var(--wait)", managed: "var(--cat-benchmark)",
  declared: "var(--cat-agents)",
};

const BLANK = {
  agent_id: "", variant: "reference", description: "", model: "",
  system_prompt: "", url: "", managed_agent_id: "", environment_id: "",
};

/** Two complementary views of the platform's agents:
 *  - the declared catalog: agents an operator pre-registers (variant +
 *    connection details) so they're pickable when configuring a run;
 *  - discovery: every agent observed from runs, since the set is open-ended.
 *  Both are merged in /api/agents, so a row can be declared, discovered, or both. */
export function AgentsPage() {
  const [data, setData] = useState<any | null>(null);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [form, setForm] = useState({ ...BLANK });
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    api.listAgents().then(setData).catch(() => setData(null));
    api.listCatalog().then((c) => setCatalog(c.agents)).catch(() => setCatalog([]));
  };
  useEffect(reload, []);

  const submit = async () => {
    setError(null);
    try {
      await api.registerAgent(form);
      setForm({ ...BLANK });
      reload();
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  };

  if (!data) return <div className="page"><div className="list-page">…</div></div>;
  const { agents, warning } = data;

  return (
    <div className="page">
      <div className="list-page">
        <h2>Agents</h2>
        <p style={{ color: "var(--muted)", marginTop: -6 }}>
          Pre-register agents in the <b>catalog</b> so they're pickable when
          configuring a run; everything else is <b>discovered</b> from runs (the
          agent set is open-ended). The table below merges both.
        </p>

        <section className="policy-box" style={{ marginBottom: 16 }}>
          <div className="policy-title">register an agent</div>
          <div style={{ display: "grid",
                        gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div>
              <label>agent_id *</label>
              <input value={form.agent_id}
                     onChange={(e) => setForm({ ...form, agent_id: e.target.value })} />
            </div>
            <div>
              <label>variant</label>
              <select value={form.variant}
                      onChange={(e) => setForm({ ...form, variant: e.target.value })}>
                <option value="reference">reference</option>
                <option value="blackbox">blackbox</option>
                <option value="managed">managed</option>
              </select>
            </div>
            {form.variant === "reference" && (
              <div>
                <label>model <small>(optional override)</small></label>
                <input value={form.model}
                       onChange={(e) => setForm({ ...form, model: e.target.value })} />
              </div>
            )}
            {form.variant === "blackbox" && (
              <div>
                <label>url *</label>
                <input value={form.url}
                       onChange={(e) => setForm({ ...form, url: e.target.value })} />
              </div>
            )}
            {form.variant === "managed" && (
              <>
                <div>
                  <label>managed_agent_id *</label>
                  <input value={form.managed_agent_id}
                         onChange={(e) => setForm(
                           { ...form, managed_agent_id: e.target.value })} />
                </div>
                <div>
                  <label>environment_id *</label>
                  <input value={form.environment_id}
                         onChange={(e) => setForm(
                           { ...form, environment_id: e.target.value })} />
                </div>
              </>
            )}
            <div style={{ gridColumn: "1 / -1" }}>
              <label>description</label>
              <input value={form.description}
                     onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            {form.variant === "reference" && (
              <div style={{ gridColumn: "1 / -1" }}>
                <label>system_prompt <small>(task instructions)</small></label>
                <textarea value={form.system_prompt}
                          onChange={(e) => setForm(
                            { ...form, system_prompt: e.target.value })} />
              </div>
            )}
          </div>
          <button className="active" style={{ marginTop: 8 }}
                  disabled={!form.agent_id.trim()} onClick={submit}>
            register
          </button>
          {error && <span style={{ color: "var(--fail)", marginLeft: 10,
                                   fontSize: 12 }}>⚠ {error}</span>}
        </section>

        {catalog.length > 0 && (
          <table className="data" style={{ marginBottom: 20 }}>
            <thead>
              <tr><th>declared agent</th><th>type</th><th>version</th>
                  <th>connection</th><th></th></tr>
            </thead>
            <tbody>
              {catalog.map((a: any) => (
                <tr key={a.agent_id}>
                  <td>{a.agent_id}
                    {a.description && (
                      <div style={{ color: "var(--muted)", fontSize: 11 }}>
                        {a.description}</div>)}
                  </td>
                  <td>{a.variant}</td>
                  <td>v{a.version}</td>
                  <td style={{ fontFamily: "monospace", fontSize: 11 }}>
                    {a.url || a.managed_agent_id || a.model || "config default"}
                  </td>
                  <td>
                    <button onClick={() => api.retireAgent(a.agent_id).then(reload)}>
                      retire
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3 style={{ color: "var(--muted)" }}>all agents (declared + discovered)</h3>
        {warning && (
          <p style={{ color: "var(--wait)", fontSize: 12 }}>⚠ {warning}</p>
        )}
        {agents.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>
            No agents yet — register one above, or run a workflow and they
            appear here automatically.
          </p>
        ) : (
          <table className="data">
            <thead>
              <tr><th>agent</th><th>type</th><th>sources</th><th>scorecards</th>
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
                  <td>{a.declared ? a.variant : "—"}</td>
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
