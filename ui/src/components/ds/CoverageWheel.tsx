/* The coverage wheel — the product's one picture.
 *
 * A pass rate is a statement about the cases someone wrote. It is silent about
 * every situation the suite never put the agent in, and silence is invisible: a
 * bar showing "22% closure" renders the missing 78% as empty background, which
 * reads as nothing at all.
 *
 * The wheel fixes that by construction. Each dimension is a spoke; how far its
 * wedge reaches from the hub is how much of that dimension has actually been
 * exercised; the rim is the closure target. **The gap between wedge and rim is
 * the untested part, drawn as area** — comparable spoke to spoke, impossible to
 * skim past.
 *
 * A dimension the model does not measure at all is hatched to the rim rather
 * than omitted or scored zero. An unmeasured dimension can never fail, so it
 * would otherwise never appear in any report; on the wheel it occupies a whole
 * sector.
 *
 * Token-only (Hard Rule 47 / SPEC-11): every colour is a `--score-*` var so the
 * console and the landing render identically and both themes follow the one
 * token source. No raw hex — the token lint enforces it.
 */
import { useId } from "react";

export interface WheelDim {
  /** coverpoint id, e.g. "action_risk" */
  id: string;
  /** 0..1 exercised, or null when this model does not measure the dimension */
  value: number | null;
  /** optional: hit/total, shown in the tooltip */
  hit?: number;
  total?: number;
}

export interface CoverageWheelProps {
  dims: WheelDim[];
  /** overall trace closure, 0..1 */
  closure: number | null;
  /** closure target, 0..1 (the rim) */
  target?: number;
  /** px; the svg scales to its container regardless */
  size?: number;
  /** hide the outside spoke labels — for a small/decorative placement */
  compact?: boolean;
  /** accessible summary; a sensible one is derived when omitted */
  label?: string;
  /** the word under the hub number. "closure" is right for the console, where
   *  the reader knows the term; the public site says "tried" instead. */
  hubLabel?: string;
}

const TAU = Math.PI * 2;
const pt = (cx: number, cy: number, r: number, a: number): [number, number] =>
  [cx + r * Math.cos(a), cy + r * Math.sin(a)];

/** annular sector path */
function seg(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number) {
  const big = a1 - a0 > Math.PI ? 1 : 0;
  const [x0, y0] = pt(cx, cy, r1, a0);
  const [x1, y1] = pt(cx, cy, r1, a1);
  const [x2, y2] = pt(cx, cy, r0, a1);
  const [x3, y3] = pt(cx, cy, r0, a0);
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r1},${r1} 0 ${big} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`
    + ` L${x2.toFixed(2)},${y2.toFixed(2)} A${r0},${r0} 0 ${big} 0 ${x3.toFixed(2)},${y3.toFixed(2)} Z`;
}

export function CoverageWheel({
  dims, closure, target = 0.95, size = 420, compact = false, label,
  hubLabel = "closure",
}: CoverageWheelProps) {
  const hatchId = useId();
  if (!dims.length) return null;

  const S = size;
  const cx = S / 2;
  const cy = S / 2;
  const hub = Math.round(S * 0.11);
  const rim = Math.round(S * (compact ? 0.44 : 0.40));
  const per = TAU / dims.length;
  const rAt = (v: number) => hub + (rim - hub) * Math.min(v / target, 1);

  const measured = dims.filter((d) => d.value != null).length;
  const unmeasured = dims.length - measured;
  const a11y = label ?? (
    `Coverage wheel: ${measured} of ${dims.length} dimensions measured`
    + (unmeasured ? `, ${unmeasured} not measured at all` : "")
    + (closure == null ? "" : `, overall closure ${Math.round(closure * 100)} percent`)
    + `, against a ${Math.round(target * 100)} percent target.`
  );

  const guides = [0.25, 0.5, 0.75].map((g) => (
    <circle key={g} className="cw-ring" cx={cx} cy={cy} r={rAt(g)} />
  ));

  const pad = compact ? 8 : Math.round(S * 0.2);

  return (
    <svg
      className={`cw${compact ? " cw-compact" : ""}`}
      viewBox={`${-pad} ${-pad / 2} ${S + pad * 2} ${S + pad}`}
      role="img"
      aria-label={a11y}
    >
      <defs>
        {/* the "not measured" channel — texture, so it never rests on colour */}
        <pattern
          id={hatchId} width="7" height="7"
          patternUnits="userSpaceOnUse" patternTransform="rotate(45)"
        >
          <line x1="0" y1="0" x2="0" y2="7" className="cw-hatch" strokeWidth="2.5" />
        </pattern>
      </defs>

      {guides}
      <circle className="cw-ring cw-target" cx={cx} cy={cy} r={rim} />

      {dims.map((d, i) => {
        const a0 = -Math.PI / 2 + per * i;
        const a1 = a0 + per;
        const mid = (a0 + a1) / 2;
        const count = d.hit != null && d.total != null ? ` (${d.hit}/${d.total})` : "";

        const wedge = d.value == null ? (
          <path
            className="cw-seg cw-unmeasured"
            fill={`url(#${hatchId})`}
            d={seg(cx, cy, hub, rim, a0, a1)}
          >
            <title>
              {`${d.id} — NOT MEASURED by this coverage model. It cannot fail, `
                + `because nothing ever asks about it.`}
            </title>
          </path>
        ) : (
          <>
            <path className="cw-seg cw-hit" d={seg(cx, cy, hub, rAt(d.value), a0, a1)}>
              <title>{`${d.id} — ${(d.value * 100).toFixed(0)}% exercised${count}`}</title>
            </path>
            <path
              className="cw-seg cw-gap"
              d={seg(cx, cy, rAt(d.value), rim, a0, a1)}
            >
              <title>
                {`${d.id} — ${((target - d.value) * 100).toFixed(0)} points short of `
                  + `the ${(target * 100).toFixed(0)}% target: situations never exercised`}
              </title>
            </path>
          </>
        );

        const [lx, ly] = pt(cx, cy, rim + 14, mid);
        const anchor = Math.abs(Math.cos(mid)) < 0.2
          ? "middle" : (Math.cos(mid) > 0 ? "start" : "end");

        return (
          <g key={d.id}>
            {wedge}
            <line
              className="cw-divider"
              x1={cx} y1={cy}
              x2={pt(cx, cy, rim, a0)[0]} y2={pt(cx, cy, rim, a0)[1]}
            />
            {!compact && (
              <>
                <text className="cw-lab" x={lx} y={ly} textAnchor={anchor}>
                  {d.id.replace(/_/g, " ")}
                </text>
                <text className="cw-val" x={lx} y={ly + 13} textAnchor={anchor}>
                  {d.value == null ? "not measured" : `${Math.round(d.value * 100)}%`}
                </text>
              </>
            )}
          </g>
        );
      })}

      <circle className="cw-hub" cx={cx} cy={cy} r={hub - 1} />
      {closure != null && (
        <>
          <text className="cw-ctr-v" x={cx} y={cy + 2}>{Math.round(closure * 100)}%</text>
          <text className="cw-ctr-k" x={cx} y={cy + 16}>{hubLabel}</text>
        </>
      )}
    </svg>
  );
}

/** Map a scorecard's `coverage.per_coverpoint` onto wheel dimensions.
 *
 * `declared` names every dimension the ARCHETYPE has, so a coverpoint the run's
 * model does not measure still gets a sector — the whole point of the hatched
 * state. Anything measured but undeclared is appended rather than dropped.
 */
export function dimsFromCoverage(
  perCoverpoint: Record<string, { closure?: number; unhit?: string[] }> | null | undefined,
  declared: string[] = [],
): WheelDim[] {
  const cp = perCoverpoint || {};
  const ids = [...declared, ...Object.keys(cp).filter((k) => !declared.includes(k))];
  return ids.map((id) => {
    const d = cp[id];
    return d == null
      ? { id, value: null }
      : { id, value: typeof d.closure === "number" ? d.closure : null };
  });
}

/** The eight dimensions the conversational_transactional archetype declares.
 *  The baseline model measures the first five; the rest render as not-measured. */
export const DECLARED_COVERPOINTS = [
  "trajectory", "tool_condition", "action_risk", "data_condition",
  "session_shape", "intent", "emotional_register", "policy_vector",
];


/** The wheel's legend. Ships beside it wherever it is used, because the three
 *  states must never be carried by colour alone. */
export function CoverageWheelLegend({ showUnmeasured = true }: { showUnmeasured?: boolean }) {
  return (
    <div className="cw-legend">
      <span className="lg"><span className="sw hit" /> exercised</span>
      <span className="lg">
        <span className="sw gap" /> never exercised — <em>not a pass</em>
      </span>
      {showUnmeasured && (
        <span className="lg">
          <span className="sw un" /> not measured by this model
        </span>
      )}
    </div>
  );
}
