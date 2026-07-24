import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, downloadBlob } from "../api";
import { EmptyState, PageHeader, Skeleton, Uncertainty } from "../components/ui";
import { Markdown } from "../components/Markdown";
import { money } from "../stats";
import { PASS_MEANING } from "../workflow/templates";
import { CoverageCell, ScopeChip, type CoverageSummary } from "../verification";

interface Row {
  scorecard_id: string;
  agent_id: string;
  suite_id: string;
  suite_version: number;
  task_success_rate: number | null;
  mean_cost_usd: number | null;
  total_cost_usd?: number | null;
  total_scoring_cost_usd?: number | null;
  n_runs?: number;
  n_errored?: number;
  n_scored?: number;      // exact scored-case count (backend)
  n_passed?: number;      // exact passing-case count (backend)
  visibility_tier?: string;
  /** Compact verification summary shipped with every row (server store
   *  `_coverage_summary`) — so history can show the SCOPE of each rate
   *  without fetching the whole scorecard. */
  coverage?: CoverageSummary;
  cached?: boolean;
  created_at: string;
}

/** Results history — every past scorecard for the tenant, re-openable without
 *  re-running. A ♻ badge marks results that are cached (an identical re-run is
 *  served for free). */
export function ResultsHistoryPage() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [report, setReport] = useState<{ id: string; text: string } | null>(null);

  useEffect(() => {
    api.listScorecards().then((r) => setRows(r as Row[])).catch(() => setRows([]));
  }, []);

  const open = (id: string) =>
    api.scorecardReport(id)
      .then((text) => setReport({ id, text }))
      .catch(() => setReport({
        id, text: "⚠ Could not load this report. Please try again.",
      }));

  const total = (rows ?? []).reduce(
    (a, r) => a + (r.total_cost_usd ?? 0) + (r.total_scoring_cost_usd ?? 0), 0);

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Results"
          subtitle={
            <>Every result you've recorded. Each row leads with what was verified —
            coverage closure and whether the properties held — and shows the pass
            rate beside it, tagged with the scope it was measured in. To see
            what's wrong with a run, open its <Link to="/app/issues">Issues report</Link>.{" "}
            <span className="mono">♻</span> marks cached results: an identical re-run
            is served for free (no agent or judge calls).</>
          }
        />
        {rows === null ? (
          <Skeleton rows={6} />
        ) : rows.length === 0 ? (
          <EmptyState icon="📊" title="No results yet"
                      hint="Run a test (guided flow, quickstart, or the REST API) — results land here." />
        ) : (
          <>
            <p className="muted-sm" style={{ marginBottom: 10 }}>
              {rows.length} result{rows.length === 1 ? "" : "s"} · total spend ${total.toFixed(4)}
            </p>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>result</th><th>agent</th><th>suite</th>
                    <th title="How much of the situation space these runs reached, and whether the properties held throughout.">
                      verification</th>
                    <th className="num" title={PASS_MEANING}>pass rate</th><th className="num">cost</th>
                    <th>when</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const cost = (r.total_cost_usd ?? 0) + (r.total_scoring_cost_usd ?? 0);
                    // Prefer the backend's EXACT scored/passed counts; fall back to
                    // (runs − errored) only for older payloads without them.
                    const nScored = r.n_scored ?? Math.max(0, (r.n_runs ?? 0) - (r.n_errored ?? 0));
                    return (
                      <tr key={r.scorecard_id}>
                        <td className="mono">
                          {r.scorecard_id}
                          {r.cached && (
                            <span className="pill" title="Cached — identical re-runs are free"
                                  style={{ marginLeft: 6 }}>♻ cached</span>
                          )}
                        </td>
                        <td>{r.agent_id}</td>
                        <td className="mono">{r.suite_id} v{r.suite_version}</td>
                        <td><CoverageCell sc={r} /></td>
                        <td className="num">
                          {r.task_success_rate == null
                            ? <span className="muted-sm">—</span>
                            : <>{Math.round(r.task_success_rate * 100)}%
                                <ScopeChip sc={r} />
                                {nScored > 0 && (
                                  <div className="cell-ci">
                                    <Uncertainty passes={r.n_passed} rate={r.task_success_rate} n={nScored} />
                                  </div>
                                )}</>}
                        </td>
                        <td className="num">{money(cost)}</td>
                        <td>{new Date(r.created_at).toLocaleString()}</td>
                        <td>
                          <button onClick={() => open(r.scorecard_id)}>report</button>
                          <button style={{ marginLeft: 6 }} title="Download as PDF"
                                  onClick={() => api.scorecardPdf(r.scorecard_id)
                                    .then((b) => downloadBlob(b, `scorecard-${r.scorecard_id}.pdf`))
                                    .catch(() => {})}>⤓ PDF</button>
                          <Link className="btn-cell" style={{ marginLeft: 6 }}
                                title="Issue a safety certificate from this scorecard"
                                to={`/app/certifications?scorecard=${encodeURIComponent(r.scorecard_id)}`}>
                            🏅 Certify
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}

        {report && (
          <div style={{ marginTop: 18 }}>
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              Report · <span className="mono">{report.id}</span>
              <button className="ghost-sm" style={{ marginLeft: 8 }}
                      onClick={() => setReport(null)}>close</button>
            </div>
            <Markdown>{report.text}</Markdown>
          </div>
        )}
      </div>
    </div>
  );
}
