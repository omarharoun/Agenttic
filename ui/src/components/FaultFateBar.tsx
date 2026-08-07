import { useId, useState } from "react";

/**
 * Fault fates across scenario runs — a stacked bar, one row per run.
 *
 * WHY A STACK, NOT A COUNT. The three fates partition one planned total:
 * `fired` (the fault happened), `skipped` (it reached its call and could not
 * happen), `never_reached` (the agent never got there). Reporting "3 faults" or
 * a single ratio collapses them, and "we broke it and the agent never arrived"
 * is a finding about the RUN, not a silence. The engine already reports them as
 * three facts; this draws them the same way.
 *
 * Colours come from `--cat-1..3`, which are validated against the six checks in
 * both themes — the dial-tuned `--viz-*` ramp fails as a categorical set (one
 * step is literally grey). Identity is never colour-alone: every segment is
 * direct-labelled at ≥ 24px, the legend is always present, and the table view
 * below carries the same numbers for assistive tech and for print.
 */

export interface FaultCounts {
  fired: number;
  skipped: number;
  never_reached: number;
  planned?: number;
}

export interface FaultFateRow {
  runId: string;
  label: string;
  counts: FaultCounts;
  worldChanged?: boolean;
  nBlocked?: number;
}

const SERIES = [
  { key: "fired" as const, label: "fired", token: "var(--cat-1)" },
  { key: "skipped" as const, label: "skipped", token: "var(--cat-2)" },
  { key: "never_reached" as const, label: "never reached", token: "var(--cat-3)" },
];

const ROW_H = 26;
const BAR_H = 14;
const GAP = 2;          // surface gap between segments (marks-and-anatomy)
// Wide enough for `12345678 · scenario-name` in 12px mono. Measured, not
// guessed: at 168 the labels ran under the bars and read as truncated scenario
// ids. The component truncates the label itself as a backstop.
const LABEL_W = 232;
const LABEL_MAX = 26;

export function FaultFateBar({ rows, max }: { rows: FaultFateRow[]; max?: number }) {
  const titleId = useId();
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null);
  const [showTable, setShowTable] = useState(false);

  const totals = rows.map((r) =>
    r.counts.planned ?? (r.counts.fired + r.counts.skipped + r.counts.never_reached));
  const scaleMax = max ?? Math.max(1, ...totals);
  const plotW = 420;
  const height = rows.length * ROW_H + 8;

  if (!rows.length) return null;

  return (
    <figure className="ffb" aria-labelledby={titleId}>
      <figcaption id={titleId} className="ffb__cap">
        Fault fates per run
        <span className="ffb__sub">
          {" "}— three facts, never a count: a staged fault the agent never
          reached is a finding, not a silence.
        </span>
      </figcaption>

      <div className="ffb__legend">
        {SERIES.map((s) => (
          <span className="ffb__key" key={s.key}>
            <span className="ffb__swatch" style={{ background: s.token }} aria-hidden="true" />
            {s.label}
          </span>
        ))}
      </div>

      <div className="ffb__plot">
        <svg width={LABEL_W + plotW} height={height} role="img"
             aria-label={`Fault fates for ${rows.length} scenario run(s)`}>
          {rows.map((r, i) => {
            const total = totals[i] || 0;
            const y = i * ROW_H;
            let x = LABEL_W;
            return (
              <g key={r.runId}>
                <text x={0} y={y + BAR_H} className="ffb__rowlab">
                  {r.label.length > LABEL_MAX
                    ? r.label.slice(0, LABEL_MAX - 1) + "\u2026" : r.label}
                </text>
                {total === 0 ? (
                  <text x={LABEL_W} y={y + BAR_H} className="ffb__none">
                    no fault staged
                  </text>
                ) : SERIES.map((s) => {
                  const v = r.counts[s.key] ?? 0;
                  if (!v) return null;
                  const w = Math.max(2, (v / scaleMax) * plotW - GAP);
                  const seg = (
                    <g key={s.key}>
                      <rect x={x} y={y} width={w} height={BAR_H} rx={4}
                            fill={s.token}
                            onMouseEnter={(e) => setHover({
                              x: e.clientX, y: e.clientY,
                              text: `${r.label} — ${v} ${s.label} of ${total}`,
                            })}
                            onMouseLeave={() => setHover(null)} />
                      {w >= 24 && (
                        <text x={x + 6} y={y + BAR_H - 3} className="ffb__val">{v}</text>
                      )}
                    </g>
                  );
                  x += w + GAP;
                  return seg;
                })}
                {(r.worldChanged || (r.nBlocked ?? 0) > 0) && (
                  <text x={LABEL_W + plotW + 6} y={y + BAR_H} className="ffb__mark">
                    {r.worldChanged ? "world changed" : ""}
                    {r.worldChanged && (r.nBlocked ?? 0) > 0 ? " · " : ""}
                    {(r.nBlocked ?? 0) > 0 ? `${r.nBlocked} blocked` : ""}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hover && (
          <div className="ffb__tip" style={{ left: hover.x + 12, top: hover.y + 12 }}>
            {hover.text}
          </div>
        )}
      </div>

      <button type="button" className="ffb__toggle"
              aria-expanded={showTable}
              onClick={() => setShowTable((v) => !v)}>
        {showTable ? "Hide the numbers" : "Show the numbers"}
      </button>
      {showTable && (
        <table className="ffb__table">
          <thead>
            <tr><th>run</th>{SERIES.map((s) => <th key={s.key}>{s.label}</th>)}
              <th>world</th><th>blocked</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.runId}>
                <td>{r.label}</td>
                {SERIES.map((s) => <td key={s.key}>{r.counts[s.key] ?? 0}</td>)}
                <td>{r.worldChanged ? "changed" : "unchanged"}</td>
                <td>{r.nBlocked ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </figure>
  );
}

export default FaultFateBar;
