import { useEffect, useState } from "react";
import { api, downloadBlob } from "../api";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";

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

  const refresh = (t: Tab) => {
    setDoc(""); setLoaded(false);
    (t === "suites" ? api.listSuites()
      : t === "scorecards" ? api.listScorecards()
      : api.listTraces())
      .then((r) => { setRows(r); setLoaded(true); })
      .catch(() => { setRows([]); setLoaded(true); });
  };
  useEffect(() => refresh(tab), [tab]);

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
                        <button onClick={() =>
                          api.scorecardReport(s.scorecard_id).then(setDoc)}>
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
                  {rows.map((t) => (
                    <tr key={t.trace_id}>
                      <td className="mono">{t.trace_id.slice(0, 12)}</td>
                      <td>{t.agent_id}</td>
                      <td>{t.test_case_id ?? "live"}</td>
                      <td className="num">{t.n_spans}</td>
                      <td>{t.final_output}</td>
                      <td><button onClick={() =>
                        api.getTrace(t.trace_id).then((full) =>
                          setDoc(JSON.stringify(full.spans, null, 2)))}>
                        spans
                      </button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {doc && <pre className="doc" style={{ marginTop: 16 }}>{doc}</pre>}
      </div>
    </div>
  );
}
