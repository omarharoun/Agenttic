import { useEffect, useState } from "react";
import { api, downloadBlob } from "../api";

type Tab = "suites" | "scorecards" | "traces";

export function ResourcesPage() {
  const [tab, setTab] = useState<Tab>("suites");
  const [rows, setRows] = useState<any[]>([]);
  const [doc, setDoc] = useState<string>("");

  const refresh = (t: Tab) => {
    setDoc("");
    (t === "suites" ? api.listSuites()
      : t === "scorecards" ? api.listScorecards()
      : api.listTraces()).then(setRows);
  };
  useEffect(() => refresh(tab), [tab]);

  return (
    <div className="page">
      <div className="list-page">
        <div className="tabs">
          {(["suites", "scorecards", "traces"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "active" : ""}
                    onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>

        {tab === "suites" && (
          <table className="data">
            <thead><tr><th>suite</th><th>version</th><th>cases</th>
                       <th>status</th><th></th></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.suite_id}>
                  <td>{s.suite_id}</td>
                  <td>v{s.version}</td>
                  <td>{s.n_cases}</td>
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
                       <th>success</th><th>mean cost</th><th>tier</th><th></th></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.scorecard_id}>
                  <td style={{ fontFamily: "monospace" }}>{s.scorecard_id}</td>
                  <td>{s.agent_id}</td>
                  <td>{s.suite_id} v{s.suite_version}</td>
                  <td>{Math.round((s.task_success_rate ?? 0) * 100)}%</td>
                  <td>${(s.mean_cost_usd ?? 0).toFixed(4)}</td>
                  <td>{s.visibility_tier}</td>
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
            <thead><tr><th>trace</th><th>agent</th><th>case</th><th>spans</th>
                       <th>output</th><th></th></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.trace_id}>
                  <td style={{ fontFamily: "monospace" }}>
                    {t.trace_id.slice(0, 12)}
                  </td>
                  <td>{t.agent_id}</td>
                  <td>{t.test_case_id ?? "live"}</td>
                  <td>{t.n_spans}</td>
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

        {doc && <pre className="doc" style={{ marginTop: 16 }}>{doc}</pre>}
      </div>
    </div>
  );
}
