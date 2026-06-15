import { useEffect, useState } from "react";
import { api } from "../api";

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
  const [board, setBoard] = useState<any | null>(null);
  const [filter, setFilter] = useState<string[]>([]);
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>(
    { key: "index", dir: -1 });

  const load = (suites: string[]) =>
    api.leaderboard(suites).then(setBoard).catch(() => setBoard(null));
  useEffect(() => { load(filter); }, [filter]);

  if (!board) return <div className="page"><div className="list-page">…</div></div>;
  const { suites } = board;

  const toggle = (s: string) =>
    setFilter((f) => f.includes(s) ? f.filter((x) => x !== s) : [...f, s]);

  const sortBy = (key: string, def: 1 | -1) =>
    setSort((cur) => cur.key === key
      ? { key, dir: (cur.dir * -1) as 1 | -1 }   // toggle direction
      : { key, dir: def });

  const agents = [...board.agents].sort((a: any, b: any) => {
    const va = a[sort.key], vb = b[sort.key];
    const cmp = typeof va === "string"
      ? String(va).localeCompare(String(vb))
      : (va ?? 0) - (vb ?? 0);
    return cmp * sort.dir;
  });

  return (
    <div className="page">
      <div className="list-page">
        <h2>Agenttic Index</h2>
        <p style={{ color: "var(--muted)", marginTop: -6 }}>
          Composite score per agent — weighted mean of per-suite task success
          (0–100), latest run per suite. Cost and latency are blended across
          suites; coverage shows how many suites each agent has run.
        </p>

        {suites.length > 1 && (
          <div style={{ margin: "10px 0" }}>
            <span style={{ color: "var(--muted)", marginRight: 8 }}>
              common set:
            </span>
            {suites.map((s: string) => (
              <button key={s}
                      className={filter.includes(s) || !filter.length ? "active" : ""}
                      style={{ marginRight: 6 }}
                      onClick={() => toggle(s)}>{s}</button>
            ))}
            {filter.length > 0 && (
              <button onClick={() => setFilter([])}>reset</button>
            )}
          </div>
        )}

        {agents.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>
            No scorecards yet — run a workflow, then agents appear here.
          </p>
        ) : (
          <>
            <Scatter agents={agents} />
            <table className="data" style={{ marginTop: 16 }}>
              <thead>
                <tr>
                  {COLUMNS.map(([key, label, def]) => (
                    <th key={key} className="sortable"
                        onClick={() => sortBy(key, def)}>
                      {label}{sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {agents.map((a: any) => (
                  <tr key={a.agent_id}>
                    <td>{a.rank}</td>
                    <td>{a.agent_id}</td>
                    <td style={a.agent_type === "discovered"
                      ? { color: "var(--muted)" } : undefined}>{a.agent_type}</td>
                    <td><b style={{ color: barColor(a.index) }}>{a.index}</b></td>
                    <td>${a.mean_cost_usd.toFixed(4)}</td>
                    <td title="execution + judge cost per case">
                      {a.all_in_cost_per_case_usd == null
                        ? <span style={{ color: "var(--muted)" }}>n/a</span>
                        : `$${a.all_in_cost_per_case_usd.toFixed(4)}`}</td>
                    <td>{Math.round(a.p95_latency_ms)}</td>
                    <td>{a.coverage}/{a.total_suites}</td>
                    <td>{a.visibility_tier.replace("_", "-")}</td>
                    <td>{a.n_errored || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

function barColor(index: number) {
  if (index >= 70) return "var(--ok)";
  if (index >= 40) return "var(--wait)";
  return "var(--fail)";
}

/** Lightweight Index-vs-cost scatter (no charting dep). Higher + left is better. */
function Scatter({ agents }: { agents: any[] }) {
  const W = 560, H = 240, pad = 40;
  const costs = agents.map((a) => a.mean_cost_usd);
  const maxCost = Math.max(...costs, 0.0001) * 1.1;
  const x = (c: number) => pad + (c / maxCost) * (W - 2 * pad);
  const y = (idx: number) => H - pad - (idx / 100) * (H - 2 * pad);
  return (
    <svg width={W} height={H} style={{
      background: "var(--panel)", border: "1px solid var(--border)",
      borderRadius: 8 }}>
      {[0, 25, 50, 75, 100].map((g) => (
        <g key={g}>
          <line x1={pad} x2={W - pad} y1={y(g)} y2={y(g)} stroke="var(--border)" />
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
