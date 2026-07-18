import { useEffect, useState } from "react";
import { api, type AgentsView, type CatalogAgent, errMessage } from "../api";
import { EmptyState, Field, PageHeader, Skeleton } from "../components/ui";
import { IconWarning, IconAgent } from "../icons";

const SOURCE_COLOR: Record<string, string> = {
  scored: "var(--ok)", traced: "var(--cat-input)",
  live: "var(--wait)", managed: "var(--cat-benchmark)",
  declared: "var(--cat-agents)",
};

const BLANK = {
  agent_id: "", variant: "reference", description: "", model: "",
  system_prompt: "", url: "", managed_agent_id: "", environment_id: "",
  cost_per_call_usd: 0, expected_input_tokens: 0, expected_output_tokens: 0,
};

/** Two complementary views of the platform's agents:
 *  - the declared catalog: agents an operator pre-registers (variant +
 *    connection details) so they're pickable when configuring a run;
 *  - discovery: every agent observed from runs, since the set is open-ended.
 *  Both are merged in /api/agents, so a row can be declared, discovered, or both. */
export function AgentsPage() {
  const [data, setData] = useState<AgentsView | null>(null);
  const [catalog, setCatalog] = useState<CatalogAgent[]>([]);
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
    } catch (e) {
      setError(errMessage(e));
    }
  };

  if (!data) {
    return (
      <div className="page"><div className="list-page">
        <PageHeader title="Agents" subtitle="Declared catalog + agents discovered from runs." />
        <Skeleton rows={6} />
      </div></div>
    );
  }
  const { agents, warning } = data;

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader title="Agents"
          subtitle={<>Pre-register agents in the <b>catalog</b> so they're pickable when
            configuring a run; everything else is <b>discovered</b> from runs (the
            agent set is open-ended). The table below merges both.</>} />

        <section className="policy-box" style={{ marginBottom: 16 }}>
          <div className="policy-title">register an agent</div>
          <div style={{ display: "grid",
                        gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div>
              <Field label="agent_id *">
                <input value={form.agent_id}
                       onChange={(e) => setForm({ ...form, agent_id: e.target.value })} />
              </Field>
            </div>
            <div>
              <Field label="variant">
                <select value={form.variant}
                        onChange={(e) => setForm({ ...form, variant: e.target.value })}>
                  <option value="reference">Built-in reference agent</option>
                  <option value="blackbox">Your API agent (external endpoint)</option>
                </select>
              </Field>
            </div>
            {form.variant === "reference" && (
              <div>
                <Field label={<>model <small>(optional override)</small></>}>
                  <input value={form.model}
                         onChange={(e) => setForm({ ...form, model: e.target.value })} />
                </Field>
              </div>
            )}
            {form.variant === "blackbox" && (
              <>
                <div>
                  <Field label="url *">
                    <input value={form.url}
                           onChange={(e) => setForm({ ...form, url: e.target.value })} />
                  </Field>
                </div>
                <div>
                  <Field label={<>cost per call (USD) <small>(black-box; 0 = unknown)</small></>}>
                    <input type="number" step="0.0001" value={form.cost_per_call_usd}
                           onChange={(e) => setForm({ ...form,
                             cost_per_call_usd: Number(e.target.value) || 0 })} />
                  </Field>
                </div>
              </>
            )}
            <div style={{ gridColumn: "1 / -1" }}>
              <Field label="description">
                <input value={form.description}
                       onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </Field>
            </div>
            {form.variant === "reference" && (
              <div style={{ gridColumn: "1 / -1" }}>
                <Field label={<>system_prompt <small>(task instructions)</small></>}>
                  <textarea value={form.system_prompt}
                            onChange={(e) => setForm(
                              { ...form, system_prompt: e.target.value })} />
                </Field>
              </div>
            )}
          </div>
          <button className="primary" style={{ marginTop: 8 }}
                  disabled={!form.agent_id.trim()} onClick={submit}>
            Register agent
          </button>
          {error && <span style={{ color: "var(--fail)", marginLeft: 10,
                                   fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}><IconWarning size={13} /> {error}</span>}
        </section>

        {catalog.length > 0 && (
          <div className="table-wrap" style={{ marginBottom: 20 }}>
            <table className="data">
              <thead>
                <tr><th>declared agent</th><th>type</th><th>version</th>
                    <th>connection</th><th></th></tr>
              </thead>
              <tbody>
                {catalog.map((a) => (
                  <tr key={a.agent_id}>
                    <td>{a.agent_id}
                      {a.description && (
                        <div style={{ color: "var(--muted)", fontSize: 11 }}>
                          {a.description}</div>)}
                    </td>
                    <td><span className="pill">{a.variant}</span></td>
                    <td>v{a.version}</td>
                    <td className="mono" style={{ fontSize: 11 }}>
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
          </div>
        )}

        <h3 style={{ color: "var(--muted)" }}>all agents (declared + discovered)</h3>
        {warning && (
          <p style={{ color: "var(--wait)", fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}><IconWarning size={13} /> {warning}</p>
        )}
        {agents.length === 0 ? (
          <EmptyState icon={<IconAgent />} title="No agents yet"
            hint="Register one above, or run a workflow — agents appear here automatically." />
        ) : (
          <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th>agent</th><th>type</th><th>sources</th><th className="num">scorecards</th>
                  <th className="num">traces</th><th>suites</th><th>last seen</th></tr>
            </thead>
            <tbody>
              {agents.map((a) => (
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
                    {(a.sources ?? []).map((s: string) => (
                      <span key={s} style={{
                        color: SOURCE_COLOR[s] ?? "var(--muted)",
                        border: `1px solid ${SOURCE_COLOR[s] ?? "var(--border)"}`,
                        borderRadius: 999, padding: "1px 7px", marginRight: 4,
                        fontSize: 11 }}>{s}</span>
                    ))}
                  </td>
                  <td className="num">{a.n_scorecards}</td>
                  <td className="num">{a.n_traces}</td>
                  <td>{(a.suites ?? []).join(", ") || "—"}</td>
                  <td>{a.last_seen
                    ? new Date(a.last_seen).toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
    </div>
  );
}
