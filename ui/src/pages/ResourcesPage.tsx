import { useEffect, useRef, useState } from "react";
import { api, downloadBlob } from "../api";
import { DataView, EmptyState, PageHeader, RawToggle, Skeleton } from "../components/ui";
import { shortTraceId, traceId } from "../traces";

type Tab = "suites" | "scorecards" | "traces";

const TAB_LABEL: Record<Tab, string> = {
  suites: "Suites", scorecards: "Scorecards", traces: "Traces",
};
const EMPTY: Record<Tab, { icon: string; title: string; hint: string }> = {
  suites: { icon: "▤", title: "No suites yet", hint: "Generate a benchmark suite from a guided workflow to see it here." },
  scorecards: { icon: "◇", title: "No scorecards yet", hint: "Run a suite against an agent — scored results land here." },
  traces: { icon: "≋", title: "No traces yet", hint: "Agent runs emit execution traces you can inspect span-by-span." },
};

export function ResourcesPage() {
  const [tab, setTab] = useState<Tab>("suites");
  const [rows, setRows] = useState<any[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [doc, setDoc] = useState<string>("");
  const [report, setReport] = useState<{ id: string; text: string } | null>(null);
  const [spans, setSpans] = useState<any[] | null>(null);
  const reportRef = useRef<HTMLDivElement>(null);
  const spansRef = useRef<HTMLDivElement>(null);

  const refresh = (t: Tab) => {
    setDoc(""); setReport(null); setSpans(null); setLoaded(false);
    (t === "suites" ? api.listSuites()
      : t === "scorecards" ? api.listScorecards()
      : api.listTraces())
      .then((r) => { setRows(r); setLoaded(true); })
      .catch(() => { setRows([]); setLoaded(true); });
  };
  useEffect(() => refresh(tab), [tab]);

  // Open a scorecard's report inline, in a titled panel consistent with the
  // Results history page — then scroll it into view so the click has a visible
  // effect even when the row sits above the fold of a long table.
  const openReport = (id: string) => {
    setReport({ id, text: "Loading report…" });
    api.scorecardReport(id)
      .then((text) => setReport({ id, text }))
      .catch(() => setReport({
        id, text: "⚠ Could not load this report. Please try again.",
      }));
  };
  useEffect(() => {
    if (report) reportRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [report]);

  // Open a trace's spans inline, then scroll the panel into view — same as the
  // report drill-down. Without this the panel renders below a long traces table
  // and the click looks dead; a failed fetch must surface, not fail silently.
  const openSpans = (id: string) => {
    setDoc("");
    api.getTrace(id)
      .then((full) => setSpans(full.spans ?? []))
      .catch(() => setSpans([]));
  };
  useEffect(() => {
    if (spans) spansRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [spans]);

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader title="Resources"
          subtitle="Benchmark suites, scorecards, and execution traces generated across your workspace." />
        <div className="tabs" role="tablist">
          {(["suites", "scorecards", "traces"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "active" : ""} role="tab"
                    aria-selected={tab === t} onClick={() => setTab(t)}>{TAB_LABEL[t]}</button>
          ))}
        </div>

        {!loaded ? <Skeleton rows={6} /> : rows.length === 0 ? (
          <EmptyState {...EMPTY[tab]} />
        ) : (
          <div className="table-wrap">
            {tab === "suites" && (
              <table className="data">
                <thead><tr><th>suite</th><th>version</th><th className="num">cases</th>
                           <th>status</th><th></th></tr></thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.suite_id}>
                      <td>{s.suite_id}</td>
                      <td>v{s.version}</td>
                      <td className="num">{s.n_cases}</td>
                      <td style={{ color: s.approved ? "var(--ok)" : "var(--wait)" }}>
                        {s.approved ? "approved" : "DRAFT"}
                      </td>
                      <td>
                        <button onClick={() =>
                          api.suiteReview(s.suite_id).then((t) =>
                            setDoc(t || "(no review file)"))}>
                          review
                        </button>
                        {!s.approved && (
                          <button className="approve" style={{ marginLeft: 6 }}
                                  onClick={() => api.approveSuite(s.suite_id, s.version)
                                    .then(() => refresh("suites"))}>
                            approve
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {tab === "scorecards" && (
              <table className="data">
                <thead><tr><th>scorecard</th><th>agent</th><th>suite</th>
                           <th className="num">success</th><th className="num">mean cost</th><th>tier</th><th></th></tr></thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.scorecard_id}>
                      <td className="mono">{s.scorecard_id}</td>
                      <td>{s.agent_id}</td>
                      <td>{s.suite_id} v{s.suite_version}</td>
                      <td className="num">{Math.round((s.task_success_rate ?? 0) * 100)}%</td>
                      <td className="num">${(s.mean_cost_usd ?? 0).toFixed(4)}</td>
                      <td>{s.visibility_tier?.replace("_", "-")}</td>
                      <td>
                        <button onClick={() => openReport(s.scorecard_id)}>
                          report
                        </button>
                        <button style={{ marginLeft: 6 }} title="Download as PDF"
                                onClick={() => api.scorecardPdf(s.scorecard_id)
                                  .then((b) => downloadBlob(b, `scorecard-${s.scorecard_id}.pdf`))
                                  .catch(() => {})}>
                          ⤓ PDF
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {tab === "traces" && (
              <table className="data">
                <thead><tr><th>trace</th><th>agent</th><th>case</th><th className="num">spans</th>
                           <th>output</th><th></th></tr></thead>
                <tbody>
                  {rows.map((t, i) => {
                    const id = traceId(t);
                    return (
                    <tr key={id || `trace-${i}`}>
                      <td className="mono">{shortTraceId(t)}</td>
                      <td>{t.agent_id}</td>
                      <td>{t.test_case_id ?? "live"}</td>
                      <td className="num">{t.n_spans}</td>
                      <td>{t.final_output}</td>
                      <td><button disabled={!id} onClick={() => openSpans(id)}>
                        spans
                      </button></td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}

        {doc && <pre className="doc" style={{ marginTop: 16 }}>{doc}</pre>}

        {report && (
          <div ref={reportRef} style={{ marginTop: 16 }}>
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              Report · <span className="mono">{report.id}</span>
              <button className="ghost-sm" style={{ marginLeft: 8 }}
                      onClick={() => setReport(null)}>close</button>
            </div>
            <pre className="doc" style={{ marginTop: 0 }}>{report.text}</pre>
          </div>
        )}

        {spans && (
          <div ref={spansRef} className="span-view" style={{ marginTop: 16 }}>
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              Trace spans ({spans.length})
              <button className="ghost-sm" style={{ marginLeft: 8 }}
                      onClick={() => setSpans(null)}>close</button>
            </div>
            {spans.length === 0 ? (
              <p className="muted-sm">This trace has no spans.</p>
            ) : (
              spans.map((s, i) => (
                <div className="no-card" key={i}>
                  <div className="no-head mono">
                    {s.name ?? s.span_type ?? s.type ?? `span ${i + 1}`}
                  </div>
                  <DataView value={s} />
                  <RawToggle value={s} />
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
