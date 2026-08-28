import { useState } from "react";
import { api, downloadBlob } from "../api";
import type { ExecutionCaseRow, ExecutionResults, ExecutionScorecardSummary, JsonValue } from "../api";
import { Uncertainty } from "../components/ui";
import { money, ms } from "../stats";
import { PASS_MEANING, PASS_THRESHOLD } from "../workflow/templates";
import { Markdown } from "../components/Markdown";
import { IconRefresh, IconWarning, IconCheck, IconHalf, IconClose, IconArrowRight, IconDownload } from "../icons";
import { CoverageWheelFor, VerificationStrip, cov, scopeNote, scopeTag, verdictScope } from "../verification";
import { ProvenanceBadge, VerdictWithScope, criterionStatus } from "../components/ds/Scorecard";

/** Post-run scoreboard: scorecard summary + one row per test case showing
 * the agent's prediction vs expected, expandable to per-criterion scores
 * and judge rationales. */
/** A test case's `expected` payload is genuinely dynamic (`JsonValue`); this
 *  reads its optional `final_output` field when present, narrowing safely. */
function expectedFinalOutput(expected: ExecutionCaseRow["expected"]): JsonValue | undefined {
  if (expected && typeof expected === "object" && !Array.isArray(expected)) {
    return expected.final_output;
  }
  return undefined;
}

export function ResultsPanel({ results }: { results: ExecutionResults }) {
  const [open, setOpen] = useState<string | null>(null);
  const [report, setReport] = useState<string>("");
  if (!results) return null;
  const { scorecards, cases } = results;
  if (!scorecards.length && !cases.length) return null;
  const errored = cases.filter((c) => c.scoring_error);
  const scored = cases.filter((c) => !c.scoring_error);
  const passed = scored.filter((c) => c.passed).length;

  const failed = scored.length - passed;
  const total = scored.length + errored.length;
  const wpct = (n: number) => total ? `${(n / total) * 100}%` : "0%";
  // Distinct provisional criteria exercised in this run — judge/fi criteria with
  // no stored calibration record (fail-closed via criterionStatus, no alpha in
  // the payload ⇒ provisional). Real today; the rest of the fence needs §7's
  // /results enrichment.
  const provCount = new Set(
    scored.flatMap((c) => (c.criteria || [])
      .filter((cr) => criterionStatus({ scorer: cr.scorer }) === "provisional")
      .map((cr) => cr.criterion_id)),
  ).size;

  return (
    <div className="results">
      {scorecards.map((sc: ExecutionScorecardSummary) => {
        const allIn = (sc.total_cost_usd ?? 0) + (sc.total_scoring_cost_usd ?? 0);
        const passThreshold = PASS_THRESHOLD;
        return (
        <div key={sc.scorecard_id}>
          {sc.cached && (
            <div className="note-ok" style={{ marginBottom: 8 }}>
              <IconRefresh size={13} /> Served from cache — identical to a previous run, so no agent or
              judge calls were made (<b>$0</b>). Re-run with refresh to recompute.
            </div>
          )}
          {/* Lead with the verdict AND its scope fence, inseparable: the reader
              cannot see a PASS colour without the narrowing that qualifies it
              (CONSOLE-DESIGN §5.3). */}
          <VerdictWithScope scope={verdictScope(sc, provCount)} />
          {/* The wheel leads: the shape of what was exercised comes before any
              rate, because a rate with no denominator is the unscoped claim. */}
          <div className="run-verif">
            <CoverageWheelFor sc={sc} size={260} />
            <div className="run-verif-nums">
              <VerificationStrip sc={sc} />
            </div>
          </div>
          <div className="score-strip">
            <div className="stat">
              <span className="lab" title={scopeNote(sc)}>Task success{scopeTag(sc)}</span>
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
                <IconDownload /> PDF
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
          <NeverExercised sc={sc} />
        </div>
        );
      })}
      {report && (
        <div style={{ margin: "8px 0" }}>
          <Markdown>{report}</Markdown>
        </div>
      )}
      {cases.map((c: ExecutionCaseRow) => (
        <div key={`${c.node_id}-${c.test_id}`} className="case-row">
          <div className="case-head"
               onClick={() => setOpen(open === c.test_id ? null : c.test_id)}>
            <span className={c.scoring_error ? "dot err-bg"
              : c.passed ? "dot ok-bg" : "dot fail-bg"} />
            <span className="case-id">{c.test_id}</span>
            {c.scoring_error ? (
              <span className="want" title={c.scoring_error}>
                <IconWarning size={12} /> not scored: {c.scoring_error}
              </span>
            ) : (
              <>
                <span className="pred" title={c.prediction}>
                  <IconArrowRight size={12} /> {c.prediction || "(no output)"}
                </span>
                {expectedFinalOutput(c.expected) !== undefined && !c.passed && (
                  <span className="want" title="expected">
                    want: {String(expectedFinalOutput(c.expected))}
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
              {c.criteria.map((cr) => (
                <div key={cr.criterion_id} className="kv">
                  <span className={cr.score >= 1 ? "ok" : "err"}>
                    {cr.score >= 1 ? <IconCheck size={13} /> : cr.score > 0 ? <IconHalf size={13} /> : <IconClose size={13} />}
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

/* ---------------------------------------------------------------------------
   SPEC-13: the run leads with VERIFICATION, not the pass rate (Hard Rule 56).
   Coverage and assertions are deterministic and cost nothing, so every run
   carries them; a pass rate reported without a fitted coverage model is an
   unscoped claim and says so.

   The vocabulary itself lives in ../verification so the dashboard, history,
   comparison and leaderboard say the same thing in the same words — a bare
   percentage on any of those screens would put the unscoped claim straight back
   in front of the reader.
   --------------------------------------------------------------------------- */

/** One coverpoint's closure — three states, and the middle one is not a zero.
 *
 *  `closure` is `null` for a dimension nothing in the system emits evidence for,
 *  and this cell used to render `Math.round((v.closure ?? 0) * 100)` — printing
 *  `0%`, the exact sentence the backend was corrected to stop saying. Zero is a
 *  measurement: it reads as "the suite never got there", a gap someone can be
 *  told to close, and no suite can close this one. It survived only because the
 *  row filter happened to drop these rows; two unrelated decisions, not a
 *  guarantee.
 *
 *  The test is `typeof v.closure === "number"`, character for character the one
 *  `dimsFromCoverage` uses to decide whether to hatch a sector — the wheel sits
 *  directly above this table on the same screen, and a table reading 0% beside a
 *  hatched sector would be the two halves of one panel disagreeing. */
/** A violated property as the assertions rollup reports it. */
interface BrokenProperty { assertion_id: string; traces: number; detail: string }

/** One `coverage.per_coverpoint` entry, mirroring the payload `ops.py` builds:
 *  `closure` is null for a coverpoint nothing can feed, and the two
 *  `not_measurable*` keys say which one and why. */
interface PerCoverpoint {
  closure?: number | null;
  unhit?: string[];
  other_hits?: number;
  not_measurable?: boolean;
  not_measurable_reason?: string;
}

function ClosureCell({ v }: { v: PerCoverpoint }) {
  if (v.not_measurable) return <span className="muted-sm">not measurable</span>;
  return typeof v.closure === "number"
    ? <>{Math.round(v.closure * 100)}%</>
    : <span className="muted-sm">not measured</span>;
}

/* Exported for ui/src/results-not-measurable.test.tsx: the not-measurable state
   is the one this table renders least often and must never get wrong, so it is
   pinned directly rather than through a whole results payload. */
export function NeverExercised({ sc }: { sc: unknown }) {
  const c = cov(sc);
  const per = (c.per_coverpoint || {}) as Record<string, PerCoverpoint>;
  const a = c.assertions;
  // A not-measurable coverpoint has an EMPTY `unhit` (you cannot have failed to
  // exercise what nothing can observe), so filtering on unhit alone hid the
  // single most important thing this panel exists to say. It is listed on its
  // own terms, with the reason, instead.
  const rows = Object.entries(per).filter(
    ([, v]) => (v.unhit || []).length || v.not_measurable);
  const brokenProps = (a?.violated_properties || []) as BrokenProperty[];
  if (!rows.length && !brokenProps.length) return null;
  return (
    <div className="never-exercised">
      {brokenProps.length > 0 && (
        <div className="ne-broken">
          <b>Properties broken</b> — a violation is a failure regardless of score:
          <ul>
            {brokenProps.map((v) => (
              <li key={v.assertion_id}>
                <code>{v.assertion_id}</code> <span className="muted-sm">({v.traces})</span>
                <div className="ne-detail">{v.detail}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {rows.length > 0 && (
        <>
          <div className="ne-head">
            What this run never exercised
            {c.baseline && <span className="muted-sm"> · baseline scope</span>}
          </div>
          <table className="ne-table">
            <tbody>
              {rows.map(([id, v]) => (
                <tr key={id}>
                  <td className="ne-cp">{id}</td>
                  <td className="ne-closure"><ClosureCell v={v} /></td>
                  <td className="ne-bins">
                    {v.not_measurable ? (
                      v.not_measurable_reason
                        ? <span className="ne-detail">{v.not_measurable_reason}</span>
                        : null
                    ) : (v.unhit || []).map((b: string) => (
                      <code key={b} className="ne-bin">{b}</code>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {c.limits && <div className="ne-limits">{c.limits}</div>}
        </>
      )}
    </div>
  );
}
