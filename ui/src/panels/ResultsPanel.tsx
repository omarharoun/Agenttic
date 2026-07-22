import { useState } from "react";
import { api, downloadBlob } from "../api";
import { Uncertainty } from "../components/ui";
import { money, ms } from "../stats";
import { PASS_MEANING, PASS_THRESHOLD } from "../workflow/templates";
import { Markdown } from "../components/Markdown";
import { ProvenanceBadge } from "../components/ds";

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

  const failed = scored.length - passed;
  const total = scored.length + errored.length;
  const wpct = (n: number) => total ? `${(n / total) * 100}%` : "0%";

  return (
    <div className="results">
      {scorecards.map((sc: any) => {
        const allIn = (sc.total_cost_usd ?? 0) + (sc.total_scoring_cost_usd ?? 0);
        const passThreshold = sc.pass_threshold ?? PASS_THRESHOLD;
        return (
        <div key={sc.scorecard_id}>
          {sc.cached && (
            <div className="note-ok" style={{ marginBottom: 8 }}>
              ♻ Served from cache — identical to a previous run, so no agent or
              judge calls were made (<b>$0</b>). Re-run with refresh to recompute.
            </div>
          )}
          <div className="score-strip">
            <div className="stat">
              <span className="lab">Task success</span>
              {scored.length === 0 ? (
                <span className="val sm err" title="No cases could be scored — see errored cases">
                  Not scored
                </span>
              ) : (
                <span className={`val ${sc.task_success_rate >= passThreshold ? "ok" : "err"}`}
                      title={PASS_MEANING}>
                  {Math.round(sc.task_success_rate * 100)}%
                </span>
              )}
            </div>
            <div className="stat">
              <span className="lab">Passed</span>
              <span className="val sm">{passed}<span className="muted-sm"> / {scored.length || 0}</span></span>
            </div>
            {errored.length > 0 && (
              <div className="stat">
                <span className="lab">Errored</span>
                <span className="val sm wait" title="scoring/config errors — excluded from the rate">{errored.length}</span>
              </div>
            )}
            <div className="stat">
              <span className="lab">Cost / case</span>
              <span className="val sm" title={sc.mean_cost_usd == null ? "not measured" : undefined}>
                {money(sc.mean_cost_usd)}</span>
            </div>
            {allIn > 0 && (
              <div className="stat" title={`agent execution $${(sc.total_cost_usd ?? 0).toFixed(4)} + judge $${(sc.total_scoring_cost_usd ?? 0).toFixed(4)}`}>
                <span className="lab">All-in total</span>
                <span className="val sm">${allIn.toFixed(4)}</span>
              </div>
            )}
            <div className="stat">
              <span className="lab">Visibility</span>
              <span className="val sm" style={{ fontFamily: "var(--font-ui)", fontSize: 14, fontWeight: 600 }}>
                {sc.visibility_tier.replace("_", "-")}</span>
            </div>
            <div className="spacer" />
            <div className="actions">
              <button onClick={() => report ? setReport("")
                  : api.scorecardReport(sc.scorecard_id).then(setReport)}>
                {report ? "Hide report" : "Report"}
              </button>
              <button title="Download as PDF"
                      onClick={() => api.scorecardPdf(sc.scorecard_id)
                        .then((b) => downloadBlob(b, `scorecard-${sc.scorecard_id}.pdf`))
                        .catch(() => {})}>
                ⤓ PDF
              </button>
            </div>
          </div>
          {total > 0 && (
            <div className="passbar" role="img"
                 aria-label={`${passed} passed, ${failed} failed, ${errored.length} errored of ${total}`}>
              {passed > 0 && <span className="p" style={{ width: wpct(passed) }} title={`${passed} passed`} />}
              {failed > 0 && <span className="f" style={{ width: wpct(failed) }} title={`${failed} failed`} />}
              {errored.length > 0 && <span className="e" style={{ width: wpct(errored.length) }} title={`${errored.length} errored`} />}
            </div>
          )}
          {scored.length > 0 && (
            <div className="score-ci">
              Task success is {passed}/{scored.length} scored cases ·{" "}
              <Uncertainty passes={passed} n={scored.length} />
              {errored.length > 0 && <> · {errored.length} excluded (scoring error)</>}
              <div className="pass-def" title={PASS_MEANING}>
                Pass = mean criterion score ≥ {passThreshold.toFixed(2)}
              </div>
            </div>
          )}
        </div>
        );
      })}
      {report && (
        <div style={{ margin: "8px 0" }}>
          <Markdown>{report}</Markdown>
        </div>
      )}
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
                  {cr.criterion_id}{" "}
                  <ProvenanceBadge scorer={cr.scorer} calibrated={cr.calibrated} />
                  {cr.rationale && (
                    <div className="rationale">{cr.rationale}</div>
                  )}
                </div>
              ))}
              <div className="kv">
                <small>{c.steps ?? "?"} steps ·
                  {" "}{money(c.cost_usd)} ·
                  {" "}{ms(c.latency_ms)}</small>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
