/* EscapementMark (SPEC-11 Step 51) — the signature dial: concentric rings with
 * a tick hand that steps around in discrete escapement beats. ONE implementation
 * (the landing hero + brand mark both use this). Reduced-motion aware: the sweep
 * animation is disabled by the global @media (prefers-reduced-motion) rule, and
 * the information (a precision instrument) is preserved statically.
 *
 * Distinct from Gauge (a 270° value dial with a numeral); this is the decorative
 * brand instrument, no value.
 */
export function EscapementMark({
  size = 300, className = "", "aria-hidden": ariaHidden = true,
}: { size?: number; className?: string; "aria-hidden"?: boolean }) {
  return (
    <svg
      className={`ds-escape ${className}`.trim()}
      viewBox="0 0 300 300"
      width={size}
      height={size}
      aria-hidden={ariaHidden}
    >
      <circle cx="150" cy="150" r="140" />
      <circle cx="150" cy="150" r="112" />
      <circle cx="150" cy="150" r="4" className="ds-escape__hub" />
      <g className="ds-escape__tick">
        <line x1="150" y1="150" x2="150" y2="16" />
      </g>
      <g className="ds-escape__ticks">
        <line x1="150" y1="8" x2="150" y2="24" />
        <line x1="292" y1="150" x2="276" y2="150" />
        <line x1="150" y1="292" x2="150" y2="276" />
        <line x1="8" y1="150" x2="24" y2="150" />
      </g>
    </svg>
  );
}
