import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api, downloadBlob, errMessage,
  type Scorecard, type ScorecardSummary, type RunScore, type CriterionScore,
} from "../api";
import { pct, money, ms } from "../stats";
import { EmptyState, PageHeader, Skeleton, Uncertainty } from "../components/ui";
import { PageData } from "../components/PageData";
import {
  IconResults, IconDownload, IconDoc, IconChevronRight, IconChevronDown,
  IconCheck, IconClose, IconError, IconExternal, IconIssues, IconLock, IconShield,
} from "../icons";
import "./ScorecardDetail.css";

/* ============================================================================
   SPEC-4 Step 19.3 — Scorecard detail.

   The scorecard is the ledger a client reads over the operator's shoulder, so
   every number here carries its units and its uncertainty, and every claim is
   traceable back to a run. Four registers:

     1. Executive header — success rate + Wilson 95% interval, cost (mean + all-in
        incl. scoring), p95 latency, and the visibility tier that governs how much
        of the evidence a reader is allowed to see.
     2. Criterion breakdown — the per-criterion mean, each tagged calibrated or
        PROVISIONAL. A judge criterion whose scores aren't calibrated is provisional:
        the number is real but the judge hasn't been proven to agree with humans.
     3. Per-case table — one row per run, pass / fail / errored, expandable to the
        per-criterion judge rationales, each linking to its trace.
     4. Regression diff — the same (agent, suite) scored before? Show the delta in
        success rate and per-criterion means vs the immediately-prior scorecard.

   Plus the honest exports (the same Markdown / PDF the CLI produces) and deep
   links from failing criteria into the Issues report.
   ========================================================================== */

const VISIBILITY_META: Record<string, { label: string; blurb: string }> = {
  glass_box: {
    label: "Glass box",
    blurb: "Full trace visibility — every span, tool call and rationale is on the record.",
  },
  black_box: {
    label: "Black box",
    blurb: "Input/output only — internal steps aren't recorded, so some evidence is unavailable.",
  },
};

/** A criterion is provisional when it was scored by a judge that hasn't been
 *  calibrated against human labels. Code/`fi` scorers are deterministic — always
 *  trustworthy. We inspect the per-case CriterionScore rows to decide. */
type CriterionStanding = { calibrated: boolean; provisional: boolean; scorer: string };

function standingFor(criterionId: string, runs: RunScore[]): CriterionStanding {
  let sawJudge = false;
  let anyUncalibratedJudge = false;
  let scorer = "";
  for (const run of runs) {
    for (const cs of run.criterion_scores) {
      if (cs.criterion_id !== criterionId) continue;
      scorer = cs.scorer;
      if (cs.scorer === "judge") {
        sawJudge = true;
        if (!cs.calibrated) anyUncalibratedJudge = true;
      }
    }
  }
  // Judge-scored + any uncalibrated sample → provisional. Otherwise calibrated.
  const provisional = sawJudge && anyUncalibratedJudge;
  return { calibrated: !provisional, provisional, scorer };
}

function CalibrationBadge({ standing }: { standing: CriterionStanding }) {
  if (standing.provisional) {
    return (
      <span className="sd-badge sd-badge-provisional"
            title="Judge-scored but not yet calibrated against human labels — the number is real, but unproven.">
        PROVISIONAL
      </span>
    );
  }
  return (
    <span className="sd-badge sd-badge-calibrated"
          title={standing.scorer === "judge"
            ? "Judge scores agree with human labels above threshold."
            : "Deterministic scorer — no calibration needed."}>
      calibrated
    </span>
  );
}

function CaseVerdict({ run }: { run: RunScore }) {
  if (run.scoring_error) {
    return <span className="sd-verdict sd-verdict-error"><IconError size={13} /> errored</span>;
  }
  if (run.passed) {
    return <span className="sd-verdict sd-verdict-pass"><IconCheck size={13} /> pass</span>;
  }
  return <span className="sd-verdict sd-verdict-fail"><IconClose size={13} /> fail</span>;
}

/** One expandable per-case row + its detail row. Keyboard-operable (Enter/Space)
 *  with aria-expanded, and links the case's trace out. */
function CaseRow({ run }: { run: RunScore }) {
  const [open, setOpen] = useState(false);
  const rowId = `sd-case-${run.trace_id}`;
  const detailId = `${rowId}-detail`;
  const toggle = () => setOpen((o) => !o);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
  };
  return (
    <>
      <tr className="sd-case-row" id={rowId}
          tabIndex={0} role="button" aria-expanded={open} aria-controls={detailId}
          onClick={toggle} onKeyDown={onKey}>
        <td className="sd-case-chev" aria-hidden="true">
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        </td>
        <td className="mono">{run.test_id}</td>
        <td><CaseVerdict run={run} /></td>
        <td className="num">{money(run.cost_usd)}</td>
        <td className="num">{ms(run.latency_ms)}</td>
        <td className="num">{run.steps}</td>
        <td>
          <Link className="sd-tracelink" to={`/app/traces/${encodeURIComponent(run.trace_id)}`}
                onClick={(e) => e.stopPropagation()} title="Open this case's trace">
            <IconExternal size={13} /> trace
          </Link>
        </td>
      </tr>
      {open && (
        <tr className="sd-case-detail" id={detailId}>
          <td colSpan={7}>
            {run.scoring_error && (
              <div className="sd-case-err" role="alert">
                <IconError size={14} /> {run.scoring_error}
              </div>
            )}
            {run.criterion_scores.length === 0 ? (
              <p className="muted-sm">No criterion scores recorded for this case.</p>
            ) : (
              <ul className="sd-crit-list">
                {run.criterion_scores.map((cs: CriterionScore) => (
                  <li key={cs.criterion_id} className="sd-crit-item">
                    <div className="sd-crit-head">
                      <code className="mono">{cs.criterion_id}</code>
                      <span className="sd-crit-score">{cs.score}</span>
                      <span className="sd-crit-scorer muted-sm">{cs.scorer}</span>
                      {cs.scorer === "judge" && (
                        <CalibrationBadge standing={{
                          calibrated: cs.calibrated, provisional: !cs.calibrated, scorer: "judge",
                        }} />
                      )}
                    </div>
                    {cs.judge_rationale && (
                      <p className="sd-rationale">{cs.judge_rationale}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

/** A signed delta, e.g. "+12%" / "−3%", coloured by direction. */
function DeltaPct({ delta }: { delta: number }) {
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
  const cls = delta > 0 ? "sd-delta-up" : delta < 0 ? "sd-delta-down" : "sd-delta-flat";
  return <span className={`sd-delta ${cls}`}>{sign}{pct(Math.abs(delta))}</span>;
}

function pickPrevious(current: Scorecard, all: ScorecardSummary[]): ScorecardSummary | null {
  const created = new Date(current.created_at).getTime();
  const peers = all
    .filter((s) =>
      s.scorecard_id !== current.scorecard_id &&
      s.agent_id === current.agent_id &&
      s.suite_id === current.suite_id &&
      s.created_at != null &&
      new Date(s.created_at).getTime() < created)
    .sort((a, b) => new Date(b.created_at!).getTime() - new Date(a.created_at!).getTime());
  return peers[0] ?? null;
}

export function ScorecardDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const [data, setData] = useState<Scorecard | null>(null);
  const [previous, setPrevious] = useState<ScorecardSummary | null>(null);
  const [prevFull, setPrevFull] = useState<Scorecard | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportErr, setExportErr] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!id) return;
    setLoading(true);
    setErr(null);
    setPrevious(null);
    setPrevFull(null);
    api.getScorecard(id)
      .then(async (sc) => {
        setData(sc);
        // The regression diff needs a prior scorecard for the SAME (agent, suite).
        // A missing list is not fatal — the page still stands; we just say
        // "no prior run" instead of failing the whole screen.
        try {
          const all = await api.listScorecards();
          const prev = pickPrevious(sc, all);
          setPrevious(prev);
          if (prev) {
            try { setPrevFull(await api.getScorecard(prev.scorecard_id)); }
            catch { /* summary-only deltas still work */ }
          }
        } catch { /* leave previous null → "no prior run" */ }
      })
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => load(), [load]);

  const downloadReport = () => {
    setExporting(true);
    setExportErr(null);
    api.scorecardReport(id)
      .then((md) => downloadBlob(new Blob([md], { type: "text/markdown" }), `scorecard-${id}.md`))
      .catch((e) => setExportErr(errMessage(e)))
      .finally(() => setExporting(false));
  };
  const downloadPdf = () => {
    setExporting(true);
    setExportErr(null);
    api.scorecardPdf(id)
      .then((b) => downloadBlob(b, `scorecard-${id}.pdf`))
      .catch((e) => setExportErr(errMessage(e)))
      .finally(() => setExporting(false));
  };

  const criterionRows = useMemo(() => {
    if (!data) return [];
    const runs = data.run_scores ?? [];
    return Object.entries(data.per_criterion_means ?? {})
      .map(([criterion_id, mean]) => {
        const standing = standingFor(criterion_id, runs);
        const prevMean = prevFull?.per_criterion_means?.[criterion_id];
        return {
          criterion_id,
          mean,
          standing,
          delta: prevMean == null ? null : mean - prevMean,
          failing: mean < 1,
        };
      })
      .sort((a, b) => a.mean - b.mean);
  }, [data, prevFull]);

  const successDelta = useMemo(() => {
    if (!data || !previous || previous.task_success_rate == null) return null;
    return data.task_success_rate - previous.task_success_rate;
  }, [data, previous]);

  const vis = data ? VISIBILITY_META[data.visibility_tier] ?? {
    label: data.visibility_tier, blurb: "",
  } : null;

  return (
    <div className="page">
      <div className="list-page sd-page">
        <PageHeader
          title="Scorecard"
          subtitle={<>The full evaluation ledger for <span className="mono">{id}</span> — success rate with its uncertainty, cost, latency, per-criterion standing, and every case, each traceable.</>}
          actions={data && (
            <div className="sd-export">
              <button type="button" className="btn-ghost" disabled={exporting}
                      onClick={downloadReport} title="Download the Markdown report the CLI produces">
                <IconDoc size={15} /> Markdown
              </button>
              <button type="button" className="btn-ghost" disabled={exporting}
                      onClick={downloadPdf} title="Download the PDF report the CLI produces">
                <IconDownload size={15} /> PDF
              </button>
            </div>
          )}
        />

        <PageData
          loading={loading}
          error={err}
          empty={false}
          onRetry={load}
          errorTitle="Couldn't load this scorecard"
          skeleton={
            <div className="sd-skel">
              <Skeleton rows={4} />
              <Skeleton rows={6} />
            </div>
          }
        >
          {data && (
            <>
              {exportErr && (
                <div className="pagedata-error-msg" role="alert" style={{ marginBottom: 12 }}>
                  Export failed: {exportErr}
                </div>
              )}

              {/* 1 — Executive header. */}
              <section className="sd-exec" aria-label="Headline metrics">
                <div className="sd-stat sd-stat-primary">
                  <div className="sd-stat-label">Task success rate</div>
                  <div className="sd-stat-value">{pct(data.task_success_rate)}</div>
                  <Uncertainty passes={data.n_passed} n={data.n_scored} />
                  <div className="sd-stat-sub muted-sm">
                    {data.n_passed}/{data.n_scored} passed
                    {data.errored_test_ids.length > 0 && <> · {data.errored_test_ids.length} errored</>}
                  </div>
                </div>
                <div className="sd-stat">
                  <div className="sd-stat-label">Cost per case</div>
                  <div className="sd-stat-value">{money(data.mean_cost_usd)}</div>
                  <div className="sd-stat-sub muted-sm">
                    all-in {money(data.total_cost_usd + data.total_scoring_cost_usd)}
                    {" "}(scoring {money(data.total_scoring_cost_usd)})
                  </div>
                </div>
                <div className="sd-stat">
                  <div className="sd-stat-label">p95 latency</div>
                  <div className="sd-stat-value">{ms(data.p95_latency_ms)}</div>
                  <div className="sd-stat-sub muted-sm">95th percentile</div>
                </div>
                <div className="sd-stat">
                  <div className="sd-stat-label">Suite</div>
                  <div className="sd-stat-value sd-stat-value-sm mono">{data.suite_id}</div>
                  <div className="sd-stat-sub muted-sm">
                    v{data.suite_version} · agent <span className="mono">{data.agent_id}</span>
                  </div>
                </div>
              </section>

              {vis && (
                <div className={`sd-visbanner sd-vis-${data.visibility_tier}`} role="note">
                  {data.visibility_tier === "glass_box" ? <IconShield size={16} /> : <IconLock size={16} />}
                  <div>
                    <b>{vis.label}</b> — {vis.blurb}
                  </div>
                </div>
              )}

              {/* 4 — Regression diff vs the prior scorecard for this (agent, suite). */}
              <section className="sd-section" aria-label="Regression diff">
                <h2 className="sd-h2"><IconResults size={16} /> Change vs previous run</h2>
                {previous == null ? (
                  <p className="muted-sm sd-noprior">
                    No prior scorecard for <span className="mono">{data.agent_id}</span> on{" "}
                    <span className="mono">{data.suite_id}</span> — this is the first run, so there's
                    nothing to compare against yet.
                  </p>
                ) : (
                  <div className="sd-regress">
                    <div className="sd-regress-head muted-sm">
                      Comparing to <Link className="sd-tracelink"
                        to={`/app/scorecards/${encodeURIComponent(previous.scorecard_id)}`}>
                        {previous.scorecard_id}</Link>
                      {previous.created_at && <> ({new Date(previous.created_at).toLocaleString()})</>}
                    </div>
                    <div className="sd-regress-row">
                      <span>Task success rate</span>
                      <span className="mono">
                        {pct(previous.task_success_rate ?? null)} → {pct(data.task_success_rate)}
                      </span>
                      {successDelta != null && <DeltaPct delta={successDelta} />}
                    </div>
                    {!prevFull && (
                      <p className="muted-sm" style={{ marginTop: 6 }}>
                        Per-criterion deltas need the previous scorecard's detail (unavailable) —
                        showing the headline delta only.
                      </p>
                    )}
                  </div>
                )}
              </section>

              {/* 2 — Criterion breakdown. */}
              <section className="sd-section" aria-label="Criterion breakdown">
                <h2 className="sd-h2"><IconResults size={16} /> Criterion breakdown</h2>
                {criterionRows.length === 0 ? (
                  <EmptyState title="No per-criterion means recorded"
                    hint="This scorecard carries no rubric-criterion scores." />
                ) : (
                  <div className="table-wrap">
                    <table className="data sd-crit-table">
                      <thead>
                        <tr>
                          <th>criterion</th><th className="num">mean</th>
                          <th>standing</th><th className="num">vs prev</th><th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {criterionRows.map((c) => (
                          <tr key={c.criterion_id} className={c.failing ? "sd-crit-failing" : ""}>
                            <td className="mono">{c.criterion_id}</td>
                            <td className="num">{pct(c.mean)}</td>
                            <td><CalibrationBadge standing={c.standing} /></td>
                            <td className="num">{c.delta == null ? <span className="muted-sm">—</span> : <DeltaPct delta={c.delta} />}</td>
                            <td>
                              {c.failing && (
                                <Link className="sd-issuelink"
                                  to={`/app/issues?criterion=${encodeURIComponent(c.criterion_id)}`}
                                  title="See the failing cases for this criterion in Issues">
                                  <IconIssues size={13} /> issues
                                </Link>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              {/* 3 — Per-case table. */}
              <section className="sd-section" aria-label="Per-case results">
                <h2 className="sd-h2"><IconResults size={16} /> Cases</h2>
                <p className="muted-sm" style={{ marginTop: 0 }}>
                  Every scored run. Expand a row for its per-criterion judge rationales; open its trace
                  for the full record.
                </p>
                {(data.run_scores?.length ?? 0) === 0 ? (
                  <EmptyState title="No cases recorded"
                    hint="This scorecard has no per-case run scores." />
                ) : (
                  <div className="table-wrap">
                    <table className="data sd-case-table">
                      <thead>
                        <tr>
                          <th aria-hidden="true"></th>
                          <th>test</th><th>verdict</th>
                          <th className="num">cost</th><th className="num">latency</th>
                          <th className="num">steps</th><th>trace</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.run_scores.map((run) => (
                          <CaseRow key={run.trace_id} run={run} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </PageData>
      </div>
    </div>
  );
}
