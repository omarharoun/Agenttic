import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Issue, type IssuesReport as Report } from "../api";
import { EmptyState, Skeleton, Uncertainty } from "./ui";

/** Severity → chip class + short label. Worst is loud, low is quiet. */
const SEV: Record<Issue["severity"], { cls: string; label: string }> = {
  critical: { cls: "sev-critical", label: "Critical" },
  high: { cls: "sev-high", label: "High" },
  medium: { cls: "sev-medium", label: "Medium" },
  low: { cls: "sev-low", label: "Low" },
};

function pct(share: number | null): string {
  return share == null ? "" : `${Math.round(share * 100)}%`;
}

/** One issue, as an expandable card: title + severity + why always visible;
 *  the failing-case evidence expands on demand. */
function IssueCard({ issue }: { issue: Issue }) {
  const [open, setOpen] = useState(false);
  const sev = SEV[issue.severity];
  const ev = issue.evidence;
  const fix = issue.suggested_fix;
  return (
    <div className={`issue-card ${sev.cls}`}>
      <div className="issue-top">
        <span className={`sev-chip ${sev.cls}`}>{sev.label}</span>
        <span className="issue-cat">{issue.category_label}</span>
        <span className="issue-affected">
          {issue.affected_n}
          {issue.n_measured ? <span className="muted-sm">/{issue.n_measured}</span> : null}
          {issue.affected_share != null && <> · {pct(issue.affected_share)} of cases</>}
        </span>
      </div>
      <h3 className="issue-title">{issue.title}</h3>
      <p className="issue-why">{issue.why}</p>
      <div className="issue-actions">
        {(ev.cases.length > 0 || (ev.criteria?.length ?? 0) > 0) && (
          <button className="issue-evbtn" onClick={() => setOpen((o) => !o)}
                  aria-expanded={open}>
            {open ? "Hide evidence" : `Show evidence (${ev.counts.failing ?? ev.counts.errored ?? ev.cases.length})`}
          </button>
        )}
        <Link className="issue-fix" to={fix.route} title={fix.blurb}>
          {fix.label} →
        </Link>
      </div>
      {fix.blurb && <p className="issue-fixblurb">{fix.blurb}</p>}
      {open && (
        <div className="issue-evidence">
          {ev.criteria?.map((c) => (
            <div className="ev-crit" key={c.criterion_id}>
              <code>{c.criterion_id}</code>
              {c.description && <span className="muted-sm"> — {c.description}</span>}
              <span className="ev-count">{c.provisional} provisional</span>
            </div>
          ))}
          {ev.cases.map((c, i) => (
            <div className="ev-case" key={c.test_id ?? i}>
              <div className="ev-case-head">
                <code>{c.test_id}</code>
                {c.score != null && (
                  <span className={`ev-score ${c.score >= 1 ? "ok" : c.score > 0 ? "half" : "bad"}`}>
                    {c.score >= 1 ? "✓" : c.score > 0 ? "½" : "✕"} {c.score}
                  </span>
                )}
                {c.calibrated === false && <span className="ev-prov">provisional</span>}
              </div>
              {c.rationale && <div className="ev-rationale">“{c.rationale}”</div>}
              {c.prediction && (
                <div className="ev-kv"><span className="ev-k">agent said</span>
                  <span className="ev-v">{c.prediction}</span></div>
              )}
              {c.expected && (
                <div className="ev-kv"><span className="ev-k">expected</span>
                  <span className="ev-v">{c.expected}</span></div>
              )}
            </div>
          ))}
          {ev.truncated > 0 && (
            <div className="ev-more">+{ev.truncated} more case{ev.truncated === 1 ? "" : "s"} not shown</div>
          )}
        </div>
      )}
    </div>
  );
}

/** The ranked scorecard of what's wrong — the hero of a result. Renders its own
 *  loading / error / empty (clean) states. Pass a report directly, or an
 *  executionId to fetch it. */
export function IssuesReport({ executionId, report: injected }: {
  executionId?: string; report?: Report | null;
}) {
  const [report, setReport] = useState<Report | null>(injected ?? null);
  const [state, setState] = useState<"idle" | "loading" | "error">(
    injected ? "idle" : "loading");

  useEffect(() => {
    if (injected) { setReport(injected); setState("idle"); return; }
    if (!executionId) return;
    let live = true;
    setState("loading");
    api.executionIssues(executionId)
      .then((r) => { if (live) { setReport(r); setState("idle"); } })
      .catch(() => { if (live) setState("error"); });
    return () => { live = false; };
  }, [executionId, injected]);

  if (state === "loading") return <div className="issues-report"><Skeleton rows={4} /></div>;
  if (state === "error")
    return (
      <div className="issues-report">
        <EmptyState icon="⚠" title="Couldn't load the issues report"
                    hint="The run may not have scored any cases yet. Try again once it finishes." />
      </div>
    );
  if (!report) return null;

  const s = report.summary;
  const sev = s.by_severity;

  return (
    <div className="issues-report">
      <div className="issues-summary">
        <div className="isum-head">
          <span className="isum-eyebrow">Issues</span>
          <h2 className="isum-headline">{s.headline}</h2>
        </div>
        <div className="isum-meta">
          {s.pass_rate != null && (
            <span className="isum-pass" title="Overall task success across scored cases">
              {Math.round(s.pass_rate * 100)}% passing
              <span className="isum-ci">
                <Uncertainty passes={s.n_passed} n={s.n_scored} />
              </span>
            </span>
          )}
          {!s.clean && (
            <span className="isum-counts">
              {sev.critical > 0 && <span className="sev-chip sev-critical">{sev.critical} critical</span>}
              {sev.high > 0 && <span className="sev-chip sev-high">{sev.high} high</span>}
              {sev.medium > 0 && <span className="sev-chip sev-medium">{sev.medium} medium</span>}
              {sev.low > 0 && <span className="sev-chip sev-low">{sev.low} low</span>}
            </span>
          )}
        </div>
      </div>

      {s.clean ? (
        <EmptyState icon="✓" title="No issues found"
                    hint="Every scored case passed, with no scoring errors or provisional scores. Nothing to fix here — re-run periodically or add harder cases to keep it honest." />
      ) : (
        <div className="issues-list">
          {report.issues.map((i) => <IssueCard key={i.id} issue={i} />)}
        </div>
      )}
    </div>
  );
}
