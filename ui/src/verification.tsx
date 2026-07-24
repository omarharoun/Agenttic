/* ============================================================================
   The verification vocabulary — one implementation, used everywhere.

   SPEC-13 reframed the RUN view to lead with what was never exercised rather
   than with a pass rate. That reframing is only true if it survives every other
   surface: a dashboard, a history table, a comparison or a leaderboard that
   shows a bare percentage puts the unscoped claim straight back in front of the
   reader, and the most-visited screens win.

   So the scope vocabulary lives here rather than inside ResultsPanel, and every
   screen that renders a pass rate renders it through these helpers:

     scopeTag / scopeNote   what the rate is (and is not) a claim about
     ScopeChip              the inline badge form, for table cells
     VerificationStrip      the headline row: closure, assertions, unexercised
     CoverageCell           the compact table-cell form of the same three facts

   All of them accept EITHER a full scorecard or a list row — the list endpoint
   ships the same compact `coverage` summary, so nothing has to fetch a whole
   scorecard just to say honestly how much its number covers.
   ========================================================================== */

/** The fields every record carries. A FULL scorecard carries more (per-coverpoint
 *  detail, holes, violated_properties); the index signature admits those without
 *  pretending the compact list rows have them. */
export interface CoverageSummary {
  model_ref?: string | null;
  baseline?: boolean | null;
  trace_closure?: number | null;
  closure_target?: number | null;
  closed?: boolean | null;
  limits?: string | null;
  assertions?: {
    total?: number; violations?: number; unexercised?: number; verdict?: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    [k: string]: any;
  } | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
}

export function cov(sc: any): CoverageSummary {
  return (sc?.coverage || {}) as CoverageSummary;
}

/** Is there enough verification data on this record to say anything at all? */
export function hasVerification(sc: any): boolean {
  const c = cov(sc);
  return Boolean(c.model_ref || c.assertions);
}

export function scopeTag(sc: any): string {
  const c = cov(sc);
  if (!c.model_ref) return " (unscoped)";
  if (c.baseline) return " (baseline scope)";
  return "";
}

export function scopeNote(sc: any): string {
  const c = cov(sc);
  if (!c.model_ref)
    return "No coverage model was applied, so this rate says nothing about what "
      + "the suite never exercised. It is an unscoped claim.";
  if (c.baseline) return c.limits || "Baseline coverage model only.";
  return "Scoped to a fitted coverage model.";
}

export function closurePct(sc: any): number | null {
  const c = cov(sc);
  if (!c.model_ref || c.trace_closure == null) return null;
  return Math.round(c.trace_closure * 100);
}

/** Inline scope badge for a table cell sitting next to a percentage. */
export function ScopeChip({ sc }: { sc: any }) {
  const c = cov(sc);
  const kind = !c.model_ref ? "unscoped" : c.baseline ? "baseline" : "fitted";
  if (kind === "fitted") return null;      // a fitted model needs no caveat
  return (
    <span className={`scope-chip scope-${kind}`} title={scopeNote(sc)}>
      {kind}
    </span>
  );
}

/** The headline verification row. Rendered ABOVE any score strip. */
export function VerificationStrip({ sc }: { sc: any }) {
  const c = cov(sc);
  const a = c.assertions;
  if (!c.model_ref && !a) return null;
  const closure = closurePct(sc);
  const target = Math.round((c.closure_target ?? 0.95) * 100);
  return (
    <div className="score-strip verif-strip">
      <div className="stat">
        <span className="lab" title={c.limits || ""}>Coverage closure</span>
        <span className={`val ${c.closed ? "ok" : "err"}`}>
          {closure == null ? "—" : `${closure}%`}
          <span className="muted-sm"> / {target}%</span>
        </span>
      </div>
      {a && (
        <>
          <div className="stat">
            <span className="lab">Assertions</span>
            <span className={`val sm ${a.violations ? "err" : "ok"}`}>
              {a.verdict}
              <span className="muted-sm"> {a.violations}/{a.total} broken</span>
            </span>
          </div>
          <div className="stat">
            <span className="lab"
                  title="Properties whose antecedent never occurred. Not evidence of correctness.">
              Never exercised
            </span>
            <span className="val sm wait">
              {a.unexercised}<span className="muted-sm"> of {a.total}</span>
            </span>
          </div>
        </>
      )}
      <div className="spacer" />
    </div>
  );
}

/** The table-cell form: closure, then broken/unexercised properties. */
export function CoverageCell({ sc }: { sc: any }) {
  const c = cov(sc);
  const a = c.assertions;
  if (!hasVerification(sc)) {
    return <span className="muted-sm" title={scopeNote(sc)}>not measured</span>;
  }
  const closure = closurePct(sc);
  return (
    <span className="cov-cell">
      {closure != null && (
        <b className={c.closed ? "ok" : "err"} title={c.limits || ""}>{closure}%</b>
      )}
      {a && (
        <span className="cov-cell-props">
          {a.violations ? (
            <span className="err"
                  title="a property was broken — a violation is a failure regardless of the score">
              {a.violations} broken
            </span>
          ) : (
            <span className="ok" title="no property was broken on any run">held</span>
          )}
          {!!a.unexercised && (
            <span className="wait"
                  title="properties whose situation never arose — not evidence of correctness">
              {a.unexercised} unexercised
            </span>
          )}
        </span>
      )}
    </span>
  );
}

/** One line stating what a pass rate on this record is a claim about. */
export function ScopeLine({ sc }: { sc: any }) {
  const c = cov(sc);
  if (!c.model_ref) {
    return (
      <p className="scope-line unscoped">
        No coverage model was applied to this result, so its pass rate describes
        only the cases the suite happens to contain. It is not a statement about
        what the agent was never put through.
      </p>
    );
  }
  return (
    <p className="scope-line">
      {c.baseline
        ? "Scoped to the baseline coverage model, which is archetype-independent. "
          + "A fitted model measures more of the situation space."
        : "Scoped to a fitted coverage model."}
      {c.limits ? ` ${c.limits}` : ""}
    </p>
  );
}
