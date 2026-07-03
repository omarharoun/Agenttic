import type { ReactNode } from "react";

/* ============================================================================
   Plain-language glossary for the platform's rigor jargon.

   Keep the rigor, lower the barrier: a term renders with a subtle dotted
   underline + a small "?" and a hover/focus tooltip that explains it in one
   sentence. Definitions live here as one source of truth so the same term reads
   the same everywhere.
   ========================================================================== */
export const GLOSSARY: Record<string, string> = {
  wilson:
    "Wilson 95% lower bound — a conservative floor on the true pass rate given "
    + "the sample size n. We judge the floor, not the lucky point estimate, so a "
    + "small sample can't look better than it is.",
  mcnemar:
    "McNemar's paired test — on the cases where the two variants disagreed, is "
    + "the winner unlikely to be luck? A low p-value (< 0.05) means the "
    + "difference is statistically significant, not noise.",
  opro:
    "OPRO / ProTeGi — published prompt-optimization methods. Read the failing "
    + "cases and judge rationales (the 'gradient'), propose better prompts, and "
    + "keep only those that beat a frozen held-out set.",
  ratchet:
    "Anti-collapse ratchet — an improvement is accepted only if it beats the "
    + "previous best on a held-out set, so a degenerate 'improvement' that "
    + "overfits or collapses the agent is refused rather than kept.",
  tiers:
    "Glass-box vs black-box — black-box grades only the agent's inputs and "
    + "outputs; glass-box also inspects its internal steps and tool calls. "
    + "Glass-box is stricter and more informative.",
  drift:
    "Drift threshold — the live-monitoring score below which a sampled "
    + "production trace is flagged as regression-worthy and promoted for review.",
};

/** Inline jargon term with a "?" affordance and a plain-language tooltip.
 *  Falls back to rendering the children plainly if the key is unknown. */
export function Term({ name, children }: { name: string; children: ReactNode }) {
  const def = GLOSSARY[name];
  if (!def) return <>{children}</>;
  return (
    <span className="term" tabIndex={0} role="note" title={def} aria-label={def}>
      {children}<span className="term-q" aria-hidden="true">?</span>
    </span>
  );
}
