/* ============================================================================
   Instrument-line iconography.

   Small stroke SVGs in the Chronometer aesthetic — thin rhodium/gilt strokes,
   `currentColor` so the caller sets the metal via CSS. No emoji: these replace
   the old glyph icons on the public surfaces. `HexMark` is the brand mark — a
   hexagon with the cube's lower face inscribed in it — and the single source of
   truth for it. Nav, console logo, copilot, seal, auth and the loading state all
   render THIS component; public/favicon.svg and public/og-image.svg are drawn
   from the same two paths. Nothing renders the mark as the U+2B21 character any
   more: a font glyph has no consistent metrics across platforms, and since the
   mark gained an inner face it is not the brand shape at all.
   ========================================================================== */

interface IconProps {
  size?: number;
  className?: string;
  title?: string;
}

/** The brand mark: a hexagon with the cube's lower face inscribed in it.
 *
 *  The two paths share three vertices — the hexagon's lower-left (3.34,17),
 *  bottom (12,22) and lower-right (20.66,17) — which is what makes the pair read
 *  as one solid rather than a diamond sitting inside a ring. The rhombus's
 *  fourth point is the hexagon's exact centre (12,12), so the figure is the
 *  isometric projection of a cube: outline the silhouette, inner edges the
 *  near faces.
 *
 *  `currentColor`, not a gradient. The caller sets the metal via CSS
 *  (`color: var(--accent)`), which is what keeps this inside the token rule and
 *  lets one component serve the gilt nav, a muted footer and an inverted chip.
 *  The brand ASSETS (public/favicon.svg, public/og-image.svg) carry the literal
 *  gradient because a file handed to a browser or a social card cannot read a
 *  CSS variable — they are generated from this same geometry. */
export function HexMark({ size = 15, className, title }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="2"
         strokeLinejoin="round" strokeLinecap="round"
         role={title ? "img" : undefined} aria-hidden={title ? undefined : true}
         aria-label={title}>
      <path d="M12 2l8.66 5v10L12 22l-8.66-5V7z" />
      <path d="M12 12l8.66 5L12 22l-8.66-5z" />
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
