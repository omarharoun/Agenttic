import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api, errMessage,
  type CalibrationReport, type CalibrationRow, type CalibrationStatus,
  type NextUnlabeled, type LabelAnchor,
} from "../api";
import { DataView, EmptyState, PageHeader, RawToggle, Skeleton, Uncertainty } from "../components/ui";
import { PageData } from "../components/PageData";
import { DriftMonitor } from "../components/DriftMonitor";
import { IconTarget, IconCheck, IconWarning } from "../icons";

/* SPEC-4 Step 20.2 — calibration status + the labeling workspace.

   Two things live here:
   1. Per-criterion standing: how well the judge agrees with humans, how many
      labels back that number, and whether the criterion is trusted
      (calibrated), on notice (PROVISIONAL — enough labels but below threshold),
      or still gathering (insufficient labels).
   2. A labeling workspace: one un-labeled trace at a time, scored on the shared
      {0, 0.5, 1} scale. Every label feeds the flywheel, so labeling is the
      centerpiece, not an afterthought. */

const STATUS_META: Record<CalibrationStatus, { label: string; color: string }> = {
  calibrated: { label: "calibrated", color: "var(--ok)" },
  PROVISIONAL: { label: "provisional", color: "var(--wait)" },
  insufficient_labels: { label: "needs labels", color: "var(--muted)" },
};

function StatusBadge({ status }: { status: CalibrationStatus }) {
  const m = STATUS_META[status] ?? { label: status, color: "var(--muted)" };
  return (
    <span className="status-chip" style={{ color: m.color }}>{m.label}</span>
  );
}

function pct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(0)}%`;
}

// ---------------------------------------------------------------------------
// The labeling workspace — fetch one trace, score it, advance.
// ---------------------------------------------------------------------------
function LabelingWorkspace({ onLabeled }: { onLabeled: () => void }) {
  const [criterionId, setCriterionId] = useState("");
  const [active, setActive] = useState("");        // the criterion currently loaded
  const [task, setTask] = useState<NextUnlabeled | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<unknown | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const fetchNext = useCallback((crit: string) => {
    if (!crit.trim()) return;
    setLoading(true);
    setErr(null);
    setNote(null);
    api.nextUnlabeled(crit.trim())
      .then((t) => { setTask(t); setActive(crit.trim()); })
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, []);

  const submit = async (score: number) => {
    if (!task?.trace) return;
    setSaving(true);
    setErr(null);
    try {
      const res = await api.addLabel({
        trace_id: task.trace.trace_id,
        criterion_id: task.criterion.criterion_id,
        score,
      });
      setNote(res.criterion
        ? `Saved. ${res.criterion.criterion_id} is now ${STATUS_META[res.criterion.status]?.label ?? res.criterion.status} `
          + `(${res.criterion.label_count} labels, agreement ${pct(res.criterion.agreement)}).`
        : `Saved. ${res.label_count} labels on this suite.`);
      onLabeled();
      fetchNext(active);           // advance to the next trace
    } catch (e) {
      setErr(e);
    } finally {
      setSaving(false);
    }
  };

  const start = (e: React.FormEvent) => { e.preventDefault(); fetchNext(criterionId); };

  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <IconTarget />
        <h2 style={{ margin: 0, fontSize: 16 }}>Labeling workspace</h2>
      </div>
      <p className="muted-sm" style={{ marginTop: 6 }}>
        Score real traces on the same three-point scale the judge uses. Each label
        you add tightens the judge's agreement — this is what makes every other
        loop trustworthy.
      </p>

      <form onSubmit={start} style={{ display: "flex", gap: 8, margin: "10px 0 4px" }}>
        <label htmlFor="cal-criterion" className="sr-only">Criterion id</label>
        <input id="cal-criterion" value={criterionId}
          onChange={(e) => setCriterionId(e.target.value)}
          placeholder="criterion id to label" style={{ minWidth: 240 }} />
        <button type="submit" className="btn-primary" disabled={!criterionId.trim() || loading}>
          {loading ? "Loading…" : "Start labeling"}
        </button>
      </form>

      {err != null && (
        <div className="pagedata-error-msg" role="alert" style={{ marginTop: 8 }}>
          {errMessage(err)}
        </div>
      )}
      {note && (
        <div className="muted-sm" role="status" style={{ marginTop: 8, color: "var(--ok)" }}>
          <IconCheck /> {note}
        </div>
      )}

      {loading && <Skeleton rows={3} />}

      {!loading && task && task.exhausted && (
        <EmptyState icon={<IconCheck />} title="Every scored trace is labeled"
          hint={`No un-labeled traces remain for ${task.criterion.criterion_id}. Score more runs to generate new work.`} />
      )}

      {!loading && task && !task.exhausted && task.trace && (
        <div style={{ marginTop: 12 }}>
          <div className="muted-sm" style={{ marginBottom: 6 }}>
            criterion <b>{task.criterion.criterion_id}</b>
            {task.criterion.description && <> — {task.criterion.description}</>}
            {task.suite_id && <> · suite <span className="mono">{task.suite_id}</span></>}
          </div>
          <div className="card" style={{ padding: 12, background: "var(--panel-2)" }}>
            <div className="muted-sm">
              trace <span className="mono">{task.trace.trace_id}</span>
              {" · "}agent <span className="mono">{task.trace.agent_id}</span>
            </div>
            <h3 style={{ fontSize: 13, margin: "10px 0 4px" }}>Final output</h3>
            <pre className="doc" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
              {task.trace.final_output || "(no final output recorded)"}
            </pre>
            <div style={{ marginTop: 10 }}>
              <RawToggle value={task.trace.spans} label="trace steps (raw)" />
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <div className="muted-sm" style={{ marginBottom: 6 }}>
              How well does this output meet the criterion?
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {task.anchors.map((a: LabelAnchor) => (
                <button key={a.score} type="button" className="btn-ghost"
                  disabled={saving}
                  onClick={() => submit(a.score)}
                  style={{ flexDirection: "column", alignItems: "flex-start", minWidth: 150, padding: "8px 12px" }}>
                  <span style={{ fontWeight: 700 }}>{a.score}</span>
                  <span className="muted-sm">{a.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function CalibrationPage() {
  const [data, setData] = useState<CalibrationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    api.calibration()
      .then(setData)
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => load(), [load]);

  const criteria: CalibrationRow[] = data?.criteria ?? [];
  const summary = useMemo(() => {
    const s = { calibrated: 0, PROVISIONAL: 0, insufficient_labels: 0 };
    for (const c of criteria) s[c.status] = (s[c.status] ?? 0) + 1;
    return s;
  }, [criteria]);

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Calibration"
          subtitle="Whether you can trust the judge. For each criterion: how well the automated judge agrees with human labels, how many labels back that number, and whether it clears the bar. Below, a workspace to add the labels that move the needle." />

        <PageData
          loading={loading}
          error={err}
          empty={data != null && criteria.length === 0 && (data.open_requests?.length ?? 0) === 0}
          onRetry={load}
          errorTitle="Couldn't load calibration"
          skeleton={<Skeleton rows={6} />}
          emptyState={
            <EmptyState icon={<IconTarget />} title="No criteria to calibrate yet"
              hint="Once a suite is scored by a judge, its criteria appear here with their agreement and label counts." />
          }
        >
          <>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", margin: "4px 0 12px" }}>
              <span className="muted-sm"><b style={{ color: "var(--ok)" }}>{summary.calibrated}</b> calibrated</span>
              <span className="muted-sm"><b style={{ color: "var(--wait)" }}>{summary.PROVISIONAL}</b> provisional</span>
              <span className="muted-sm"><b style={{ color: "var(--muted)" }}>{summary.insufficient_labels}</b> need labels</span>
            </div>

            {criteria.length > 0 && (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>criterion</th><th>suite</th><th>agreement</th>
                      <th>labels</th><th>threshold</th><th>status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {criteria.map((c) => (
                      <tr key={`${c.suite_id}:${c.criterion_id}`}>
                        <td className="mono">{c.criterion_id}</td>
                        <td className="mono muted-sm">{c.suite_id}</td>
                        <td>
                          {pct(c.agreement)}
                          {c.paired_count > 0 && (
                            <Uncertainty n={c.paired_count} rate={c.agreement ?? 0} />
                          )}
                        </td>
                        <td>
                          {c.label_count}
                          {c.label_count < c.min_labels && (
                            <span className="muted-sm"> / {c.min_labels} needed</span>
                          )}
                        </td>
                        <td>{pct(c.threshold)}</td>
                        <td><StatusBadge status={c.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {(data?.open_requests?.length ?? 0) > 0 && (
              <div style={{ marginTop: 22 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <IconWarning />
                  <h2 style={{ margin: 0, fontSize: 16 }}>Open judge-optimization requests</h2>
                </div>
                <p className="muted-sm" style={{ marginTop: 4 }}>
                  Criteria the system has flagged for a judge tune-up — usually because
                  agreement slipped or new labels disagree with the current judge.
                </p>
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr><th>criterion</th><th>suite</th><th>reason</th><th>status</th><th>opened</th></tr>
                    </thead>
                    <tbody>
                      {data!.open_requests.map((r) => (
                        <tr key={r.request_id}>
                          <td className="mono">{r.criterion_id}</td>
                          <td className="mono muted-sm">{r.suite_id}</td>
                          <td>{r.reason || <DataView value={null} />}</td>
                          <td><span className="status-chip">{r.status}</span></td>
                          <td className="muted-sm">{new Date(r.created_at).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div style={{ marginTop: 22 }}>
              <LabelingWorkspace onLabeled={load} />
            </div>
          </>
        </PageData>

        {/* SPEC-5 23.4 — live drift strip-chart per criterion, re-derived from
            the raw rolling window through sim-core. */}
        <section className="cal-drift" aria-label="Live drift" style={{ marginTop: 28 }}>
          <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>Live drift</h2>
          <p className="muted-sm" style={{ marginTop: 0 }}>
            Rolling live means vs the batch baseline for the pilot support-triage
            agent. A criterion drifts when its mean falls more than the threshold
            below baseline — then a re-evaluation is requested.
          </p>
          <DriftMonitor agentId="support-triage-agent" baselineScorecardId="sc_base" />
        </section>
      </div>
    </div>
  );
}
