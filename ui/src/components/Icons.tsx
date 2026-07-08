/* ============================================================================
   Instrument-line iconography.

   Small stroke SVGs in the Chronometer aesthetic — thin rhodium/gilt strokes,
   `currentColor` so the caller sets the metal via CSS. No emoji: these replace
   the old glyph icons on the public surfaces. `HexMark` is the brand hexagon,
   the single source of truth for the "⬡" mark (nav, footer, favicon derive from
   the same geometry).
   ========================================================================== */

interface IconProps {
  size?: number;
  className?: string;
  title?: string;
}

/** The brand hexagon mark. Gilt by default (color: var(--accent) via .brand). */
export function HexMark({ size = 15, className, title }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="2"
         role={title ? "img" : undefined} aria-hidden={title ? undefined : true}
         aria-label={title}>
      <path d="M12 2l8.66 5v10L12 22l-8.66-5V7z" />
    </svg>
  );
}

/** CI / pull-request rail — three ruled lines. */
export function IcoRail({ size = 24, className }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <path d="M4 7h16M4 12h16M4 17h10" />
    </svg>
  );
}

/** Message bus — a hub with cross-traffic. */
export function IcoBus({ size = 24, className }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3v18M3 12h18" />
    </svg>
  );
}

/** VPC shield — zero-egress enclosure. */
export function IcoShield({ size = 24, className }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6z" />
    </svg>
  );
}
