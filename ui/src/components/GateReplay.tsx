import { useEffect, useState } from "react";
import { api, errMessage, type AgentLineageNode, type Scorecard } from "../api";
import { gateSteps, type GateInput, type GateStep } from "../sim-core";
import "./GateReplay.css";

/* ============================================================================
   SPEC-5 Step 23.3 — lineage gate replay.

   Clicking a promotion doesn't just show its receipt — it REPLAYS it. We fetch
   the candidate scorecard and its parent (baseline) scorecard and re-derive the
   gate through sim-core `gateSteps`: each condition (improvement → missing →
   epsilon floor → cost → latency) stamps pass/fail in sequence, ending on the
   verdict. A rejected sibling replays to its exact failing condition and stops.

   This is a re-derivation from stored scorecards, not a recording — the final
   verdict is the parity-proven sim-core gate (Hard Rule 24). The staggered
   reveal is CSS-only and collapses to all-at-once under reduced motion.
   ========================================================================== */

/** Build a sim-core GateInput from a candidate + baseline scorecard. Paired
 *  significance isn't recoverable client-side, so the significance-veto path is
 *  not re-run (significant=false) — the deterministic floors do the work, which
 *  is exactly what governs these lineage edges. */
export function buildGateInputFromScorecards(candidate: Scorecard, baseline: Scorecard): GateInput {
  const baselineMeans = baseline.per_criterion_means ?? {};
  const candidateMeans = candidate.per_criterion_means ?? {};
  return {
    comparison: {
      successRateA: baseline.task_success_rate,
      successRateB: candidate.task_success_rate,
      successDelta: candidate.task_success_rate - baseline.task_success_rate,
      nPaired: Math.min(candidate.n_scored, baseline.n_scored),
      perCriterion: Object.keys(candidateMeans).map((cid) => ({
        criterionId: cid,
        delta: (candidateMeans[cid] ?? 0) - (baselineMeans[cid] ?? 0),
        significant: false,
      })),
    },
    baselineMeans,
    candidateMeans,
    baselineMeanCost: baseline.mean_cost_usd,
    candidateMeanCost: candidate.mean_cost_usd,
    baselineP95: baseline.p95_latency_ms,
    candidateP95: candidate.p95_latency_ms,
  };
}

const lastId = (ids: string[] | undefined) => (ids && ids.length ? ids[ids.length - 1] : null);

export function GateReplay({ node, parent }: {
  node: AgentLineageNode;
  parent: AgentLineageNode | null;
}) {
  const [steps, setSteps] = useState<GateStep[] | null>(null);
  const [verdict, setVerdict] = useState<{ promote: boolean; reason: string } | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "unavailable">("idle");
  const [detail, setDetail] = useState<string>("");

  const candId = lastId(node.scorecard_ids);
  const baseId = lastId(parent?.scorecard_ids);
  const replayable = Boolean(candId && baseId);

  // reset when the selected node changes
  useEffect(() => { setSteps(null); setVerdict(null); setState("idle"); }, [node.hash]);

  async function replay() {
    if (!candId || !baseId) return;
    setState("loading");
    try {
      const [candidate, baseline] = await Promise.all([
        api.getScorecard(candId), api.getScorecard(baseId),
      ]);
      const { steps: s, result } = gateSteps(buildGateInputFromScorecards(candidate, baseline));
      setSteps(s);
      setVerdict(result);
      setState("ready");
    } catch (e) {
      setDetail(errMessage(e));
      setState("unavailable");
    }
  }

  if (!replayable) {
    return (
      <p className="gr-note muted-sm">
        No baseline scorecard on record for this edge — nothing to replay.
      </p>
    );
  }

  return (
    <div className="gate-replay">
      <button type="button" className="btn-ghost" onClick={replay} disabled={state === "loading"}>
        {state === "loading" ? "Re-deriving…" : steps ? "Replay again" : "Replay the gate"}
      </button>
      {state === "unavailable" && (
        <p className="gr-note muted-sm">Couldn’t load the scorecards to replay: {detail}</p>
      )}
      {steps && (
        <ol className="gr-steps" aria-label="Gate conditions, in order">
          {steps.map((s, i) => (
            <li
              key={s.key}
              className={`gr-step ${s.ok ? "is-ok" : "is-fail"}`}
              style={{ animationDelay: `${i * 0.28}s` }}
            >
              <span className="gr-mark" aria-hidden="true">{s.ok ? "✓" : "✕"}</span>
              <span className="gr-step-body">
                <span className="gr-step-label">{s.label}</span>
                <span className="gr-step-note">{s.note}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {verdict && (
        <p className={`gr-verdict ${verdict.promote ? "is-promote" : "is-reject"}`}>
          Re-derived verdict:{" "}
          <strong>{verdict.promote ? "PROMOTE" : "REJECT"}</strong>
          {verdict.promote === (node.status === "promoted")
            ? " — matches the recorded ledger."
            : " — differs from the recorded status (paired significance not re-run client-side)."}
        </p>
      )}
    </div>
  );
}
