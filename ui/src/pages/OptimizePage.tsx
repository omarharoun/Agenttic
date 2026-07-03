import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";
import { Term } from "../components/Term";

const pct = (x: number | null | undefined) =>
  x == null ? "—" : `${Math.round(x * 100)}%`;
const signedPct = (x: number | null | undefined) =>
  x == null ? "—" : `${x >= 0 ? "+" : ""}${Math.round(x * 100)}pp`;

/** Train-vs-heldout is the overfitting guard: a large positive overfit_gap means
 * gains are likely memorized to the suite, not real generalization. */
function overfitColor(gap: number | null | undefined): string {
  if (gap == null) return "var(--muted)";
  if (gap > 0.15) return "var(--fail)";
  if (gap > 0.05) return "var(--wait)";
  return "var(--ok)";
}

type StartCfg = {
  agent_id: string; suite_id: string; baseline_prompt: string;
  rounds: number; candidates_per_round: number; heldout_fraction: number;
  model: string; max_agent_runs: number;
};
const blank = (): StartCfg => ({
  agent_id: "agent-under-test", suite_id: "", baseline_prompt: "",
  rounds: 2, candidates_per_round: 3, heldout_fraction: 0.3, model: "",
  max_agent_runs: 60,
});

/** Launch a prompt-optimization run. Cost-warned: it surfaces the projected
 * number of suite executions before the run starts (BYO-key pays for each). */
function StartForm({ suites, onStarted }: {
  suites: { suite_id: string; approved: boolean }[];
  onStarted: (runId: string) => void;
}) {
  const [cfg, setCfg] = useState<StartCfg>(blank());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const set = (p: Partial<StartCfg>) => setCfg({ ...cfg, ...p });
  const approved = suites.filter((s) => s.approved);

  const run = async () => {
    setErr(null); setNote(null); setBusy(true);
    try {
      const res = await api.startOptimize(cfg);
      setNote(`~${res.projected_agent_runs} suite executions projected ` +
              `(cap ${res.max_agent_runs}). ${res.note}`);
      onStarted(res.run_id);
    } catch (e: any) {
      setErr(String(e.message ?? e));
    } finally { setBusy(false); }
  };

  return (
    <div className="policy-box">
      <div className="policy-title">optimize a system prompt</div>
      <p style={{ color: "var(--muted)", fontSize: 12, margin: "2px 0 10px" }}>
        Keep the model frozen; treat the suite score as the reward. Each round
        reads the failing criteria + judge rationales (the gradient), an LLM
        proposes edited prompts, and a candidate is kept only on a paired
        improvement with <b>no regressed criterion</b>. A held-out slice the
        optimizer never sees makes overfitting visible.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <div>
          <label>suite</label>
          <select value={cfg.suite_id} onChange={(e) => set({ suite_id: e.target.value })}>
            <option value="">select an approved suite…</option>
            {approved.map((s) => (
              <option key={s.suite_id} value={s.suite_id}>{s.suite_id}</option>
            ))}
          </select>
        </div>
        <div>
          <label>agent id</label>
          <input value={cfg.agent_id}
                 onChange={(e) => set({ agent_id: e.target.value })} />
        </div>
        <div style={{ gridColumn: "1 / -1" }}>
          <label>baseline system prompt <small>(the starting point to improve)</small></label>
          <textarea value={cfg.baseline_prompt} rows={3}
                    placeholder="leave blank to start from no system prompt"
                    onChange={(e) => set({ baseline_prompt: e.target.value })} />
        </div>
        <div>
          <label>rounds</label>
          <input type="number" min={1} max={10} value={cfg.rounds}
                 onChange={(e) => set({ rounds: +e.target.value })} />
        </div>
        <div>
          <label>candidates / round</label>
          <input type="number" min={1} max={8} value={cfg.candidates_per_round}
                 onChange={(e) => set({ candidates_per_round: +e.target.value })} />
        </div>
        <div>
          <label>held-out fraction <small>(overfitting guard)</small></label>
          <input type="number" min={0} max={0.5} step={0.05}
                 value={cfg.heldout_fraction}
                 onChange={(e) => set({ heldout_fraction: +e.target.value })} />
        </div>
        <div>
          <label>max suite executions <small>(cost cap)</small></label>
          <input type="number" min={1} value={cfg.max_agent_runs}
                 onChange={(e) => set({ max_agent_runs: +e.target.value })} />
        </div>
        <div>
          <label>model <small>(frozen across the run)</small></label>
          <input value={cfg.model} placeholder="blank = default"
                 onChange={(e) => set({ model: e.target.value })} />
        </div>
      </div>
      {err && <div style={{ color: "var(--fail)", marginTop: 8 }}>{err}</div>}
      {note && <div style={{ color: "var(--wait)", marginTop: 8, fontSize: 12 }}>{note}</div>}
      <button className="active" style={{ marginTop: 10 }} disabled={busy || !cfg.suite_id}
              onClick={run}>{busy ? "starting…" : "Optimize prompt"}</button>
    </div>
  );
}

/** Per-version train (solid) and held-out (dashed) score curve. Divergence
 * between the two lines IS the overfitting signal. */
function ScoreCurve({ lineage }: { lineage: any[] }) {
  if (!lineage?.length) return null;
  const W = 420, H = 140, pad = 28;
  const xs = lineage.map((_, i) =>
    pad + (lineage.length === 1 ? 0 : i * (W - 2 * pad) / (lineage.length - 1)));
  const y = (v: number) => H - pad - v * (H - 2 * pad);
  const path = (key: string) => lineage
    .map((v, i) => v[key] == null ? null : `${xs[i]},${y(v[key])}`)
    .filter(Boolean).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: W }}>
      {[0, 0.5, 1].map((g) => (
        <g key={g}>
          <line x1={pad} x2={W - pad} y1={y(g)} y2={y(g)} stroke="var(--border)" />
          <text x={4} y={y(g) + 4} fontSize={9} fill="var(--muted)">{g * 100}%</text>
        </g>
      ))}
      <polyline points={path("train_success_rate")} fill="none"
                stroke="var(--accent)" strokeWidth={2} />
      <polyline points={path("heldout_success_rate")} fill="none"
                stroke="var(--info)" strokeWidth={2} strokeDasharray="4 3" />
      {lineage.map((v, i) => (
        <g key={i}>
          {v.train_success_rate != null &&
            <circle cx={xs[i]} cy={y(v.train_success_rate)} r={3} fill="var(--accent)" />}
          <text x={xs[i]} y={H - 8} fontSize={9} textAnchor="middle"
                fill="var(--muted)">v{v.version}</text>
        </g>
      ))}
    </svg>
  );
}

/** Baseline → best prompt diff (line-level add/remove). */
function PromptDiff({ before, after }: { before: string; after: string }) {
  const a = (before || "").split("\n");
  const b = (after || "").split("\n");
  const bSet = new Set(b), aSet = new Set(a);
  return (
    <div className="mono" style={{ fontSize: 12, lineHeight: 1.5,
                                   background: "var(--panel-2)", borderRadius: 8,
                                   padding: 10, whiteSpace: "pre-wrap" }}>
      {a.filter((l) => !bSet.has(l)).map((l, i) => (
        <div key={`r${i}`} style={{ color: "var(--fail)" }}>- {l}</div>
      ))}
      {b.filter((l) => !aSet.has(l)).map((l, i) => (
        <div key={`a${i}`} style={{ color: "var(--ok)" }}>+ {l}</div>
      ))}
      {before === after && <div style={{ color: "var(--muted)" }}>
        (best prompt unchanged from baseline — no candidate improved the suite)
      </div>}
    </div>
  );
}

function RunDetail({ run }: { run: any }) {
  const art = run.run;
  const prog = run.progress;
  if (run.status === "running") {
    return (
      <div className="policy-box">
        <div className="policy-title">optimizing… <span className="spinner" /></div>
        <p style={{ color: "var(--muted)", fontSize: 12 }}>
          {prog?.event === "cost_projection" &&
            `projected ~${prog.projected_agent_runs} suite executions`}
          {prog?.event === "propose" &&
            `round ${prog.round}: targeting ${(prog.failing_criteria || []).join(", ") || "—"}`}
          {prog?.event === "candidate" &&
            `round ${prog.round} · candidate ${prog.index}: ${prog.accepted ? "✓ accepted" : "✗ rejected"} — ${prog.reason}`}
          {prog?.event === "round_done" && `round ${prog.round} done`}
          {!prog && "starting…"}
        </p>
      </div>
    );
  }
  if (run.status === "failed") {
    return <div className="policy-box"><div className="policy-title"
              style={{ color: "var(--fail)" }}>failed</div>
      <p style={{ color: "var(--muted)" }}>{run.error}</p></div>;
  }
  if (!art) return null;
  const gap = art.overfit_gap;
  const trainGain = art.best_train_rate - art.baseline_train_rate;
  const heldGain = art.best_heldout_rate == null || art.baseline_heldout_rate == null
    ? null : art.best_heldout_rate - art.baseline_heldout_rate;

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="policy-box">
        <div className="policy-title">
          {art.improved
            ? <span style={{ color: "var(--ok)" }}>improved — adopted {art.best_version} edit(s)</span>
            : <span style={{ color: "var(--muted)" }}>no improvement found</span>}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10,
                      marginTop: 6 }}>
          <Metric label="train (optimized on)"
                  value={`${pct(art.baseline_train_rate)} → ${pct(art.best_train_rate)}`}
                  sub={signedPct(trainGain)} subColor="var(--accent)" />
          <Metric label="held-out (never seen)"
                  value={art.best_heldout_rate == null ? "—"
                    : `${pct(art.baseline_heldout_rate)} → ${pct(art.best_heldout_rate)}`}
                  sub={signedPct(heldGain)} subColor="var(--info)" />
          <Metric label="overfit gap" value={signedPct(gap)}
                  sub={gap == null ? "no held-out"
                    : gap > 0.15 ? "gains likely overfit" : "generalizes"}
                  subColor={overfitColor(gap)} />
        </div>
        <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 8 }}>
          {art.n_train} train / {art.n_heldout} held-out cases · {art.n_agent_runs} suite
          executions · ${Number(art.total_cost_usd).toFixed(4)} · {art.methodology}
        </div>
      </div>

      {art.lineage?.length > 1 && (
        <div className="policy-box">
          <div className="policy-title">score per prompt version</div>
          <ScoreCurve lineage={art.lineage} />
          <div style={{ display: "flex", gap: 16, fontSize: 11, color: "var(--muted)" }}>
            <span><span style={{ color: "var(--accent)" }}>━</span> train</span>
            <span><span style={{ color: "var(--info)" }}>┅</span> held-out</span>
          </div>
        </div>
      )}

      <div className="policy-box">
        <div className="policy-title">baseline → best prompt</div>
        <PromptDiff before={art.baseline_prompt} after={art.best_prompt} />
      </div>

      <div className="policy-box">
        <div className="policy-title">rounds — why each candidate was kept or rejected</div>
        {art.rounds.map((r: any) => (
          <div key={r.round} style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>
              round {r.round} · baseline {pct(r.baseline_train_rate)} · targeting{" "}
              {(r.failing_criteria || []).join(", ") || "—"}
            </div>
            {r.candidates.map((c: any) => (
              <div key={c.index} style={{ fontSize: 12, marginTop: 4, paddingLeft: 10,
                borderLeft: `2px solid ${c.accepted ? "var(--ok)" : "var(--border)"}` }}>
                <span style={{ color: c.accepted ? "var(--ok)" : "var(--muted)" }}>
                  {c.accepted ? "✓ accepted" : "✗ rejected"}
                </span>{" "}
                <span style={{ color: "var(--muted)" }}>({signedPct(c.success_delta)} train) — {c.reason}</span>
                {c.regressions?.length > 0 && (
                  <span style={{ color: "var(--fail)" }}>
                    {" "}regressed: {c.regressions.map((x: any) => x.criterion_id).join(", ")}
                  </span>
                )}
              </div>
            ))}
            {!r.candidates.length && <div style={{ fontSize: 12, color: "var(--muted)",
              paddingLeft: 10 }}>no candidates (nothing left to fix)</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value, sub, subColor }: {
  label: string; value: string; sub?: string; subColor?: string;
}) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700 }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: subColor ?? "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

/** Prompt-optimizer console: launch a self-improving system-prompt run and watch
 * the baseline→best lineage, the per-round accept/reject reasoning, and the
 * train-vs-held-out overfitting check. */
export function OptimizePage() {
  const [suites, setSuites] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<number | null>(null);

  const refreshRuns = () =>
    api.listOptimizeRuns().then((r) => setRuns(r.runs)).catch(() => {});

  useEffect(() => {
    Promise.all([api.listSuites().catch(() => []), refreshRuns()])
      .then(([s]) => setSuites(s as any[]))
      .finally(() => setLoading(false));
  }, []);

  // poll the selected run until it settles
  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    let alive = true;
    const tick = async () => {
      try {
        const run = await api.getOptimizeRun(selected);
        if (!alive) return;
        setDetail(run);
        if (run.status === "running") {
          pollRef.current = window.setTimeout(tick, 1200);
        } else { refreshRuns(); }
      } catch { /* ignore */ }
    };
    tick();
    return () => { alive = false; if (pollRef.current) clearTimeout(pollRef.current); };
  }, [selected]);

  if (loading) return <Skeleton rows={6} />;

  return (
    <div>
      <PageHeader title="Prompt optimizer"
        subtitle={<>Self-improving system prompt — frozen model, suite score as
          reward (<Term name="opro">OPRO/ProTeGi</Term> reflection)</>} />
      <div style={{ display: "grid", gridTemplateColumns: "minmax(340px, 1fr) 1.4fr",
                    gap: 16, alignItems: "start" }}>
        <div style={{ display: "grid", gap: 14 }}>
          <StartForm suites={suites} onStarted={(id) => { setSelected(id); refreshRuns(); }} />
          <div className="policy-box">
            <div className="policy-title">runs</div>
            {!runs.length && <EmptyState icon="✨" title="No optimization runs yet"
              hint="Optimization rewrites your system prompt to lift suite score without touching the model — pick an approved suite and a baseline prompt above to start." />}
            {runs.map((r) => (
              <button key={r.run_id} onClick={() => setSelected(r.run_id)}
                className={selected === r.run_id ? "active" : ""}
                style={{ display: "block", width: "100%", textAlign: "left",
                         marginBottom: 6 }}>
                <span className="mono" style={{ fontSize: 12 }}>{r.run_id}</span>
                <span style={{ float: "right", fontSize: 11,
                  color: r.status === "succeeded" ? "var(--ok)"
                       : r.status === "failed" ? "var(--fail)" : "var(--wait)" }}>
                  {r.status}
                </span>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>
                  {r.suite_id}
                  {r.status === "succeeded" &&
                    ` · train ${signedPct((r.best_train_rate ?? 0) - (r.baseline_train_rate ?? 0))}` +
                    (r.overfit_gap != null ? ` · gap ${signedPct(r.overfit_gap)}` : "")}
                </div>
              </button>
            ))}
          </div>
        </div>
        <div>
          {!selected && <EmptyState icon="◌" title="Select or start a run"
            hint="The baseline→best prompt diff, score curve and accept/reject reasons appear here." />}
          {selected && detail && <RunDetail run={detail} />}
        </div>
      </div>
    </div>
  );
}
