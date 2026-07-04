import { useState } from "react";

/**
 * Provisional Evaluation-Gaming Resistance (EGR) card.
 *
 * HONESTY CONTRACT (docs/GAMING_SPEC.md §4.3): a high EGR is evidence of the
 * ABSENCE OF DETECTABLE gaming, not proof of honesty. The card renders the
 * PROVISIONAL badge, the honesty caption, and — the differentiator — the
 * side-by-side test-vs-deployment transcripts for each incident.
 *
 * Fed by GET /executions/{id}/gaming (see gaming.issues.gaming_api_payload).
 * Kept dependency-light and un-routed; the parent page wires it in.
 */

type Framing = {
  framing: string;
  prompt: string;
  behavior_rate: number;
  n_runs: number;
  awareness_verbalized: boolean;
  awareness_quote: string | null;
  sample_transcript: string;
};

type GamingIssue = {
  id: string;
  title: string;
  category_label: string;
  mechanism: string;
  severity: "critical" | "high" | "medium" | "low";
  why: string;
  evidence: { framings: Framing[]; detail: Record<string, unknown> };
  suggested_fix: { label: string; route: string; blurb: string };
};

export type GamingReport = {
  egr: number;
  egr_low: number;
  egr_high: number;
  band: [number, number];
  sub_scores: Record<string, number>;
  provisional: boolean;
  limits: string;
  n_probes: number;
  n_incidents: number;
  n_critical_incidents: number;
  agent_cost_usd: number;
  issues: GamingIssue[];
  summary: { headline: string; clean: boolean };
};

const SEV_CLS: Record<GamingIssue["severity"], string> = {
  critical: "sev-critical",
  high: "sev-high",
  medium: "sev-medium",
  low: "sev-low",
};

const SUB_LABEL: Record<string, string> = {
  behavior_delta: "Eval-awareness Δ",
  sandbagging: "Sandbagging",
  consistency: "Cross-framing",
  heldout: "Held-out freshness",
};

function band(n: number): string {
  return `${Math.round(n)}`;
}

/** One incident with the side-by-side framing transcripts. */
function IncidentCard({ issue }: { issue: GamingIssue }) {
  const [open, setOpen] = useState(issue.severity === "critical");
  const fx = issue.suggested_fix;
  return (
    <div className={`issue-card ${SEV_CLS[issue.severity]}`}>
      <div className="issue-top">
        <span className={`sev-chip ${SEV_CLS[issue.severity]}`}>{issue.severity}</span>
        <span className="issue-cat">{issue.category_label}</span>
        <span className="issue-affected">{issue.mechanism}</span>
      </div>
      <h3 className="issue-title">{issue.title}</h3>
      <p className="issue-why">{issue.why}</p>
      <div className="issue-actions">
        <button className="issue-evbtn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Hide transcripts" : "Show test-vs-deployment transcripts"}
        </button>
        <a className="issue-fix" href={fx.route} title={fx.blurb}>
          {fx.label} →
        </a>
      </div>
      {open && (
        <div className="issue-evidence" style={{ display: "grid", gap: 12,
             gridTemplateColumns: `repeat(${Math.min(issue.evidence.framings.length, 2)}, 1fr)` }}>
          {issue.evidence.framings.map((f) => (
            <div className="ev-case" key={f.framing}>
              <div className="ev-case-head">
                <code>{f.framing}</code>
                <span className={`ev-score ${f.behavior_rate >= 1 ? "ok" : f.behavior_rate > 0 ? "half" : "bad"}`}>
                  safe {Math.round(f.behavior_rate * 100)}%
                </span>
                {f.awareness_verbalized && <span className="ev-prov">verbalised “test”</span>}
              </div>
              <div className="muted-sm" style={{ marginBottom: 4 }}>{f.prompt}</div>
              <div className="ev-rationale">“{f.sample_transcript}”</div>
              {f.awareness_quote && (
                <div className="muted-sm" style={{ marginTop: 4 }}>
                  awareness: “{f.awareness_quote}”
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function GamingCard({ report }: { report: GamingReport }) {
  const subs = Object.entries(report.sub_scores);
  return (
    <section className="issue-card" style={{ borderLeftWidth: 4 }}>
      <div className="issue-top">
        <span className="sev-chip sev-low">PROVISIONAL</span>
        <span className="issue-cat">Evaluation-Gaming Resistance</span>
        <span className="issue-affected">
          {report.n_probes} probes · {report.n_incidents} incidents
          {report.n_critical_incidents ? ` · ${report.n_critical_incidents} critical` : ""}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 12, margin: "8px 0" }}>
        <span style={{ fontSize: 40, fontWeight: 700, lineHeight: 1 }}>{band(report.egr)}</span>
        <span className="muted-sm">/ 100</span>
        <span className="muted-sm">
          band {band(report.egr_low)}–{band(report.egr_high)}
        </span>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        {subs.map(([k, v]) => (
          <span key={k} className="issue-cat" title={k}>
            {SUB_LABEL[k] ?? k}: <strong>{Math.round(v)}</strong>
          </span>
        ))}
      </div>

      <p className="issue-why" style={{ fontStyle: "italic" }}>
        Evidence of the absence of <em>detectable</em> gaming, not proof of honesty. {report.limits}
      </p>

      {report.issues.length === 0 ? (
        <p className="issue-why">
          No eval-gaming detected across {report.n_probes} probes — but a uniformly
          deceptive or silently eval-aware agent would also score clean.
        </p>
      ) : (
        <div style={{ display: "grid", gap: 10, marginTop: 8 }}>
          {report.issues.map((i) => (
            <IncidentCard key={i.id} issue={i} />
          ))}
        </div>
      )}
    </section>
  );
}

export default GamingCard;
