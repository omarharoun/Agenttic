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
        <text x="60" y="60" className="seal-hex" fill={ring}
              textAnchor="middle" dominantBaseline="central">⬡</text>
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
      <span className="sm-hex" aria-hidden="true">⬡</span>
      <span className="sm-text">{label}</span>
    </span>
  );
}
