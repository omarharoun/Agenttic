/* ============================================================================
   Gauge — a power-reserve-style complication: a 270° arc that sweeps to the
   value on mount (escapement easing, honors reduced-motion via CSS), with a
   tabular numeral at center. Used for the Agenttic Index on the certificate.
   ========================================================================== */
import { useEffect, useState } from "react";

export function Gauge({ value, size = 132, label = "Agenttic Index",
                        color = "var(--accent)" }: {
  value: number; size?: number; label?: string; color?: string;
}) {
  const v = Math.max(0, Math.min(100, value));
  // Show the Index at the same precision as the scan's "Safety score X/100"
  // (one decimal), so the dial numeral never disagrees with the headline number
  // beside it. Whole numbers stay clean (100, not 100.0).
  const shown = Number.isInteger(v) ? String(v) : v.toFixed(1);
  const r = 50;
  const C = 2 * Math.PI * r;
  const arc = C * 0.75;                       // 270° dial
  const off = arc * (1 - v / 100);
  const [swept, setSwept] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setSwept(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return (
    <svg className="gauge" width={size} height={size} viewBox="0 0 120 120"
         role="img" aria-label={`${label}: ${shown} of 100`}
         style={{ color }}>
      <g transform="rotate(135 60 60)">
        <circle className="gauge-track" cx={60} cy={60} r={r} fill="none"
                strokeWidth={6} strokeLinecap="round"
                strokeDasharray={`${arc} ${C}`} />
        <circle className="gauge-sweep" cx={60} cy={60} r={r} fill="none"
                stroke="currentColor" strokeWidth={6} strokeLinecap="round"
                strokeDasharray={`${arc} ${C}`}
                strokeDashoffset={swept ? off : arc} />
      </g>
      <text x={60} y={66} textAnchor="middle" className="gauge-num">
        {shown}
      </text>
      {/* caption sits in the dial's open reserve gap (bottom 90°), where a
          power-reserve label lives on a real watch — clear of the ring. */}
      <text x={60} y={106} textAnchor="middle" className="gauge-cap">
        {label}
      </text>
    </svg>
  );
}
