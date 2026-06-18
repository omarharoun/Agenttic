import { useState } from "react";
import { api, downloadBlob } from "../api";

/** Post-run scoreboard: scorecard summary + one row per test case showing
 * the agent's prediction vs expected, expandable to per-criterion scores
 * and judge rationales. */
export function ResultsPanel({ results }: { results: any }) {
  const [open, setOpen] = useState<string | null>(null);
  const [report, setReport] = useState<string>("");
  if (!results) return null;
  const { scorecards, cases } = results;
  if (!scorecards.length && !cases.length) return null;
  const errored = cases.filter((c: any) => c.scoring_error);
  const scored = cases.filter((c: any) => !c.scoring_error);
  const passed = scored.filter((c: any) => c.passed).length;

  return (
    <div className="results">
      {scorecards.map((sc: any) => (
        <div key={sc.scorecard_id} className="results-summary">
          {scored.length === 0 ? (
            <span className="err" style={{ fontSize: 14, fontWeight: 700 }}
                  title="No cases could be scored — see errored cases">
              Not scored
            </span>
          ) : (
            <>
              <span className={sc.task_success_rate >= 0.7 ? "ok" : "err"}
                    style={{ fontSize: 15, fontWeight: 700 }}>
                {Math.round(sc.task_success_rate * 100)}%
              </span>
              <span>{passed}/{scored.length} passed{scored.length !== cases.length
                ? " of scored" : ""}</span>
            </>
          )}
          {errored.length > 0 && (
            <span className="err" title="scoring/config errors — excluded from the rate">
              {errored.length} errored
            </span>
          )}
          <span title="agent execution cost / case">
            ${(sc.mean_cost_usd ?? 0).toFixed(4)}/case</span>
          {(sc.total_cost_usd != null || sc.total_scoring_cost_usd != null) && (
            <span title={`agent execution $${(sc.total_cost_usd ?? 0).toFixed(4)} + `
                         + `judge $${(sc.total_scoring_cost_usd ?? 0).toFixed(4)}`}>
              total ${((sc.total_cost_usd ?? 0)
                       + (sc.total_scoring_cost_usd ?? 0)).toFixed(4)}
            </span>
          )}
          <span>{sc.visibility_tier.replace("_", "-")}</span>
          <button style={{ marginLeft: "auto" }}
                  onClick={() => report ? setReport("")
                    : api.scorecardReport(sc.scorecard_id).then(setReport)}>
            {report ? "hide report" : "report"}
          </button>
          <button title="Download as PDF"
                  onClick={() => api.scorecardPdf(sc.scorecard_id)
                    .then((b) => downloadBlob(b, `scorecard-${sc.scorecard_id}.pdf`))
                    .catch(() => {})}>
            ⤓ PDF
          </button>
        </div>
      ))}
      {report && <pre className="doc" style={{ margin: "8px 0" }}>{report}</pre>}
      {cases.map((c: any) => (
        <div key={`${c.node_id}-${c.test_id}`} className="case-row">
          <div className="case-head"
               onClick={() => setOpen(open === c.test_id ? null : c.test_id)}>
            <span className={c.scoring_error ? "dot err-bg"
              : c.passed ? "dot ok-bg" : "dot fail-bg"} />
            <span className="case-id">{c.test_id}</span>
            {c.scoring_error ? (
              <span className="want" title={c.scoring_error}>
                ⚠ not scored: {c.scoring_error}
              </span>
            ) : (
              <>
                <span className="pred" title={c.prediction}>
                  → {c.prediction || "(no output)"}
                </span>
                {c.expected?.final_output !== undefined && !c.passed && (
                  <span className="want" title="expected">
                    want: {String(c.expected.final_output)}
                  </span>
                )}
              </>
            )}
          </div>
          {open === c.test_id && (
            <div className="case-detail">
              {c.expected && (
                <div className="kv">expected:
                  <code>{JSON.stringify(c.expected)}</code></div>
              )}
              {c.criteria.map((cr: any) => (
                <div key={cr.criterion_id} className="kv">
                  <span className={cr.score >= 1 ? "ok" : "err"}>
                    {cr.score >= 1 ? "✓" : cr.score > 0 ? "½" : "✕"}
                  </span>{" "}
                  {cr.criterion_id} <small>({cr.scorer}
                  {cr.calibrated ? "" : ", PROVISIONAL"})</small>
                  {cr.rationale && (
                    <div className="rationale">{cr.rationale}</div>
                  )}
                </div>
              ))}
              <div className="kv">
                <small>{c.steps ?? "?"} steps ·
                  ${(c.cost_usd ?? 0).toFixed(4)} ·
                  {Math.round(c.latency_ms ?? 0)}ms</small>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
