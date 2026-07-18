import { useMemo, useState } from "react";
import type { RunScore } from "../api";
import {
  recomputeScorecard, DEFAULT_PASS_THRESHOLD, type WhatIfRun,
} from "../sim-core";
import { pct } from "../stats";
import "./WhatIfPanel.css";

/* ============================================================================
   SPEC-5 Step 23.2 — scorecard "What if" mode.

   Turn criterion weights + the pass threshold into live controls and watch
   pass/fail per case, the success rate, and its interval recompute INSTANTLY
   via the parity-proven sim-core `recomputeScorecard` — the same math the
   server scores with. This is the consulting conversation: retune the rubric
   with the client in the room and see which cases flip.

   HARD RULE 25: the mode is visibly labelled hypothetical and never mutates.
   Leaving hypothesis happens only via "Propose as rubric v(n+1)", which hands
   the proposed weights to the normal versioned-rubric flow.

   No animation to encode: recomputation is instant state, so this is inherently
   reduced-motion safe — every number is text.
   ========================================================================== */

const WEIGHT_STEPS = { min: 0, max: 3, step: 0.5 };

export function WhatIfPanel({ runs, criteria }: { runs: RunScore[]; criteria: string[] }) {
  const [weights, setWeights] = useState<Record<string, number>>(
    () => Object.fromEntries(criteria.map((c) => [c, 1.0])),
  );
  const [threshold, setThreshold] = useState(DEFAULT_PASS_THRESHOLD);
  const [proposal, setProposal] = useState<string | null>(null);

  const whatIfRuns: WhatIfRun[] = useMemo(
    () =>
      runs.map((r) => ({
        testId: r.test_id,
        scores: Object.fromEntries(r.criterion_scores.map((c) => [c.criterion_id, c.score])),
        scoringError: r.scoring_error != null,
      })),
    [runs],
  );

  const result = useMemo(
    () => recomputeScorecard(whatIfRuns, weights, threshold),
    [whatIfRuns, weights, threshold],
  );

  // which cases changed pass/fail vs the stored scorecard (the flips)
  const storedPass = useMemo(
    () => new Map(runs.map((r) => [r.test_id, r.passed])),
    [runs],
  );
  const flips = result.perCase.filter(
    (pc) => storedPass.get(pc.testId) !== undefined && storedPass.get(pc.testId) !== pc.passed,
  );

  return (
    <section className="whatif" aria-label="What-if rubric explorer">
      <div className="whatif-banner" role="status">
        Hypothetical — not saved. Nothing here changes the recorded scorecard.
      </div>

      <div className="whatif-body">
        <div className="whatif-controls">
          <h3 className="whatif-h3">Criterion weights</h3>
          {criteria.map((cid) => (
            <label key={cid} className="whatif-slider" htmlFor={`w-${cid}`}>
              <span className="whatif-slider-head">
                <span>{cid}</span>
                <span className="whatif-slider-val">{weights[cid].toFixed(1)}×</span>
              </span>
              <input
                id={`w-${cid}`} type="range" {...WEIGHT_STEPS} value={weights[cid]}
                onChange={(e) =>
                  setWeights((w) => ({ ...w, [cid]: parseFloat(e.target.value) }))}
              />
            </label>
          ))}
          <label className="whatif-slider" htmlFor="w-threshold">
            <span className="whatif-slider-head">
              <span>Pass threshold</span>
              <span className="whatif-slider-val">{threshold.toFixed(2)}</span>
            </span>
            <input
              id="w-threshold" type="range" min={0} max={1} step={0.05} value={threshold}
              onChange={(e) => setThreshold(parseFloat(e.target.value))}
            />
          </label>
        </div>

        <div className="whatif-readout">
          <div className="whatif-rate">
            <span className="whatif-rate-num">{pct(result.successRate)}</span>
            <span className="whatif-rate-ci">
              95% CI {pct(result.wilsonLow)}–{pct(result.wilsonHigh)}
            </span>
            <span className="whatif-rate-n">
              {result.nPassed}/{result.nScored} pass
            </span>
          </div>
          <p className="whatif-flips">
            {flips.length === 0
              ? "No cases flip at these settings."
              : `${flips.length} case${flips.length === 1 ? "" : "s"} flip vs the recorded run:`}
          </p>
          {flips.length > 0 && (
            <ul className="whatif-flip-list">
              {flips.map((f) => (
                <li key={f.testId} className={f.passed ? "flip-gain" : "flip-loss"}>
                  <code>{f.testId}</code> → {f.passed ? "now passes" : "now fails"}{" "}
                  <span className="flip-weighted">(weighted {f.weighted.toFixed(2)})</span>
                </li>
              ))}
            </ul>
          )}

          <button
            type="button" className="btn-ghost whatif-propose"
            onClick={() =>
              setProposal(JSON.stringify({ weights, pass_threshold: threshold }, null, 2))}
          >
            Propose as rubric v(n+1)
          </button>
          {proposal && (
            <div className="whatif-proposal">
              <p>
                Proposed rubric config — apply through the versioned-rubric flow
                (this panel never mutates the live rubric):
              </p>
              <pre>{proposal}</pre>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
