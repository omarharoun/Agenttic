import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EmptyState, PageHeader, Skeleton, Uncertainty } from "../components/ui";
import { Onboarding } from "../components/Onboarding";
import { money } from "../stats";
import { CoverageCell, ScopeChip, cov, hasVerification, scopeNote } from "../verification";

/* ============================================================================
   Dashboard — the front door of the console.

   A leaderboard snapshot, recent results, and the live state of the workspace.

   SPEC-13: this screen is the most-visited surface in the console, so it is the
   one most able to undo the run view's reframing. A dashboard whose "recent
   results" column is a bare percentage teaches the reader that the percentage is
   the answer. So the recent-results table leads with VERIFICATION — coverage
   closure and whether the properties held — and shows the pass rate beside it,
   carrying the scope it was measured in.
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

  // Verification headlines, counted across the workspace. These are the two
  // facts a pass-rate dashboard cannot show you: results that broke a property
  // (a failure regardless of score), and results carrying no coverage model at
  // all — whose rate is an unscoped claim.
  const withBrokenProps = (results ?? []).filter(
    (r) => (cov(r).assertions?.violations ?? 0) > 0).length;
  const unscoped = (results ?? []).filter(
    (r) => hasVerification(r) && !cov(r).model_ref).length;

  const loading = board === undefined || results === null || execs === null;
  const empty = !loading && agents.length === 0 && (results ?? []).length === 0;

  return (
    <div className="page">
      <div className="list-page">
        <Onboarding />
        <PageHeader
          title="Dashboard"
          subtitle={<>What held, what broke, and what nothing has looked at yet.
            Results lead with coverage closure and property outcomes; the pass rate
            sits beside them carrying the scope it was measured in. Every rate
            carries its sample size and a Wilson 95% interval.</>}
          actions={
            <div className="dash-cta">
              <Link className="btn-primary" to="/app/build">＋ New evaluation</Link>
              <Link className="btn-ghost" to="/app/issues">🔎 Find issues</Link>
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
              <Stat label="properties broken"
                    value={<span className={withBrokenProps ? "err" : "ok"}>{withBrokenProps}</span>}
                    hint="Results where a property was violated. A violation is a failure regardless of the score." />
              <Stat label="unscoped results"
                    value={<span className={unscoped ? "wait" : "ok"}>{unscoped}</span>}
                    hint="Results with no coverage model applied — their pass rate says nothing about what the suite never exercised." />
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
                      No ranked agents yet. A ranking compares agents on the share
                      of written cases each one passed — run the standard benchmark
                      to populate the Agenttic Index. What an agent was never put
                      through stays a per-result question.
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
                  <Link className="ghost-sm" to="/app/issues">What's wrong →</Link>
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
                    <thead>
                      <tr>
                        <th>agent</th>
                        <th title="How much of the situation space these runs reached, and whether the properties held throughout.">
                          verification
                        </th>
                        <th className="num"
                            title="The share of written cases that passed. It says nothing about the cases nobody wrote.">
                          pass rate
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {recent.map((r) => {
                        const nScored = Math.max(0, (r.n_runs ?? 0) - (r.n_errored ?? 0));
                        return (
                          <tr key={r.scorecard_id}>
                            <td>
                              {r.agent_id}
                              <div className="muted-sm mono">{r.suite_id}</div>
                            </td>
                            <td><CoverageCell sc={r} /></td>
                            <td className="num">
                              {r.task_success_rate == null
                                ? <span className="muted-sm">—</span>
                                : <>
                                    <span className="dash-rate">
                                      {Math.round(r.task_success_rate * 100)}%
                                    </span>
                                    <ScopeChip sc={r} />
                                    {nScored > 0 && (
                                      <div className="cell-ci">
                                        <Uncertainty rate={r.task_success_rate} n={nScored} />
                                      </div>
                                    )}
                                  </>}
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
