/* ============================================================================
   The Agenttic Safety Certification seal / mark.

   Two forms of one brand asset:
   - <Seal>      a circular wax-seal-style mark ("AGENTTIC · SAFETY CERTIFIED")
                 with the hex glyph and an optional grade in the center. Used on
                 the public certificate page and the directory header.
   - <SealMark>  a compact inline "⬡ Tested with Agenttic" lockup for nav / cards.

   Pure SVG + CSS variables (Clay accent, currentColor) so it themes with the
   rest of Noor and needs no image asset.
   ========================================================================== */

import { gradeColor } from "../cert";
import { HexMark } from "./Icons";

/** Circular safety-certified seal. Pass a `grade` to stamp it in the middle;
 *  otherwise the hex mark sits center. */
export function Seal({ grade, size = 132, title = "Agenttic Safety Certified" }: {
  grade?: string; size?: number; title?: string;
}) {
  const ring = grade ? gradeColor(grade) : "var(--accent)";
  // arc path ids must be unique per grade so two seals on a page don't collide
  const topId = `seal-top-${grade ?? "x"}`;
  const botId = `seal-bot-${grade ?? "x"}`;
  return (
    <svg className="seal" width={size} height={size} viewBox="0 0 120 120"
         role="img" aria-label={grade ? `${title}: grade ${grade}` : title}>
      <defs>
        <path id={topId} d="M 60 60 m -44 0 a 44 44 0 1 1 88 0" />
        <path id={botId} d="M 60 60 m 44 0 a 44 44 0 1 1 -88 0" />
      </defs>
      {/* scalloped double ring */}
      <circle cx="60" cy="60" r="57" fill="none" stroke={ring} strokeWidth="1.4"
              strokeDasharray="2 3" opacity="0.55" />
      <circle cx="60" cy="60" r="51" fill="none" stroke={ring} strokeWidth="2" />
      <circle cx="60" cy="60" r="38" fill="var(--accent-soft)"
              stroke={ring} strokeWidth="1" />
      {/* arched lettering */}
      <text className="seal-arc" fill={ring}>
        <textPath href={`#${topId}`} startOffset="50%" textAnchor="middle">
          AGENTTIC
        </textPath>
      </text>
      <text className="seal-arc" fill={ring}>
        <textPath href={`#${botId}`} startOffset="50%" textAnchor="middle">
          SAFETY&nbsp;CERTIFIED
        </textPath>
      </text>
      {/* center: grade, else hex mark */}
      {grade ? (
        <text x="60" y="60" className="seal-grade" fill={ring}
              textAnchor="middle" dominantBaseline="central">{grade}</text>
      ) : (
        /* The mark as GEOMETRY, not as a glyph in whatever font resolved.
           `⬡` (U+2B21) has no consistent metrics across platforms — it is the
           reason the seal's centre drifted off-axis on some machines — and it
           is not the brand shape at all now that the mark carries an inner
           face. Scaled from the same 24-unit artwork `HexMark` uses so the two
           can never diverge; centred by translating the artwork's own centre
           (12,12) onto the seal's (60,60). */
        <g transform="translate(60,60) scale(1.55) translate(-12,-12)"
           fill="none" stroke={ring} strokeWidth="2"
           strokeLinejoin="round" strokeLinecap="round">
          <path d="M12 2l8.66 5v10L12 22l-8.66-5V7z" />
          <path d="M12 12l8.66 5L12 22l-8.66-5z" />
        </g>
      )}
      {/* tiny stars flanking */}
      <text x="22" y="64" fill={ring} fontSize="9" textAnchor="middle">✦</text>
      <text x="98" y="64" fill={ring} fontSize="9" textAnchor="middle">✦</text>
    </svg>
  );
}

/** Compact inline trust lockup — "⬡ Tested with Agenttic". */
export function SealMark({ label = "Tested with Agenttic" }: { label?: string }) {
  return (
    <span className="seal-mark" title="Agenttic Safety Certification">
      <span className="sm-hex" aria-hidden="true"><HexMark size={13} /></span>
      <span className="sm-text">{label}</span>
    </span>
  );
}
