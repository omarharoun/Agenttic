import { useEffect, useState } from "react";
import { api, errMessage, type LiveWindows, type LiveWindowCriterion } from "../api";
import { driftStatus } from "../sim-core";
import { EmptyState, Skeleton } from "./ui";
import "./DriftMonitor.css";

/* ============================================================================
   SPEC-5 Step 23.4 — the live drift strip-chart.

   One strip per live criterion: the recent per-response scores as dots, the
   rolling window mean drawn against the batch-baseline band, and the fire
   threshold as a hairline. When the mean falls past the threshold the line goes
   to the fail colour and the re-eval request annotates the chart. The drift
   decision is re-derived from the raw window through the parity-proven sim-core
   `driftStatus` — the strip is a re-derivation, not a recording.

   Static SVG, so inherently reduced-motion safe (every value is also text).
   ========================================================================== */

const W = 460, H = 96, PAD = 10;

function Strip({ crit, window, threshold }: {
  crit: LiveWindowCriterion; window: number; threshold: number;
}) {
  const status = driftStatus({
    criteria: [crit.criterion_id],
    liveScores: { [crit.criterion_id]: crit.window_scores },
    baselineMeans: { [crit.criterion_id]: crit.baseline_mean },
    window, driftThreshold: threshold,
  });
  const mean = status.perCriterionMean[crit.criterion_id] ?? 0;
  const fired = status.driftDetected;
  // oldest -> newest for the chart (registry returns newest-first)
  const pts = [...crit.window_scores].reverse();
  const n = pts.length;
  const y = (v: number) => H - PAD - v * (H - PAD * 2);
  const dx = n > 1 ? (W - PAD * 2) / (n - 1) : 0;
  const baseY = y(crit.baseline_mean);
  const floorY = y(crit.baseline_mean - threshold);
  const meanY = y(mean);

  return (
    <div className="dm-strip">
      <div className="dm-strip-head">
        <span className="dm-crit">{crit.criterion_id}</span>
        <span className={`dm-mean ${fired ? "is-fired" : ""}`}>
          mean {mean.toFixed(2)} vs baseline {crit.baseline_mean.toFixed(2)}
        </span>
        <span className={`dm-flag ${fired ? "is-fired" : "is-ok"}`}>
          {fired ? "DRIFT" : "stable"}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="dm-svg" role="img"
           aria-label={`${crit.criterion_id}: rolling mean ${mean.toFixed(2)}, baseline ${crit.baseline_mean.toFixed(2)}, ${fired ? "drift fired" : "stable"}`}>
        <rect x={PAD} y={baseY} width={W - PAD * 2} height={Math.max(0, floorY - baseY)}
              className="dm-band" />
        <line x1={PAD} x2={W - PAD} y1={floorY} y2={floorY} className="dm-threshold" />
        <line x1={PAD} x2={W - PAD} y1={meanY} y2={meanY}
              className={`dm-meanline ${fired ? "is-fired" : ""}`} />
        {pts.map((v, i) => (
          <circle key={i} cx={PAD + i * dx} cy={y(v)} r={2.5}
                  className={v >= 0.5 ? "dm-dot-ok" : "dm-dot-bad"} />
        ))}
      </svg>
      {status.reeval[0] && <p className="dm-reeval">{status.reeval[0]}</p>}
    </div>
  );
}

export function DriftMonitor({ agentId, baselineScorecardId }: {
  agentId: string; baselineScorecardId: string;
}) {
  const [data, setData] = useState<LiveWindows | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    setState("loading");
    api.getLiveWindows(agentId, baselineScorecardId)
      .then((d) => { if (live) { setData(d); setState("ready"); } })
      .catch((e) => { if (live) { setErr(errMessage(e)); setState("error"); } });
    return () => { live = false; };
  }, [agentId, baselineScorecardId]);

  if (state === "loading") return <Skeleton />;
  if (state === "error") return <p className="dm-error">Couldn’t load live drift: {err}</p>;
  if (!data || data.criteria.length === 0) {
    return <EmptyState title="No live scores yet"
      hint="Sampled production responses will chart here as they arrive." />;
  }

  return (
    <div className="drift-monitor">
      {data.criteria.map((c) => (
        <Strip key={c.criterion_id} crit={c}
               window={data.window} threshold={data.drift_threshold} />
      ))}
    </div>
  );
}
