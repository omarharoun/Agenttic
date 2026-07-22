/* Shared product-score components (SPEC-11 Step 51). ONE implementation each,
 * used by BOTH the console scorecard (real data) and the landing demo (sample
 * data) — Hard Rule 48. Token-only styling (design/tokens.css via ds.css).
 */
import type { ReactNode } from "react";

// ---- ProvenanceBadge ------------------------------------------------------
// How a score was measured — the real provenance model. Deterministic (code
// check), judged·calibrated (LLM judge agreeing with humans at α), or
// provisional (judge not yet calibrated — shown, never counted as certain).

export type Scorer = "code" | "judge" | "fi";

export function ProvenanceBadge({
  scorer, calibrated = true, alpha,
}: { scorer: Scorer; calibrated?: boolean; alpha?: number }) {
  let kind: "det" | "cal" | "prov";
  let label: string;
  if (scorer === "code") {
    kind = "det"; label = "deterministic";
  } else if (scorer === "fi") {
    kind = "cal"; label = "measured";
  } else if (!calibrated) {
    kind = "prov"; label = "judged · provisional";
  } else {
    kind = "cal";
    label = alpha != null ? `judged · α=${alpha.toFixed(2)}` : "judged · calibrated";
  }
  const title = {
    det: "A code check on the trace — same input, same result, no model in the loop.",
    cal: alpha != null
      ? `An LLM judge scored this and agrees with human reviewers at α=${alpha?.toFixed(2)}.`
      : "Scored by an LLM judge calibrated against human reviewers.",
    prov: "Scored by a judge not yet calibrated against humans on this criterion — shown, flagged, never counted as certain.",
  }[kind];
  return (
    <span className={`ds-badge ds-badge--${kind}`} title={title}>{label}</span>
  );
}

// ---- ScoreValue -----------------------------------------------------------
// A number in Geist Mono, coloured by its semantic score token, with an
// optional interval (e.g. "±4" or "[0.80, 1.00]").

export type ScoreTone = "pass" | "provisional" | "fail" | "neutral";

export function ScoreValue({
  value, interval, tone = "pass", unit,
}: { value: number | string; interval?: string; tone?: ScoreTone; unit?: string }) {
  const shown = typeof value === "number"
    ? (Number.isInteger(value) ? String(value) : value.toFixed(2))
    : value;
  return (
    <span className={`ds-score ds-score--${tone}`}>
      {shown}{unit && <small className="ds-score__unit">{unit}</small>}
      {interval && <small className="ds-score__ci"> {interval}</small>}
    </span>
  );
}

// ---- ScorecardCard --------------------------------------------------------
// The criterion-row + metrics-header block. The console renders it with real
// data; the landing renders it with sample data. SAME component.

export interface ScoreMetric { label: string; value: ReactNode; sub?: string; }

export interface CriterionRow {
  name: string;
  description?: string;
  scorer: Scorer;
  calibrated?: boolean;
  alpha?: number;
  score: number;              // 0..1
  tone?: ScoreTone;           // defaults from score (>= 0.7 pass, else provisional/fail)
}

function toneFor(score: number): ScoreTone {
  if (score >= 0.9) return "pass";
  if (score >= 0.7) return "pass";
  if (score >= 0.5) return "provisional";
  return "fail";
}

export function ScorecardCard({
  bar, metrics = [], rows = [],
}: { bar?: string; metrics?: ScoreMetric[]; rows?: CriterionRow[] }) {
  return (
    <div className="ds-card">
      {bar && <div className="ds-card__bar">{bar}</div>}
      {metrics.length > 0 && (
        <div className="ds-card__metrics">
          {metrics.map((m) => (
            <div className="ds-metric" key={m.label}>
              <div className="ds-metric__l">{m.label}</div>
              <div className="ds-metric__v">{m.value}{m.sub && <small> {m.sub}</small>}</div>
            </div>
          ))}
        </div>
      )}
      {rows.map((r) => (
        <div className="ds-crow" key={r.name}>
          <div className="ds-crow__id">
            <div className="ds-crow__name">{r.name}</div>
            {r.description && <div className="ds-crow__desc">{r.description}</div>}
          </div>
          <ProvenanceBadge scorer={r.scorer} calibrated={r.calibrated} alpha={r.alpha} />
          <ScoreValue value={r.score} tone={r.tone ?? toneFor(r.score)} />
        </div>
      ))}
    </div>
  );
}
