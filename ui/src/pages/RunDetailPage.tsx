import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api, errMessage,
  type Execution, type ExecutionResults, type ExecutionCaseRow,
  type ExecutionScorecardSummary, type Trace, type Span,
} from "../api";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";
import { PageData } from "../components/PageData";
import { PipelineFlow } from "../components/PipelineFlow";
import { useFlowStore, emptyExec, type NodeRunState } from "../store";
import { useExecutionEvents } from "../sse";
import {
  IconPlay, IconCheck, IconClose, IconWaiting, IconError, IconClose as IconX,
} from "../icons";
import "./RunDetail.css";

/* ============================================================================
 * SPEC-4 Step 19.2 — the Run detail live view.
 *
 * Mounts at /app/runs/:id. The initial execution + results are fetched once
 * (PageData owns loading/empty/error+retry), but the interesting part is LIVE:
 * `useExecutionEvents(id)` (from ../sse) subscribes to the server's SSE stream
 * and feeds every event into the shared flow store's `exec` slice. We read that
 * slice with `useFlowStore((s) => s.exec)`, so the per-node/per-case progress
 * table and the selected case's streaming trace preview update WITHOUT a
 * refetch. On completion the results carry a scorecard + per-case trace ids, and
 * we surface the "View scorecard →" handoff and per-case trace links.
 *
 * Colours/spacing come exclusively from theme.css tokens (var(--…)); no raw hex.
 * ==========================================================================*/

const TERMINAL = new Set([
  "succeeded", "failed", "cancelled", "completed_with_errors",
]);

const STATE_TONE: Record<string, string> = {
  running: "run-tone-running",
  waiting: "run-tone-waiting",
  waiting_approval: "run-tone-waiting",
  succeeded: "run-tone-ok",
  failed: "run-tone-fail",
  cancelled: "run-tone-fail",
  completed_with_errors: "run-tone-waiting",
  skipped: "run-tone-muted",
  idle: "run-tone-muted",
  pending: "run-tone-muted",
};

function StateIcon({ state }: { state: string }) {
  switch (state) {
    case "succeeded":
      return <IconCheck aria-hidden />;
    case "failed":
    case "cancelled":
      return <IconClose aria-hidden />;
    case "waiting":
    case "waiting_approval":
      return <IconWaiting aria-hidden />;
    case "running":
    case "pending":
      return <span className="run-spinner" aria-hidden />;
    default:
      return <span className="run-dot" aria-hidden />;
  }
}

function fmtCost(usd: number | null | undefined): string {
  if (usd == null) return "—";
  return `$${usd.toFixed(usd < 0.01 ? 4 : 2)}`;
}

/** One row of the LIVE per-node progress table. Its status/steps/cost come
 *  from the live store slice (running/waiting/done), falling back to the
 *  fetched execution when the stream hasn't spoken about the node yet. */
interface NodeRow {
  nodeId: string;
  state: string;
  done: number;
  total: number;
}

/** The streaming span timeline for the selected case's trace. Spans are laid
 *  out on a shared time axis (llm_call / tool_call / error as coloured rows);
 *  it fills in as `getTrace` returns more spans while the run progresses. */
function TraceTimeline({ trace }: { trace: Trace }) {
  const spans = trace.spans ?? [];
  const bounds = useMemo(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const s of spans) {
      const a = Date.parse(s.start_time);
      const b = Date.parse(s.end_time);
      if (!Number.isNaN(a)) min = Math.min(min, a);
      if (!Number.isNaN(b)) max = Math.max(max, b);
    }
    if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) {
      return { min: 0, span: 1 };
    }
    return { min, span: max - min };
  }, [spans]);

  if (spans.length === 0) {
    return <p className="run-muted">No spans recorded yet — the trace fills in as the run progresses.</p>;
  }

  return (
    <ol className="run-timeline" aria-label="Span timeline">
      {spans.map((s: Span) => {
        const a = Date.parse(s.start_time);
        const b = Date.parse(s.end_time);
        const left = Number.isNaN(a) ? 0 : ((a - bounds.min) / bounds.span) * 100;
        const width = Number.isNaN(a) || Number.isNaN(b)
          ? 2
          : Math.max(1.5, ((b - a) / bounds.span) * 100);
        return (
          <li key={s.span_id} className="run-span-row">
            <span className="run-span-label" title={s.name}>
              <span className={`run-span-kind kind-${s.kind}`}>{s.kind.replace(/_/g, " ")}</span>
              <span className="run-span-name">{s.name}</span>
            </span>
            <span className="run-span-track">
              <span
                className={`run-span-bar kind-${s.kind}`}
                style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
              />
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function RunDetailPage() {
  const { id = "" } = useParams<{ id: string }>();

  // --- initial fetch (PageData owns the trio) ---
  const [detail, setDetail] = useState<Execution | null>(null);
  const [results, setResults] = useState<ExecutionResults | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);

  // --- live slice from the SSE-fed flow store ---
  const exec = useFlowStore((s) => s.exec);
  const setExec = useFlowStore((s) => s.setExec);

  // Subscribe to the live event stream. The hook writes into the store's `exec`
  // slice via pushEvent; we consume it above. It reconnects/resumes on its own.
  useExecutionEvents(id || null);

  const load = useCallback(() => {
    if (!id) return;
    setLoading(true);
    setErr(null);
    // seed the live store with the current execution so the SSE stream merges
    // onto a real baseline (states/status) instead of an empty one.
    api.getExecution(id)
      .then((ex) => {
        setDetail(ex);
        setExec({
          executionId: ex.execution_id,
          status: ex.status,
          nodeStates: (ex.node_states as Record<string, NodeRunState>) ?? {},
          progress: {},
          log: [],
        });
        return api.executionResults(id).then(setResults).catch(() => setResults(null));
      })
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, [id, setExec]);

  useEffect(() => {
    load();
    return () => setExec(emptyExec());
  }, [load, setExec]);

  // Live status: prefer the streamed status; fall back to the fetched one.
  const liveStatus = exec.executionId === id ? exec.status : (detail?.status ?? "pending");
  const isTerminal = TERMINAL.has(liveStatus);

  // Build the live per-node rows by unioning node ids from the fetched execution
  // and the streamed states, so a node shows up the moment either source knows it.
  const nodeRows: NodeRow[] = useMemo(() => {
    const ids = new Set<string>();
    for (const k of Object.keys(detail?.node_states ?? {})) ids.add(k);
    for (const k of Object.keys(exec.nodeStates)) ids.add(k);
    for (const k of Object.keys(exec.progress)) ids.add(k);
    return [...ids].sort().map((nodeId) => {
      const state = exec.nodeStates[nodeId]
        ?? (detail?.node_states?.[nodeId] as string | undefined)
        ?? "idle";
      const p = exec.progress[nodeId];
      return { nodeId, state, done: p?.done ?? 0, total: p?.total ?? 0 };
    });
  }, [detail, exec.nodeStates, exec.progress]);

  // --- per-case rows (from results once available) ---
  const cases: ExecutionCaseRow[] = results?.cases ?? [];
  const scorecards: ExecutionScorecardSummary[] = results?.scorecards ?? [];

  // --- selected case + its streaming trace ---
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [traceErr, setTraceErr] = useState<unknown | null>(null);

  // A case's trace id comes from the scorecard run_scores; the results per-case
  // row doesn't carry it directly, so we derive a stable case→trace map from the
  // results cases in order they appear (the trace list is keyed elsewhere). We
  // expose the trace id via the case row's node/test pairing when present.
  const caseTraceId = useCallback((c: ExecutionCaseRow): string | null => {
    const raw = (c as unknown as { trace_id?: string }).trace_id;
    return raw ?? null;
  }, []);

  useEffect(() => {
    if (!selectedTrace) { setTrace(null); return; }
    let live = true;
    setTraceErr(null);
    const fetchTrace = () => {
      api.getTrace(selectedTrace)
        .then((t) => { if (live) setTrace(t); })
        .catch((e) => { if (live) setTraceErr(e); });
    };
    fetchTrace();
    // while the run is still going, poll the trace so the timeline fills in.
    const t = isTerminal ? null : setInterval(fetchTrace, 2000);
    return () => { live = false; if (t) clearInterval(t); };
  }, [selectedTrace, isTerminal]);

  const selectCase = useCallback((c: ExecutionCaseRow) => {
    const tid = caseTraceId(c);
    setSelectedTrace(tid ?? `${c.node_id}:${c.test_id}`);
  }, [caseTraceId]);

  const cancel = useCallback(() => {
    if (!id) return;
    api.cancel(id).then(() => load()).catch(() => { /* surfaced via live status */ });
  }, [id, load]);

  const statusLabel = liveStatus.replace(/_/g, " ");
  const canCancel = !isTerminal && liveStatus !== "idle";

  const empty = detail == null;

  return (
    <div className="page">
      <div className="run-detail">
        <PageHeader
          title="Run detail"
          subtitle="Live per-case progress as the run scores — then the scorecard and per-case traces it produced."
          actions={
            <div className="run-header-actions">
              <span
                className={`run-status-chip ${STATE_TONE[liveStatus] ?? "run-tone-muted"}`}
                role="status"
                aria-live="polite"
              >
                <StateIcon state={liveStatus} /> {statusLabel}
              </span>
              {canCancel && (
                <button type="button" className="btn-ghost run-cancel" onClick={cancel}>
                  <IconX aria-hidden /> Cancel run
                </button>
              )}
            </div>
          }
        />

        <PageData
          loading={loading}
          error={err}
          empty={empty}
          onRetry={load}
          errorTitle="Couldn't load this run"
          skeleton={<Skeleton rows={6} />}
          emptyState={
            <EmptyState
              icon={<IconPlay />}
              title="Run not found"
              hint="This run may not exist yet, or it belongs to another workspace."
            />
          }
        >
          <>
            <div className="run-meta run-muted">
              run <span className="mono">{id}</span>
              {detail?.workflow_id && <> · workflow <span className="mono">{detail.workflow_id}</span></>}
              {detail?.started_at && <> · started {new Date(detail.started_at).toLocaleTimeString()}</>}
            </div>

            {detail?.error_reason && (
              <div className="run-error-banner" role="alert">
                <IconError aria-hidden /> {detail.error_reason}
                {detail.error && <span className="run-muted"> — {detail.error}</span>}
              </div>
            )}

            {/* -------- LIVE per-node progress -------- */}
            <section aria-label="Live node progress" className="run-section">
              <h2 className="run-h2">Progress</h2>
              {/* SPEC-5 23.1: the pipeline as a live lane diagram, driven by the
                  same SSE exec slice; the table below stays the source of record. */}
              <PipelineFlow exec={exec} />
              {nodeRows.length === 0 ? (
                <p className="run-muted">Waiting for the run to report progress…</p>
              ) : (
                <div className="table-wrap">
                  <table className="data run-nodes">
                    <thead>
                      <tr><th>node</th><th>status</th><th>steps</th><th>progress</th></tr>
                    </thead>
                    <tbody aria-live="polite">
                      {nodeRows.map((r) => (
                        <tr key={r.nodeId}>
                          <td className="mono">{r.nodeId}</td>
                          <td>
                            <span className={`run-state ${STATE_TONE[r.state] ?? "run-tone-muted"}`}>
                              <StateIcon state={r.state} /> {r.state}
                            </span>
                          </td>
                          <td className="mono">
                            {r.total > 0 ? `${r.done} / ${r.total}` : (r.done > 0 ? r.done : "—")}
                          </td>
                          <td>
                            <span className="run-bar" aria-hidden>
                              <span
                                className={`run-bar-fill ${STATE_TONE[r.state] ?? "run-tone-muted"}`}
                                style={{ width: r.total > 0 ? `${Math.min(100, (r.done / r.total) * 100)}%` : (TERMINAL.has(r.state) ? "100%" : "0%") }}
                              />
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* -------- LIVE per-case table + trace preview -------- */}
            {cases.length > 0 && (
              <section aria-label="Per-case results" className="run-section">
                <h2 className="run-h2">Cases</h2>
                <div className="run-split">
                  <div className="table-wrap run-cases-wrap">
                    <table className="data run-cases">
                      <thead>
                        <tr><th>case</th><th>result</th><th>steps</th><th>cost</th><th>trace</th></tr>
                      </thead>
                      <tbody aria-live="polite">
                        {cases.map((c) => {
                          const key = `${c.node_id}:${c.test_id}`;
                          const tid = caseTraceId(c);
                          const selected = selectedTrace === (tid ?? key);
                          const result = c.scoring_error
                            ? "errored"
                            : c.passed ? "passed" : "failed";
                          return (
                            <tr
                              key={key}
                              className={selected ? "run-case-selected" : ""}
                              tabIndex={0}
                              role="button"
                              aria-pressed={selected}
                              aria-label={`Case ${c.test_id} — ${result}. Show trace timeline.`}
                              onClick={() => selectCase(c)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  selectCase(c);
                                }
                              }}
                            >
                              <td className="mono">{c.test_id}</td>
                              <td>
                                <span className={`run-state ${
                                  result === "passed" ? "run-tone-ok"
                                    : result === "errored" ? "run-tone-waiting" : "run-tone-fail"}`}>
                                  <StateIcon state={result === "passed" ? "succeeded" : result === "errored" ? "waiting" : "failed"} />
                                  {result}
                                </span>
                              </td>
                              <td className="mono">{c.steps ?? "—"}</td>
                              <td className="mono">{fmtCost(c.cost_usd)}</td>
                              <td>
                                {tid ? (
                                  <Link
                                    to={`/app/traces/${tid}`}
                                    className="run-link"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    view →
                                  </Link>
                                ) : <span className="run-muted">—</span>}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  <div className="run-trace-panel" aria-label="Trace timeline preview">
                    <h3 className="run-h3">Trace timeline</h3>
                    {!selectedTrace ? (
                      <p className="run-muted">Select a case to preview its span timeline.</p>
                    ) : traceErr ? (
                      <p className="run-muted">{errMessage(traceErr)}</p>
                    ) : trace ? (
                      <TraceTimeline trace={trace} />
                    ) : (
                      <p className="run-muted">Loading trace…</p>
                    )}
                  </div>
                </div>
              </section>
            )}

            {/* -------- Completion handoff: View scorecard -------- */}
            {isTerminal && scorecards.length > 0 && (
              <section aria-label="Scorecards" className="run-section">
                <h2 className="run-h2">Scorecards</h2>
                <div className="run-scorecards">
                  {scorecards.map((sc) => (
                    <div className="run-scorecard-card" key={sc.scorecard_id}>
                      <div className="run-sc-head">
                        <span className="mono run-muted">{sc.agent_id}</span>
                        <span className="run-sc-rate">
                          {Math.round((sc.task_success_rate ?? 0) * 100)}% pass
                          <span className="run-muted"> · {sc.n_passed}/{sc.n_scored}</span>
                        </span>
                      </div>
                      <Link className="btn-primary run-view-scorecard" to={`/app/scorecards/${sc.scorecard_id}`}>
                        View scorecard →
                      </Link>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        </PageData>
      </div>
    </div>
  );
}
