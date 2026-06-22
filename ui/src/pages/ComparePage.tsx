import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, downloadBlob } from "../api";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";

type Variant = {
  label: string;
  variant: string;
  agent_id: string;
  model: string;
  system_prompt: string;
  url: string;
  managed_agent_id: string;
  environment_id: string;
};

const blankVariant = (label: string): Variant => ({
  label, variant: "reference", agent_id: "", model: "", system_prompt: "",
  url: "", managed_agent_id: "", environment_id: "",
});

const STATUS_COLOR: Record<string, string> = {
  running: "var(--cat-input)", succeeded: "var(--ok)", failed: "var(--fail)",
};

/** One variant's configuration. The three modes map to the three A/B cases:
 * a built-in reference agent (model + system-prompt are the knobs the
 * prompt-optimizer turns), an external HTTP agent, or a deployed managed agent. */
function VariantForm({ v, onChange, accent }: {
  v: Variant; onChange: (v: Variant) => void; accent: string;
}) {
  const set = (patch: Partial<Variant>) => onChange({ ...v, ...patch });
  return (
    <div className="policy-box" style={{ borderLeft: `3px solid ${accent}` }}>
      <div className="policy-title" style={{ color: accent }}>
        Variant {v.label}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <div>
          <label>agent_id *</label>
          <input value={v.agent_id} placeholder="e.g. triage-bot"
                 onChange={(e) => set({ agent_id: e.target.value })} />
        </div>
        <div>
          <label>kind</label>
          <select value={v.variant} onChange={(e) => set({ variant: e.target.value })}>
            <option value="reference">Built-in reference agent</option>
            <option value="blackbox">Your API agent (external endpoint)</option>
            <option value="managed">Managed agent (deployed)</option>
          </select>
        </div>
        {v.variant === "reference" && (
          <div style={{ gridColumn: "1 / -1" }}>
            <label>model <small>(optional — blank uses the default)</small></label>
            <input value={v.model} placeholder="e.g. claude-haiku-4-5-20251001"
                   onChange={(e) => set({ model: e.target.value })} />
          </div>
        )}
        {v.variant === "reference" && (
          <div style={{ gridColumn: "1 / -1" }}>
            <label>system_prompt <small>(task instructions)</small></label>
            <textarea value={v.system_prompt} rows={3}
                      onChange={(e) => set({ system_prompt: e.target.value })} />
          </div>
        )}
        {v.variant === "blackbox" && (
          <div style={{ gridColumn: "1 / -1" }}>
            <label>url *</label>
            <input value={v.url} placeholder="https://…/agent"
                   onChange={(e) => set({ url: e.target.value })} />
          </div>
        )}
        {v.variant === "managed" && (
          <>
            <div>
              <label>managed_agent_id *</label>
              <input value={v.managed_agent_id}
                     onChange={(e) => set({ managed_agent_id: e.target.value })} />
            </div>
            <div>
              <label>environment_id *</label>
              <input value={v.environment_id}
                     onChange={(e) => set({ environment_id: e.target.value })} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function variantPayload(v: Variant): Record<string, any> {
  // send only the fields the chosen kind needs (avoids stray validation)
  const base: Record<string, any> = { label: v.label, variant: v.variant,
    agent_id: v.agent_id };
  if (v.variant === "reference") { base.model = v.model; base.system_prompt = v.system_prompt; }
  if (v.variant === "blackbox") base.url = v.url;
  if (v.variant === "managed") {
    base.managed_agent_id = v.managed_agent_id;
    base.environment_id = v.environment_id;
  }
  return base;
}

const pct = (x: number) => `${Math.round((x ?? 0) * 100)}%`;
const signedPct = (x: number) => `${x >= 0 ? "+" : ""}${Math.round(x * 100)}pp`;

/** The side-by-side comparison scorecard with the honest verdict. */
function Comparison({ c, id }: { c: any; id: string }) {
  const [report, setReport] = useState("");
  const mc = c.mcnemar || {};
  const la = c.label_a, lb = c.label_b;
  const sig = mc.significant;
  const isTie = c.winner === "tie";
  const winnerLabel = isTie ? "No clear winner"
    : `Winner: ${c.winner === "B" ? lb : c.winner === "A" ? la : c.winner}`;

  return (
    <div style={{ marginTop: 18 }}>
      <div className={`verdict ${isTie ? "tie" : "win"}`}>
        <div className="v-lab">
          Verdict <span className="tag">{winnerLabel}</span>
        </div>
        <div className="v-text">{c.verdict}</div>
      </div>

      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th></th><th>{la} ({c.variant_a?.agent_id})</th>
                <th>{lb} ({c.variant_b?.agent_id})</th><th className="num">Δ ({lb}−{la})</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><b>Success (paired, n={c.n_paired})</b></td>
              <td className="num">{pct(c.success_rate_a)}</td>
              <td className="num">{pct(c.success_rate_b)}</td>
              <td className="num" style={{ color: c.success_delta > 0 ? "var(--ok)"
                : c.success_delta < 0 ? "var(--fail)" : "var(--muted)" }}>
                {signedPct(c.success_delta)}</td>
            </tr>
            <tr><td>Mean cost / run</td>
              <td className="num">${(c.mean_cost_a ?? 0).toFixed(4)}</td>
              <td className="num">${(c.mean_cost_b ?? 0).toFixed(4)}</td>
              <td className="num">${((c.mean_cost_b - c.mean_cost_a) || 0).toFixed(4)}</td></tr>
            <tr><td>Total cost</td>
              <td className="num">${(c.total_cost_a ?? 0).toFixed(4)}</td>
              <td className="num">${(c.total_cost_b ?? 0).toFixed(4)}</td>
              <td className="num">${((c.total_cost_b - c.total_cost_a) || 0).toFixed(4)}</td></tr>
            <tr><td>p95 latency</td>
              <td className="num">{Math.round(c.p95_latency_a ?? 0)} ms</td>
              <td className="num">{Math.round(c.p95_latency_b ?? 0)} ms</td>
              <td className="num">{Math.round((c.p95_latency_b - c.p95_latency_a) || 0)} ms</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 12, color: "var(--muted)", margin: "6px 2px" }}>
        McNemar's paired test: {mc.b} case(s) only {la} passed, {mc.c} only {lb} passed →
        {" "}{sig ? <b style={{ color: "var(--ok)" }}>significant (p={(mc.p_value ?? 0).toFixed(3)})</b>
          : mc.underpowered ? <span style={{ color: "var(--wait)" }}>too few to conclude (p={(mc.p_value ?? 0).toFixed(2)})</span>
          : <span>not significant (p={(mc.p_value ?? 0).toFixed(2)})</span>} ({mc.test}).
      </div>

      <h3 style={{ marginTop: 16 }}>Per-criterion deltas</h3>
      {c.per_criterion?.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>criterion</th><th className="num">{la}</th><th className="num">{lb}</th><th className="num">Δ</th>
              <th>favors</th><th>significance</th><th className="num">n</th></tr></thead>
            <tbody>
              {c.per_criterion.map((cc: any) => (
                <tr key={cc.criterion_id}>
                  <td className="mono">{cc.criterion_id}</td>
                  <td className="num">{pct(cc.mean_a)}</td><td className="num">{pct(cc.mean_b)}</td>
                  <td className="num" style={{ color: cc.delta > 0 ? "var(--ok)" : cc.delta < 0
                    ? "var(--fail)" : "var(--muted)" }}>{signedPct(cc.delta)}</td>
                  <td>{cc.direction === "tie" ? "—" : cc.direction === "B" ? lb : la}</td>
                  <td style={{ color: cc.significant ? "var(--ok)" : "var(--muted)" }}>
                    {cc.significant ? "significant" : "n.s."} (p={(cc.p_value ?? 0).toFixed(2)})</td>
                  <td className="num">{cc.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p style={{ color: "var(--muted)" }}>No criteria scored on paired cases.</p>}

      <h3 style={{ marginTop: 16 }}>Flipped cases</h3>
      {c.flipped_cases?.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>test case</th><th>{la}</th><th>{lb}</th><th>direction</th></tr></thead>
            <tbody>
              {c.flipped_cases.map((f: any) => (
                <tr key={f.test_id}>
                  <td className="mono">{f.test_id}</td>
                  <td className={f.a_passed ? "ok" : "err"}>{f.a_passed ? "PASS" : "FAIL"}</td>
                  <td className={f.b_passed ? "ok" : "err"}>{f.b_passed ? "PASS" : "FAIL"}</td>
                  <td>
                    <span className={`flip ${f.direction === "gain" ? "gain" : "loss"}`}>
                      {f.direction === "gain" ? `${la} fail → ${lb} pass`
                        : `${la} pass → ${lb} fail`}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p style={{ color: "var(--muted)" }}>No cases changed outcome between the variants.</p>}

      {c.excluded_test_ids?.length > 0 && (
        <p style={{ color: "var(--wait)", fontSize: 12, marginTop: 10 }}>
          ⚠ {c.excluded_test_ids.length} case(s) errored in a variant and were
          excluded from the comparison: {c.excluded_test_ids.join(", ")}
        </p>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button onClick={() => report ? setReport("") : api.abReport(id).then(setReport)}>
          {report ? "hide report" : "report"}
        </button>
        <button title="Download as PDF"
                onClick={() => api.abPdf(id)
                  .then((b) => downloadBlob(b, `ab-comparison-${id}.pdf`)).catch(() => {})}>
          ⤓ PDF
        </button>
        {c.scorecard_a_id && (
          <Link className="btn" to={`/app/hardening?promote=${c.scorecard_a_id}`}
                title={`Promote ${la}'s failing cases into a regression suite`}>
            🛡 Harden {la}'s failures
          </Link>
        )}
        {c.scorecard_b_id && (
          <Link className="btn" to={`/app/hardening?promote=${c.scorecard_b_id}`}
                title={`Promote ${lb}'s failing cases into a regression suite`}>
            🛡 Harden {lb}'s failures
          </Link>
        )}
      </div>
      {report && <pre className="doc" style={{ marginTop: 8 }}>{report}</pre>}
    </div>
  );
}

export function ComparePage() {
  const [suites, setSuites] = useState<any[] | null>(null);
  const [suiteId, setSuiteId] = useState("");
  const [a, setA] = useState(blankVariant("A"));
  const [b, setB] = useState(blankVariant("B"));
  const [runs, setRuns] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const refreshRuns = () => api.listAbRuns().then(setRuns).catch(() => {});
  useEffect(() => {
    api.listSuites().then(setSuites).catch(() => setSuites([]));
    refreshRuns();
    const t = setInterval(refreshRuns, 3000);
    return () => clearInterval(t);
  }, []);

  // poll the selected run until it settles
  useEffect(() => {
    if (!selected) return;
    let live = true;
    const tick = () => api.getAbRun(selected).then((d) => {
      if (!live) return;
      setDetail(d);
      if (d.status === "running") setTimeout(tick, 1500);
    }).catch(() => {});
    tick();
    return () => { live = false; };
  }, [selected]);

  const start = async () => {
    setError(null);
    setStarting(true);
    try {
      const { comparison_id } = await api.startAbRun({
        suite_id: suiteId,
        variant_a: variantPayload(a),
        variant_b: variantPayload(b),
      });
      setSelected(comparison_id);
      refreshRuns();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setStarting(false);
    }
  };

  const canStart = suiteId && a.agent_id.trim() && b.agent_id.trim() && !starting;

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader title="Compare (A/B)"
          subtitle="Run two agent variants head-to-head on the same suite — same cases, same judge — for a statistically honest verdict on which is better." />

        <section className="policy-box" style={{ marginBottom: 16 }}>
          <div className="policy-title">new comparison</div>
          <div style={{ marginBottom: 10 }}>
            <label>suite *</label>
            <select value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
              <option value="">— pick a suite —</option>
              {(suites ?? []).map((s) => (
                <option key={s.suite_id} value={s.suite_id} disabled={!s.approved}>
                  {s.suite_id} (v{s.version}, {s.n_cases} cases)
                  {s.approved ? "" : " — not approved"}
                </option>
              ))}
            </select>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <VariantForm v={a} onChange={setA} accent="var(--cat-agents)" />
            <VariantForm v={b} onChange={setB} accent="var(--cat-benchmark)" />
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
            <button className="active" disabled={!canStart} onClick={start}>
              {starting ? "starting…" : "Run comparison"}
            </button>
            <button onClick={() => setB({ ...a, label: "B" })}
                    title="Copy A's config into B (then tweak model/prompt)">
              clone A → B
            </button>
            {error && <span style={{ color: "var(--fail)", fontSize: 12 }}>⚠ {error}</span>}
          </div>
        </section>

        {detail && (
          <div style={{ marginBottom: 20 }}>
            <h2>
              {detail.comparison_id}{" "}
              <span style={{ color: STATUS_COLOR[detail.status] }}>
                ({detail.status})</span>
            </h2>
            {detail.status === "running" && (
              <p style={{ color: "var(--muted)" }}>
                <span className="spinner" /> running variant{" "}
                {detail.progress?.variant ?? "…"}
                {detail.progress?.total
                  ? ` — ${detail.progress.done ?? 0}/${detail.progress.total}` : ""}
              </p>
            )}
            {detail.status === "failed" && (
              <p style={{ color: "var(--fail)" }}>⚠ {detail.error}</p>
            )}
            {detail.comparison && (
              <Comparison c={detail.comparison} id={detail.comparison_id} />
            )}
          </div>
        )}

        <h3 style={{ color: "var(--muted)" }}>past comparisons</h3>
        {suites === null ? <Skeleton rows={4} /> : runs.length === 0 ? (
          <EmptyState icon="⚖" title="No comparisons yet"
            hint="Pick a suite, configure two variants, and run a comparison." />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>id</th><th>suite</th><th>status</th>
                <th>verdict</th><th>when</th><th></th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.comparison_id}>
                    <td className="mono">{r.comparison_id}</td>
                    <td>{r.suite_id}</td>
                    <td style={{ color: STATUS_COLOR[r.status] }}>{r.status}</td>
                    <td style={{ maxWidth: 360, fontSize: 12 }}>{r.verdict ?? "—"}</td>
                    <td>{new Date(r.created_at).toLocaleString()}</td>
                    <td><button onClick={() => setSelected(r.comparison_id)}>view</button></td>
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
