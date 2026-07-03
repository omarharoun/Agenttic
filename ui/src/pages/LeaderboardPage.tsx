import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EmptyState, PageHeader, Skeleton, Uncertainty } from "../components/ui";

/** Standard benchmarking — canonical, literature-anchored metrics rolled into
 *  the normalized Agenttic Index (the "Artificial Analysis for agents" spine). */
const COMPONENT_COLS: [string, string][] = [
  ["tool_call_accuracy", "Tool-call acc"],
  ["harmful_refusal_rate", "Refusal rate"],
  ["injection_robustness", "Injection robust"],
  ["reliability_pass_k", "pass^k"],
  ["calibration_ece", "Calibration"],
];

/** Score (0–100) → semantic colour, shared by the index bar + scatter. */
function barColor(index: number) {
  if (index >= 70) return "var(--ok)";
  if (index >= 40) return "var(--wait)";
  return "var(--fail)";
}

/** Compact horizontal score bar used in the index column. */
function IndexBar({ value, small }: { value: number; small?: boolean }) {
  const c = barColor(value);
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="idx-cell" title={`Agenttic Index ${value}`}>
      <span className="idx-track">
        <span className="idx-fill" style={{ width: `${pct}%`, background: c }} />
      </span>
      <b className={`idx-val${small ? " sm" : ""}`} style={{ color: c }}>{value}</b>
    </div>
  );
}

/** A component score (0–1) rendered as a percentage with a micro-bar. */
function ComponentCell({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="muted-sm">—</span>;
  const pct = Math.round(value * 100);
  return (
    <div className="comp-cell">
      <span className="comp-val">{pct}%</span>
      <span className="comp-track"><span className="comp-fill" style={{ width: `${pct}%` }} /></span>
    </div>
  );
}

function StandardBenchmarks() {
  const [cat, setCat] = useState<any | null>(null);
  const [board, setBoard] = useState<any | null | undefined>(undefined);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [showMethod, setShowMethod] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const load = () => {
    api.standardMetrics().then(setCat).catch(() => setCat(null));
    api.standardLeaderboard().then(setBoard).catch(() => setBoard(null));
    api.standardDatasets().then((d) => setDatasets(d.datasets ?? [])).catch(() => setDatasets([]));
  };
  useEffect(load, []);

  const seed = async () => {
    setBusy("seed");
    try { await api.seedStandard(); load(); } finally { setBusy(""); }
  };
  const ingest = async (id: string) => {
    setBusy("ingest-" + id); setMsg(null);
    try { const r = await api.ingestDataset(id); setMsg({ kind: "ok", text: `Ingested ${id} (${r.ingested ?? 0} cases).` }); load(); }
    catch (e: any) { setMsg({ kind: "err", text: `Ingest failed: ${String(e.message ?? e)}` }); }
    finally { setBusy(""); }
  };
  const runBench = async () => {
    setBusy("run"); setMsg(null);
    try { const r = await api.runStandard({ k: 3 }); setMsg({ kind: "ok", text: r.note || "Standard run started." }); }
    catch (e: any) {
      const d = e?.message ?? e;
      setMsg(String(d).includes("Anthropic API key")
        ? { kind: "err", text: "Add your Anthropic API key in Settings to run the standard benchmarks." }
        : { kind: "err", text: `Could not start: ${String(d)}` });
    } finally { setBusy(""); }
  };

  const agents: any[] = board?.agents ?? [];
  const present = new Set<string>(agents.flatMap((a) => Object.keys(a.components ?? {})));
  const cols = COMPONENT_COLS.filter(([k]) => present.has(k));

  return (
    <div style={{ marginBottom: 36 }}>
      <header className="std-hero">
        <span className="eyebrow">Standard benchmarks</span>
        <h2>
          Agenttic Index
          {agents.length > 0 && <span className="pill-count">{agents.length} agent{agents.length === 1 ? "" : "s"} ranked</span>}
        </h2>
        <p style={{ color: "var(--muted)", margin: "6px 0 0", maxWidth: 760 }}>
          Canonical, literature-anchored metrics on agenttic's own seed data,
          normalized into one Agenttic Index — components always shown. Each Index
          carries its sample size <span className="mono">n</span> and a{" "}
          <b>Wilson 95% interval</b> (<span className="mono">*</span> = the composite
          treated as a pass rate over <span className="mono">n</span> cases). We implement
          the published <i>methodology</i>; these are <b>not</b> the public
          BFCL / τ-bench / AgentHarm datasets (direct dataset comparability is a
          next phase).{" "}
          <button className="ghost-sm" style={{ marginLeft: 4 }}
                  aria-expanded={showMethod}
                  onClick={() => setShowMethod((s) => !s)}>
            {showMethod ? "Hide methodology" : "Methodology & weights"}
          </button>
          {" "}
          <Link to="/methodology" style={{ color: "var(--accent)", fontWeight: 600, marginLeft: 6 }}>
            Full methodology →
          </Link>
          {" "}
          <Link to="/app/certifications" style={{ color: "var(--accent)", fontWeight: 600, marginLeft: 6 }}>
            🏅 Certify an agent →
          </Link>
        </p>
      </header>

      {showMethod && cat && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-body" style={{ padding: 0 }}>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Metric</th><th>Implements</th><th className="num">Index weight</th></tr></thead>
                <tbody>
                  {cat.metrics.map((m: any) => (
                    <tr key={m.id}>
                      <td><b>{m.name}</b></td>
                      <td style={{ color: "var(--muted)", maxWidth: 520 }}>{m.methodology}</td>
                      <td className="num">{m.status === "deferred"
                        ? <span className="muted-sm">deferred</span>
                        : `${Math.round(m.weight * 100)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* dataset provenance — license + source for each canonical suite */}
      {datasets.length > 0 && (
        <>
          <div className="eyebrow" style={{ margin: "4px 0 2px" }}>Datasets</div>
          <div className="dataset-grid">
            {datasets.map((d) => (
              <div key={d.dataset_id} className="dataset-card">
                <div className="dc-top">
                  <span className="dc-name">{d.name}</span>
                  <span className="dc-meta-row">
                    {d.gated && (
                      <span className="dc-gated" title="Access-gated upstream — bring your own access; a vendored sample is ingested offline">
                        <span aria-hidden="true">🔒</span> Gated
                      </span>
                    )}
                    {d.license && <span className="dc-lic" title="License">{d.license}</span>}
                  </span>
                </div>
                {d.citation && <div className="dc-meta">{d.citation}</div>}
                {d.caveat && (
                  <div className="dc-caveat">
                    <span className="ic" aria-hidden="true">⚠</span><span>{d.caveat}</span>
                  </div>
                )}
                <div className="dc-foot">
                  {d.source_url
                    ? <a className="dc-src" href={d.source_url} target="_blank" rel="noreferrer">Source ↗</a>
                    : <span />}
                  {d.present
                    ? <span className="dc-status in"><span className="d" />Ingested</span>
                    : <button className="ghost-sm"
                              disabled={busy === "ingest-" + d.dataset_id}
                              onClick={() => ingest(d.dataset_id)}>
                        {busy === "ingest-" + d.dataset_id ? "Ingesting…" : "Ingest"}
                      </button>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="std-toolbar">
        <button className="primary" disabled={busy === "run"} onClick={runBench}>
          {busy === "run" ? "Starting…" : "▶ Run standard benchmark (k=3)"}
        </button>
      </div>
      {msg && <div className={msg.kind === "ok" ? "note-ok" : "note-err"} style={{ margin: "0 0 14px" }}>{msg.text}</div>}

      {board === undefined ? <Skeleton rows={4} /> : agents.length === 0 ? (
        <EmptyState icon="◇" title="No standard runs yet"
          hint="The Agenttic Index is how buyers compare agents at a glance — install the canonical suites, then run a benchmark to put your agents on it."
          action={<button className="primary" disabled={busy === "seed"} onClick={seed}>
            {busy === "seed" ? "Seeding…" : "Install standard suites"}
          </button>} />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th className="num">#</th><th>Agent</th><th>Agenttic Index</th>
                {cols.map(([, label]) => <th key={label}>{label}</th>)}
                <th className="num">Suites</th></tr>
            </thead>
            <tbody>
              {agents.map((a, i) => (
                <tr key={a.agent_id}>
                  <td className="num">{i + 1}</td>
                  <td>{a.agent_id}</td>
                  <td>
                    <IndexBar value={a.index} />
                    {a.n_cases != null
                      ? <div className="cell-ci"><Uncertainty rate={a.index / 100} n={a.n_cases} approx /></div>
                      : <div className="cell-todo" title="sample size (n_cases) not present on this row — partial index rolled from scorecards">n = —</div>}
                  </td>
                  {cols.map(([k]) => (
                    <td key={k}><ComponentCell value={a.components?.[k]} /></td>
                  ))}
                  <td className="num muted-sm">{(a.suites_run ?? []).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Agenttic Index — ranks agents across suites (artificialanalysis.ai style):
 * a leaderboard table + an Index-vs-cost scatter, with a common-set filter. */
// columns: [key, label, default direction (1 asc / -1 desc), numeric?]
const COLUMNS: [string, string, 1 | -1, boolean][] = [
  ["rank", "#", 1, true],
  ["agent_id", "agent", 1, false],
  ["agent_type", "type", 1, false],
  ["index", "Index", -1, true],
  ["mean_cost_usd", "exec $/case", 1, true],
  ["all_in_cost_per_case_usd", "all-in $/case", 1, true],
  ["p95_latency_ms", "p95 ms", 1, true],
  ["coverage", "coverage", -1, true],
  ["visibility_tier", "tier", 1, false],
  ["n_errored", "errored", 1, true],
];

export function LeaderboardPage() {
  const [board, setBoard] = useState<any | null | undefined>(undefined);
  const [filter, setFilter] = useState<string[]>([]);
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>(
    { key: "index", dir: -1 });

  const load = (suites: string[]) =>
    api.leaderboard(suites).then(setBoard).catch(() => setBoard(null));
  useEffect(() => { load(filter); }, [filter]);

  const toggle = (s: string) =>
    setFilter((f) => f.includes(s) ? f.filter((x) => x !== s) : [...f, s]);

  const sortBy = (key: string, def: 1 | -1) =>
    setSort((cur) => cur.key === key
      ? { key, dir: (cur.dir * -1) as 1 | -1 }   // toggle direction
      : { key, dir: def });

  const agents = board ? [...board.agents].sort((a: any, b: any) => {
    const va = a[sort.key], vb = b[sort.key];
    const cmp = typeof va === "string"
      ? String(va).localeCompare(String(vb))
      : (va ?? 0) - (vb ?? 0);
    return cmp * sort.dir;
  }) : [];

  return (
    <div className="page">
      <div className="list-page">
        <StandardBenchmarks />

        <PageHeader title="All suites — task-success leaderboard"
          subtitle={<>Composite score per agent across <i>all</i> suites (incl. your own) —
            weighted mean of per-suite task success (0–100), latest run per suite.
            Cost and latency are blended across suites; coverage shows how many
            suites each agent has run.</>} />

        {board === undefined ? <Skeleton rows={6} /> : (
          <>
            {board && board.suites.length > 1 && (
              <div style={{ margin: "0 0 12px", display: "flex", alignItems: "center",
                            gap: 6, flexWrap: "wrap" }}>
                <span className="eyebrow" style={{ marginRight: 2 }}>Common set</span>
                {board.suites.map((s: string) => (
                  <button key={s}
                          className={filter.includes(s) || !filter.length ? "active" : ""}
                          onClick={() => toggle(s)}>{s}</button>
                ))}
                {filter.length > 0 && (
                  <button onClick={() => setFilter([])}>reset</button>
                )}
              </div>
            )}

            {agents.length === 0 ? (
              <EmptyState icon="🏆" title="No scorecards yet"
                hint="Run a workflow or a standard benchmark — agents appear here once they have a scored run." />
            ) : (
              <>
                <Scatter agents={agents} />
                <div className="table-wrap" style={{ marginTop: 16 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        {COLUMNS.map(([key, label, def, numeric]) => (
                          <th key={key} className={`sortable${numeric ? " num" : ""}`}
                              tabIndex={0} role="button"
                              aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                              onClick={() => sortBy(key, def)}
                              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(key, def); } }}>
                            {label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {agents.map((a: any) => (
                        <tr key={a.agent_id}>
                          <td className="num">{a.rank}</td>
                          <td>{a.agent_id}</td>
                          <td style={a.agent_type === "discovered"
                            ? { color: "var(--muted)" } : undefined}>{a.agent_type}</td>
                          <td>
                            <IndexBar value={a.index} small />
                            {/* n_scored = total scored cases behind this agent's Index
                                (backend compute_leaderboard). The Index is a weighted mean
                                of per-suite rates, so this CI is an approximation (hence
                                `approx`); per-suite exact intervals live in per_suite. */}
                            {(a.n_scored ?? a.n ?? a.n_cases) != null
                              ? <div className="cell-ci"><Uncertainty rate={a.index / 100} n={a.n_scored ?? a.n ?? a.n_cases} approx /></div>
                              : <div className="cell-todo" title="per-agent case count not in payload yet — coverage shown as sample proxy">{a.coverage}/{a.total_suites} suites</div>}
                          </td>
                          <td className="num">${a.mean_cost_usd.toFixed(4)}</td>
                          <td className="num" title="execution + judge cost per case">
                            {a.all_in_cost_per_case_usd == null
                              ? <span style={{ color: "var(--muted)" }}>n/a</span>
                              : `$${a.all_in_cost_per_case_usd.toFixed(4)}`}</td>
                          <td className="num">{Math.round(a.p95_latency_ms)}</td>
                          <td className="num">{a.coverage}/{a.total_suites}</td>
                          <td>{a.visibility_tier.replace("_", "-")}</td>
                          <td className="num">{a.n_errored || ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Lightweight Index-vs-cost scatter (no charting dep). Higher + left is better. */
function Scatter({ agents }: { agents: any[] }) {
  const W = 560, H = 240, pad = 40;
  const costs = agents.map((a) => a.mean_cost_usd);
  const maxCost = Math.max(...costs, 0.0001) * 1.1;
  const x = (c: number) => pad + (c / maxCost) * (W - 2 * pad);
  const y = (idx: number) => H - pad - (idx / 100) * (H - 2 * pad);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, height: "auto",
      background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 8 }}
      role="img" aria-label="Scatter plot of Agenttic Index versus mean cost per case; up and to the left is better.">
      {[0, 25, 50, 75, 100].map((g) => (
        <g key={g}>
          <line x1={pad} x2={W - pad} y1={y(g)} y2={y(g)} stroke="var(--viz-grid)" />
          <text x={6} y={y(g) + 4} fill="var(--muted)" fontSize="10">{g}</text>
        </g>
      ))}
      <text x={W / 2} y={H - 6} fill="var(--muted)" fontSize="10"
            textAnchor="middle">mean cost / case →</text>
      <text x={12} y={16} fill="var(--muted)" fontSize="10">Index ↑</text>
      {agents.map((a) => (
        <g key={a.agent_id}>
          <circle cx={x(a.mean_cost_usd)} cy={y(a.index)} r={6}
                  fill={barColor(a.index)} opacity={0.85} />
          <text x={x(a.mean_cost_usd) + 9} y={y(a.index) + 4}
                fill="var(--text)" fontSize="11">{a.agent_id}</text>
        </g>
      ))}
    </svg>
  );
}
