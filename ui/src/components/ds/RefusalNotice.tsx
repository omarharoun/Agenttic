/* The certificate that was NOT issued.
 *
 * Every other tool in this market sells you a number that goes up. The one thing
 * none of them can do — because their customer is the person paying them — is
 * tell that customer the evidence isn't good enough yet.
 *
 * So the hero of the site is a refusal. Not a mockup: this is the shape of what
 * the signing path actually produces when it declines, with the same reasons it
 * gives on the command line.
 */

export interface RefusalReason {
  /** short label, plain words */
  head: string;
  /** the specific finding */
  detail: string;
  /** true for the one that is a hard stop rather than a shortfall */
  critical?: boolean;
}

export interface RefusalNoticeProps {
  subject: string;
  reasons: RefusalReason[];
  /** small print under the reasons */
  footnote?: string;
}

export function RefusalNotice({ subject, reasons, footnote }: RefusalNoticeProps) {
  return (
    <figure className="rn" role="img"
      aria-label={
        `A refused certificate for ${subject}. `
        + reasons.map((r) => `${r.head}: ${r.detail}`).join(". ")
      }>
      <div className="rn__doc">
        <div className="rn__head">
          <span className="rn__kicker">Agenttic certificate</span>
          <span className="rn__stamp">Refused</span>
        </div>

        <p className="rn__subject">{subject}</p>

        <ul className="rn__reasons">
          {reasons.map((r) => (
            <li key={r.head} className={r.critical ? "is-critical" : ""}>
              <span className="rn__rhead">{r.head}</span>
              <span className="rn__rdetail">{r.detail}</span>
            </li>
          ))}
        </ul>

        {footnote && <p className="rn__foot">{footnote}</p>}
      </div>
    </figure>
  );
}
