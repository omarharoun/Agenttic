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

// ---- criterionStatus ------------------------------------------------------
// The ONE fail-closed derivation of a criterion's calibration status on the
// client, mirroring the backend criterion_status() (reporting/scorecard_report.py).
// A judge/fi criterion is calibrated ONLY when a stored calibration record travels
// with it — proven here by a finite `alpha` (a promoted record's measured
// agreement). The self-asserted `calibrated` boolean is DELIBERATELY not consulted:
// a payload can claim calibrated:true with no record (the F1 fail-open F4 closed on
// the server), and it must still read provisional here. Record presence, never a flag.

export type CalStatus = "deterministic" | "calibrated" | "provisional";

export function criterionStatus(
  { scorer, alpha }: { scorer: Scorer; alpha?: number | null },
): CalStatus {
  if (scorer === "code") return "deterministic";
  return typeof alpha === "number" && Number.isFinite(alpha)
    ? "calibrated" : "provisional";
}

export function ProvenanceBadge({
  scorer, calibrated, alpha, ceiling,
}: { scorer: Scorer; calibrated?: boolean; alpha?: number | null; ceiling?: number | null }) {
  // `calibrated` is accepted for call-site compatibility but intentionally
  // ignored: status derives from record presence (alpha), not a self-asserted flag.
  void calibrated;
  const status = criterionStatus({ scorer, alpha });
  let kind: "det" | "cal" | "prov";
  let label: string;
  if (status === "deterministic") {
    kind = "det"; label = "deterministic";
  } else if (status === "provisional") {
    kind = "prov";
    label = scorer === "fi" ? "measured · provisional" : "judged · provisional";
  } else {  // calibrated — a stored record is present
    kind = "cal";
    const cap = ceiling != null ? ` (ceiling ${ceiling.toFixed(2)})` : "";
    label = (scorer === "fi"
      ? `measured · α=${alpha!.toFixed(2)}`
      : `judged · α=${alpha!.toFixed(2)}`) + cap;
  }
  const title = {
    det: "A code check on the trace — same input, same result, no model in the loop.",
    cal: `Scored by a judge that agrees with human reviewers at α=${alpha?.toFixed(2)} (a stored, promoted calibration record).`,
    prov: "Scored by a judge with no stored calibration record on this criterion — shown, flagged, never counted as certain.",
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
  ceiling?: number;           // human–human agreement ceiling (calibrated criteria)
  score: number;              // 0..1
  tone?: ScoreTone;           // defaults from score (>= 0.7 pass, else provisional/fail)
}

function scoreToneFor(score: number): ScoreTone {
  if (score >= 0.7) return "pass";
  if (score >= 0.5) return "provisional";
  return "fail";
}

// A provisional criterion is a distinct TYPE, never a point on the pass→fail
// ramp: its number, however high, must not read as a pass. Status wins over value
// (CONSOLE-DESIGN §5.1). An explicit `tone` override still wins for demo rows.
function rowTone(r: CriterionRow): ScoreTone {
  if (r.tone) return r.tone;
  if (criterionStatus(r) === "provisional") return "provisional";
  return scoreToneFor(r.score);
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
          <ProvenanceBadge scorer={r.scorer} calibrated={r.calibrated} alpha={r.alpha} ceiling={r.ceiling} />
          <ScoreValue value={r.score} tone={rowTone(r)} />
        </div>
      ))}
    </div>
  );
}

// ---- VerdictWithScope -----------------------------------------------------
// The verdict and its scope fence are ONE indivisible unit. The coloured verdict
// pill is emitted only from inside this component, in the same return as the
// fence, and the fence is unconditional — so there is no code path that shows a
// PASS colour without the narrowing that qualifies it (CONSOLE-DESIGN §5.3).
//
// Proven two ways, not merely intended: a grep test pins the `vws-verdict--`
// colour class to this one file; a render test pins the fence as always present.
// Together: colour ⟹ fence, structurally. Leading a scorecard with a bare
// verdict number is impossible unless someone deletes both tests.

export interface VerdictScope {
  status: "PASS" | "INCOMPLETE" | "FAIL" | null;  // verification_status; null = not recorded
  scoped: boolean;               // a coverage model was applied; false ⇒ "unscoped"
  coverageHoles: number;         // unexercised required coverage bins
  notMeasured: number;           // coverpoints nothing could measure
  assertionsUnexercised: number; // vacuous properties — never a pass
  provisionalCriteria: number;   // uncalibrated judge/fi criteria
  naCriteria?: number;           // criteria inapplicable to the cases
  closurePct?: number | null;    // trace closure, 0..100
  closureTarget?: number | null; // target closure, 0..100
}

const VERDICT_KIND: Record<string, "pass" | "fail" | "incomplete"> = {
  PASS: "pass", FAIL: "fail", INCOMPLETE: "incomplete",
};

export function VerdictWithScope({ scope }: { scope: VerdictScope }) {
  const kind = scope.status ? VERDICT_KIND[scope.status] : "none";
  const label = scope.status ?? "NOT RECORDED";

  const holes = scope.coverageHoles || 0;
  const nm = scope.notMeasured || 0;
  const unex = scope.assertionsUnexercised || 0;
  const prov = scope.provisionalCriteria || 0;
  const na = scope.naCriteria || 0;

  const parts: string[] = [];
  if (holes) parts.push(`${holes} coverage ${holes === 1 ? "bin" : "bins"} unexercised`);
  if (nm) parts.push(`${nm} ${nm === 1 ? "dimension" : "dimensions"} not measured`);
  if (unex) parts.push(`${unex} ${unex === 1 ? "assertion" : "assertions"} unexercised`);
  if (prov) parts.push(`${prov} ${prov === 1 ? "criterion" : "criteria"} provisional`);
  if (na) parts.push(`${na} N/A`);

  // The fence ALWAYS speaks. An absent coverage model reads "unscoped" — never a
  // false all-clear (absence must not read as full coverage). A clean, scoped run
  // states so explicitly rather than leaving the reader to assume it.
  let fence: string;
  if (!scope.scoped) {
    fence = "unscoped — no coverage model applied to this result";
  } else if (parts.length === 0) {
    fence = "within full scope — nothing unexercised or unmeasured";
  } else {
    fence = parts.join(" · ");
  }

  return (
    <div className="vws">
      <span className={`vws-verdict vws-verdict--${kind}`}>{label}</span>
      <span className="vws-scope">
        {scope.scoped && scope.closurePct != null && (
          <span className="vws-closure">
            closure {scope.closurePct}%
            {scope.closureTarget != null && (
              <span className="muted-sm"> / {scope.closureTarget}%</span>
            )}
          </span>
        )}
        <span className="vws-fence"
              title="What this verdict does and does not cover — the scope travels with the status.">
          {fence}
        </span>
      </span>
    </div>
  );
}
