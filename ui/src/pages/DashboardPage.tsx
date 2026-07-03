import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EmptyState, PageHeader, Skeleton, Uncertainty } from "../components/ui";
import { Onboarding } from "../components/Onboarding";
import { money } from "../stats";

/* ============================================================================
   Dashboard — the front door of the console.

   A benchmark *authority* should open on the numbers, not on a blank workflow
   canvas. This is the "one excellent dashboard" the roadmap's Phase 2 calls for:
   a leaderboard snapshot, recent results (with their uncertainty), and the live
   state of the workspace — all in the existing editorial design system.
   ========================================================================== */

function barColor(index: number): string {
  if (index >= 70) return "var(--ok)";
  if (index >= 40) return "var(--wait)";
  return "var(--fail)";
}

function IndexPill({ value }: { value: number }) {
  const c = barColor(value);
  const w = Math.max(0, Math.min(100, value));
  return (
    <div className="dash-idx" title={`Agenttic Index ${value}`}>
      <span className="dash-idx-track">
        <span className="dash-idx-fill" style={{ width: `${w}%`, background: c }} />
      </span>
      <b style={{ color: c }}>{value}</b>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="dash-stat" title={hint}>
      <div className="dash-stat-val">{value}</div>
      <div className="dash-stat-lab">{label}</div>
    </div>
  );
}

export function DashboardPage() {
  const [board, setBoard] = useState<any | null | undefined>(undefined);
  const [results, setResults] = useState<any[] | null>(null);
  const [execs, setExecs] = useState<any[] | null>(null);

  useEffect(() => {
    api.standardLeaderboard().then(setBoard).catch(() => setBoard(null));
    api.listScorecards().then((r) => setResults(r as any[])).catch(() => setResults([]));
    api.listExecutions().then((r) => setExecs(r)).catch(() => setExecs([]));
  }, []);

  const agents: any[] = board?.agents ?? [];
  const topAgents = agents.slice(0, 5);
  const recent = (results ?? []).slice(0, 6);
  const running = (execs ?? []).filter(
    (r) => ["running", "waiting_approval"].includes(r.status)).length;
  const totalSpend = (results ?? []).reduce(
    (a, r) => a + (r.total_cost_usd ?? 0) + (r.total_scoring_cost_usd ?? 0), 0);

  const loading = board === undefined || results === null || execs === null;
  const empty = !loading && agents.length === 0 && (results ?? []).length === 0;

  return (
    <div className="page">
      <div className="list-page">
        <Onboarding />
        <PageHeader
          title="Dashboard"
          subtitle={<>The credibility surface — agents ranked on the Agenttic Index,
            your latest results with their confidence intervals, and what's running
            now. Every headline number carries its sample size and a Wilson 95% interval.</>}
          actions={
            <div className="dash-cta">
              <Link className="btn-primary" to="/app/build">＋ New evaluation</Link>
              <a href="/scan" className="btn-ghost">Scan an agent</a>
            </div>
          }
        />

        {loading ? <Skeleton rows={6} /> : empty ? (
          <EmptyState icon="◇" title="No results yet"
            hint={<>Run a standard benchmark or a guided evaluation, and this
              dashboard fills with a ranked leaderboard and your scored results.</>}
            action={
              <div className="dash-cta">
                <Link className="btn-primary" to="/app/build">Start an evaluation</Link>
                <Link className="btn-ghost" to="/app/leaderboard">Open the leaderboard</Link>
              </div>
            } />
        ) : (
          <>
            <div className="dash-stats">
              <Stat label="agents ranked" value={agents.length}
                    hint="Agents with a canonical Agenttic Index" />
              <Stat label="results recorded" value={(results ?? []).length}
                    hint="Scorecards across this workspace" />
              <Stat label="running now" value={running}
                    hint="Executions in progress or awaiting approval" />
              <Stat label="total spend" value={money(totalSpend)}
                    hint="Agent + judge cost across all results" />
            </div>

            <div className="dash-grid">
              {/* leaderboard snapshot */}
              <section className="dash-card">
                <header className="dash-card-head">
                  <h2>Agenttic Index — top agents</h2>
                  <Link className="ghost-sm" to="/app/leaderboard">Full leaderboard →</Link>
                </header>
                {topAgents.length === 0 ? (
                  <div className="dash-card-empty">
                    <p className="muted-sm">
                      No ranked agents yet. A ranking is how you compare agents and
                      show which is safer — run the standard benchmark to populate
                      the Agenttic Index.
                    </p>
                    <Link className="btn-primary" to="/app/leaderboard">Run standard benchmark</Link>
                  </div>
                ) : (
                  <table className="data">
                    <thead><tr><th className="num">#</th><th>agent</th><th>Index</th></tr></thead>
                    <tbody>
                      {topAgents.map((a, i) => (
                        <tr key={a.agent_id}>
                          <td className="num">{i + 1}</td>
                          <td>{a.agent_id}</td>
                          <td>
                            <IndexPill value={a.index} />
                            {a.n_cases != null && (
                              <div className="cell-ci">
                                <Uncertainty rate={a.index / 100} n={a.n_cases} approx />
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              {/* recent results */}
              <section className="dash-card">
                <header className="dash-card-head">
                  <h2>Recent results</h2>
                  <Link className="ghost-sm" to="/app/results">All results →</Link>
                </header>
                {recent.length === 0 ? (
                  <div className="dash-card-empty">
                    <p className="muted-sm">
                      No results yet. A scored run is what earns a grade and a
                      certificate you can publish — a guided evaluation lands here.
                    </p>
                    <Link className="btn-primary" to="/app/build">New evaluation</Link>
                  </div>
                ) : (
                  <table className="data">
                    <thead><tr><th>agent</th><th>suite</th><th className="num">success</th></tr></thead>
                    <tbody>
                      {recent.map((r) => {
                        const nScored = Math.max(0, (r.n_runs ?? 0) - (r.n_errored ?? 0));
                        return (
                          <tr key={r.scorecard_id}>
                            <td>{r.agent_id}</td>
                            <td className="mono">{r.suite_id}</td>
                            <td className="num">
                              {r.task_success_rate == null
                                ? <span className="muted-sm">—</span>
                                : <>{Math.round(r.task_success_rate * 100)}%
                                    {nScored > 0 && (
                                      <div className="cell-ci">
                                        <Uncertainty rate={r.task_success_rate} n={nScored} />
                                      </div>
                                    )}</>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </section>
            </div>

            {board?.note && (
              <p className="muted-sm dash-note">◇ {board.note}</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
