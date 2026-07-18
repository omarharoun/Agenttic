import type { ExecState, NodeRunState } from "../store";
import "./PipelineFlow.css";

/* ============================================================================
   SPEC-5 Step 23.1 — the run pipeline, moving.

   A lane diagram of the execution: each node is a stage the cases flow through
   (harness → checks → judge → scorecard). It is driven PURELY by the real SSE
   `exec` slice the store already maintains — node states + per-node case
   progress — so there are no timers faking motion; a token only advances when
   an event says a case finished. The active stage pulses; a failed stage flares
   to the fail colour. The per-case table below remains the source of record.

   Reduced-motion: the pulse is disabled (see css); all state is still conveyed
   by colour + the "done/total" text, so no information is lost.
   ========================================================================== */

export interface PipelineStage {
  nodeId: string;
  state: NodeRunState | "pending";
  done: number;
  total: number;
}

/** Order the run's nodes into pipeline order — by first appearance in the event
 *  log (execution order), falling back to id. Pure, so it unit-tests. */
export function pipelineStages(exec: ExecState): PipelineStage[] {
  const firstSeq: Record<string, number> = {};
  for (const l of exec.log) {
    if (l.nodeId && !(l.nodeId in firstSeq)) firstSeq[l.nodeId] = l.seq;
  }
  const ids = new Set<string>([
    ...Object.keys(exec.nodeStates),
    ...Object.keys(exec.progress),
  ]);
  return [...ids]
    .sort((a, b) => (firstSeq[a] ?? Infinity) - (firstSeq[b] ?? Infinity) || a.localeCompare(b))
    .map((nodeId) => ({
      nodeId,
      state: exec.nodeStates[nodeId] ?? "pending",
      done: exec.progress[nodeId]?.done ?? 0,
      total: exec.progress[nodeId]?.total ?? 0,
    }));
}

const MAX_DOTS = 24;

function Tokens({ done, total }: { done: number; total: number }) {
  if (total <= 0) return null;
  const shown = Math.min(total, MAX_DOTS);
  const doneShown = Math.round((done / total) * shown);
  return (
    <div className="pf-tokens" aria-hidden="true">
      {Array.from({ length: shown }, (_, i) => (
        <span key={i} className={`pf-token${i < doneShown ? " is-done" : ""}`} />
      ))}
    </div>
  );
}

export function PipelineFlow({ exec }: { exec: ExecState }) {
  const stages = pipelineStages(exec);
  if (stages.length === 0) return null;

  return (
    <div className="pf" role="group" aria-label="Pipeline progress">
      {stages.map((s, i) => (
        <div key={s.nodeId} className="pf-stage-wrap">
          <div className={`pf-stage pf-state-${s.state}`}>
            <span className="pf-stage-name">{s.nodeId}</span>
            <span className="pf-stage-count">
              {s.total > 0 ? `${s.done}/${s.total}` : s.state}
            </span>
            <Tokens done={s.done} total={s.total} />
          </div>
          {i < stages.length - 1 && <span className="pf-arrow" aria-hidden="true">→</span>}
        </div>
      ))}
    </div>
  );
}
