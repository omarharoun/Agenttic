import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";
import { Term } from "../components/Term";

const pct = (x: number | null | undefined) =>
  x == null ? "—" : `${Math.round(x * 100)}%`;
const signedPct = (x: number | null | undefined) =>
  x == null ? "—" : `${x >= 0 ? "+" : ""}${Math.round(x * 100)}pp`;

const STATUS_COLOR: Record<string, string> = {
  improved: "var(--ok)", regressed: "var(--fail)", same: "var(--muted)",
  new: "var(--info)", errored: "var(--wait)",
};

/** A compact summary of a regression delta: improved / regressed / same / new. */
function DeltaChips({ d }: { d: Record<string, number> | null | undefined }) {
  if (!d) return <span className="delta-empty">not run yet</span>;
  const order = ["improved", "regressed", "same", "new", "errored"] as const;
  if (order.every((k) => !d[k])) return <span className="delta-empty">no cases</span>;
  return (
    <span className="delta-chips">
      {order.filter((k) => d[k]).map((k) => (
        <span key={k} className="delta-chip" style={{ color: STATUS_COLOR[k] }}>
          <span className="n">{d[k]}</span> {k}
        </span>
      ))}
    </span>
  );
}

type RerunCfg = {
  variant: string; model: string; system_prompt: string; url: string;
  managed_agent_id: string; environment_id: string;
};
const blankRerun = (): RerunCfg => ({
  variant: "reference", model: "", system_prompt: "", url: "",
  managed_agent_id: "", environment_id: "",
});

/** Re-run a regression suite to prove the fix held. Single-agent config — the
 * agent_id is fixed (it's the agent the suite hardens). */
function RerunForm({ suiteId, onStarted }: {
  suiteId: string; onStarted: () => void;
}) {
  const [cfg, setCfg] = useState<RerunCfg>(blankRerun());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (patch: Partial<RerunCfg>) => setCfg({ ...cfg, ...patch });

  const run = async () => {
    setErr(null); setBusy(true);
    try {
      await api.rerunRegression({ regression_suite_id: suiteId, ...cfg });
      onStarted();
    } catch (e: any) {
      setErr(String(e.message ?? e));
    } finally { setBusy(false); }
  };

  return (
    <div className="policy-box" style={{ marginTop: 12 }}>
      <div className="policy-title">re-run to prove the fix held</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <div>
          <label>kind</label>
          <select value={cfg.variant} onChange={(e) => set({ variant: e.target.value })}>
            <option value="reference">Built-in reference agent</option>
            <option value="blackbox">Your API agent (external endpoint)</option>
            <option value="managed">Managed agent (deployed)</option>
          </select>
        </div>
        {cfg.variant === "reference" && (
          <div>
            <label>model <small>(optional)</small></label>
            <input value={cfg.model} placeholder="blank = default"
                   onChange={(e) => set({ model: e.target.value })} />
          </div>
        )}
        {cfg.variant === "reference" && (
          <div style={{ gridColumn: "1 / -1" }}>
            <label>system_prompt <small>(the fixed instructions under test)</small></label>
            <textarea value={cfg.system_prompt} rows={3}
                      onChange={(e) => set({ system_prompt: e.target.value })} />
          </div>
        )}
        {cfg.variant === "blackbox" && (
          <div style={{ gridColumn: "1 / -1" }}>
            <label>url *</label>
            <input value={cfg.url} placeholder="https://…/agent"
                   onChange={(e) => set({ url: e.target.value })} />
          </div>
        )}
        {cfg.variant === "managed" && (
          <>
            <div><label>managed_agent_id *</label>
              <input value={cfg.managed_agent_id}
                     onChange={(e) => set({ managed_agent_id: e.target.value })} /></div>
            <div><label>environment_id *</label>
              <input value={cfg.environment_id}
                     onChange={(e) => set({ environment_id: e.target.value })} /></div>
          </>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
        <button className="primary" disabled={busy} onClick={run}>
          {busy ? "Starting…" : "Re-run regression suite"}
        </button>
        {err && <span role="alert" style={{ color: "var(--fail)", fontSize: 12 }}>⚠ {err}</span>}
      </div>
    </div>
  );
}

/** Detail for one regression suite: its cases (+ why each was caught), run
 * history, the latest per-case delta, and the re-run control. */
function SuiteDetail({ suiteId, onBack }: { suiteId: string; onBack: () => void }) {
  const [d, setD] = useState<any | null | undefined>(undefined);
  const [pollFrom, setPollFrom] = useState<number | null>(null);

  const load = () => api.hardeningDetail(suiteId).then(setD).catch(() => setD(null));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [suiteId]);

  // after a re-run is kicked off, poll until a new scorecard lands in history
  useEffect(() => {
    if (pollFrom == null) return;
    let live = true;
    const tick = () => api.hardeningDetail(suiteId).then((nd) => {
      if (!live) return;
      setD(nd);
      if ((nd.history?.length ?? 0) > pollFrom) { setPollFrom(null); return; }
      setTimeout(tick, 2500);
    }).catch(() => {});
    const t = setTimeout(tick, 2500);
    return () => { live = false; clearTimeout(t); };
  }, [pollFrom, suiteId]);

  if (d === undefined) return <Skeleton rows={6} />;
  if (d === null) return <p style={{ color: "var(--fail)" }}>Could not load suite.</p>;

  const delta = d.latest_delta;
  const runs = d.history?.length ?? 0;
  return (
    <div style={{ marginBottom: 20 }}>
      <button className="ghost-sm" onClick={onBack}>← back to suites</button>
      <h2 className="mono" style={{ marginTop: 8 }}>{d.regression_suite_id}</h2>
      <p style={{ color: "var(--muted)", marginTop: -4 }}>
        agent <b className="mono">{d.agent_id}</b> · hardened from{" "}
        <span className="mono">{d.source_suite_id || "—"}</span>
        {pollFrom != null && <> · <span className="spinner" /> re-running…</>}
      </p>

      <div className="score-strip" role="group" aria-label="Regression suite summary">
        <div className="stat"><span className="lab">Cases</span>
          <span className="val">{d.cases.length}</span></div>
        <div className="stat"><span className="lab">Version</span>
          <span className="val">v{d.version}</span></div>
        <div className="stat"><span className="lab">Re-runs</span>
          <span className="val">{runs}</span></div>
        {delta && (
          <div className="stat"><span className="lab">Latest success</span>
            <span className="val sm">{pct(delta.task_success_rate)}</span></div>
        )}
        {delta && delta.success_delta != null && (
          <div className="stat"><span className="lab">Δ vs prior</span>
            <span className={"val sm " + (delta.success_delta > 0 ? "ok"
              : delta.success_delta < 0 ? "err" : "")}>
              {signedPct(delta.success_delta)}</span></div>
        )}
      </div>

      {delta && (
        <>
          <h3 style={{ marginTop: 16 }}>Latest regression delta</h3>
          <div className="verdict" style={{ marginBottom: 10 }}>
            <div className="v-lab">
              vs prior run <DeltaChips d={delta.summary} />
            </div>
            <div className="v-text">
              Success {pct(delta.prev_task_success_rate)} → {pct(delta.task_success_rate)}{" "}
              (<b style={{ color: (delta.success_delta ?? 0) > 0 ? "var(--ok)"
                : (delta.success_delta ?? 0) < 0 ? "var(--fail)" : "var(--muted)" }}>
                {signedPct(delta.success_delta)}</b>)
              {delta.mcnemar?.significant && " — change is statistically significant"}
            </div>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>test case</th><th>before</th><th>after</th><th>status</th></tr></thead>
              <tbody>
                {delta.per_case.map((c: any) => (
                  <tr key={c.test_id}>
                    <td className="mono">{c.test_id}</td>
                    <td className={c.prev_passed == null ? "" : c.prev_passed ? "ok" : "err"}>
                      {c.prev_passed == null ? "—" : c.prev_passed ? "PASS" : "FAIL"}</td>
                    <td className={c.now_passed == null ? "" : c.now_passed ? "ok" : "err"}>
                      {c.now_passed == null ? "—" : c.now_passed ? "PASS" : "FAIL"}</td>
                    <td style={{ color: STATUS_COLOR[c.status] }}>{c.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <RerunForm suiteId={suiteId} onStarted={() => setPollFrom(d.history?.length ?? 0)} />

      <h3 style={{ marginTop: 16 }}>Cases & why they were caught</h3>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>test case</th><th>task</th><th>why caught</th></tr></thead>
          <tbody>
            {d.cases.map((c: any) => (
              <tr key={c.test_id}>
                <td className="mono">{c.test_id}</td>
                <td style={{ maxWidth: 320, fontSize: 12 }}>{c.task_description}</td>
                <td style={{ fontSize: 12, color: "var(--muted)" }}>
                  {c.provenance?.why ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 style={{ marginTop: 16, color: "var(--muted)" }}>Run history</h3>
      {d.history.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>Not re-run yet — run it to start tracking the delta.</p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>scorecard</th><th>v</th><th className="num">success</th>
              <th className="num">cases</th><th className="num">errored</th><th>when</th></tr></thead>
            <tbody>
              {d.history.slice().reverse().map((h: any) => (
                <tr key={h.scorecard_id}>
                  <td className="mono">{h.scorecard_id}</td>
                  <td>{h.suite_version}</td>
                  <td className="num">{pct(h.task_success_rate)}</td>
                  <td className="num">{h.n_cases}</td>
                  <td className="num">{h.errored}</td>
                  <td>{new Date(h.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Live-monitor catches: below-threshold sampled production traces. Promoting
 * one reconstructs a regression case from the trace's input and lands it in a
 * needs-review suite — we never fabricate the ground truth a live trace lacks,
 * so each promoted case wants a human's eyes (verify input + attach a rubric).
 * Deliberately distinct from the scorecard candidates below. */
function LiveCatches({ onPromoted }: { onPromoted: (msg: string) => void }) {
  const [catches, setCatches] = useState<any[] | null>(null);
  const [rubric, setRubric] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => api.hardeningLiveCandidates()
    .then((r) => setCatches(r.candidates)).catch(() => setCatches([]));
  useEffect(() => { load(); }, []);

  const promote = async (c: any) => {
    setBusy(c.trace_id);
    try {
      const res = await api.promoteLiveFailures({
        agent_id: c.agent_id, trace_ids: [c.trace_id], rubric_id: rubric,
      });
      const added = res.added?.length ?? 0;
      onPromoted(added
        ? `Promoted live catch into ${res.regression_suite_id} (v${res.version}) — needs review before re-run.`
        : `Already promoted — no new live case added.`);
      load();
    } catch (e: any) {
      onPromoted(`⚠ ${String(e.message ?? e)}`);
    } finally { setBusy(null); }
  };

  return (
    <>
      <h3 style={{ marginTop: 22 }}>Promote live-monitor catches</h3>
      <p style={{ color: "var(--muted)", marginTop: -6, fontSize: 13 }}>
        Sampled production traces that scored below the <Term name="drift">drift threshold</Term>. A live
        trace has no scripted ground truth, so promoting one reconstructs the
        case from the trace's <i>input only</i> and lands it{" "}
        <b>needs-review</b> (no fabricated <code>expected</code>) in a dedicated
        live regression suite — approve it after verifying the input and rubric.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "8px 0 10px" }}>
        <label style={{ fontSize: 12, color: "var(--muted)" }}>rubric_id <small>(applied to promoted cases)</small></label>
        <input value={rubric} placeholder="rubric the reconstructed case runs on"
               onChange={(e) => setRubric(e.target.value)}
               style={{ maxWidth: 320 }} />
      </div>
      {catches === null ? <Skeleton rows={3} /> : catches.length === 0 ? (
        <EmptyState icon="📡" title="No live catches"
          hint={<>When live monitoring scores a sampled production trace below the{" "}
            <Term name="drift">drift threshold</Term>, it shows up here as promotable.</>} />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>trace</th><th>agent</th><th className="num">mean</th>
              <th>failing criteria</th><th>input</th><th>when</th><th></th></tr></thead>
            <tbody>
              {catches.map((c) => (
                <tr key={c.trace_id}>
                  <td className="mono" style={{ maxWidth: 160, overflow: "hidden",
                    textOverflow: "ellipsis" }}>{c.trace_id}</td>
                  <td className="mono">{c.agent_id}</td>
                  <td className="num" style={{ color: "var(--fail)" }}>{pct(c.mean_score)}</td>
                  <td style={{ fontSize: 12 }}>{(c.failing_criteria ?? []).join(", ") || "—"}</td>
                  <td style={{ fontSize: 12, color: c.input_reconstructed ? "inherit" : "var(--wait)" }}>
                    {c.input_reconstructed ? "reconstructed" : "partial"}</td>
                  <td style={{ fontSize: 12 }}>
                    {c.created_at ? new Date(c.created_at).toLocaleString() : "—"}</td>
                  <td>
                    {c.already_promoted ? (
                      <span style={{ fontSize: 12, color: "var(--muted)" }}>promoted ✓</span>
                    ) : (
                      <button disabled={busy === c.trace_id}
                              aria-label={`Promote live catch ${c.trace_id}`}
                              onClick={() => promote(c)}>
                        {busy === c.trace_id ? "Promoting…" : "Promote (needs review)"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function HardeningPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("suite");
  const promoteFrom = params.get("promote");  // deep-link: a scorecard to promote

  const [candidates, setCandidates] = useState<any[] | null>(null);
  const [suites, setSuites] = useState<any[] | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = () => {
    api.hardeningCandidates().then((r) => setCandidates(r.candidates)).catch(() => setCandidates([]));
    api.hardeningSuites().then((r) => setSuites(r.suites)).catch(() => setSuites([]));
  };
  useEffect(() => { refresh(); }, []);

  const promote = async (scorecardId: string) => {
    setBusy(scorecardId); setNote(null);
    try {
      const res = await api.promoteFailures({ scorecard_id: scorecardId });
      const added = res.added?.length ?? 0;
      setNote({ ok: true, text: added
        ? `Promoted ${added} case(s) into ${res.regression_suite_id} (v${res.version}).`
        : `No new cases to promote — already hardened (${res.skipped_duplicates?.length ?? 0} duplicate(s)).` });
      refresh();
    } catch (e: any) {
      setNote({ ok: false, text: String(e.message ?? e) });
    } finally { setBusy(null); }
  };

  // honor a ?promote=<scorecard_id> deep-link from the Compare/results pages
  useEffect(() => {
    if (promoteFrom) {
      promote(promoteFrom);
      params.delete("promote"); setParams(params, { replace: true });
    }
    // eslint-disable-next-line
  }, [promoteFrom]);

  if (selected) {
    return (
      <div className="page"><div className="list-page">
        <PageHeader title="Hardening" subtitle="Regression suite detail." />
        <SuiteDetail suiteId={selected}
          onBack={() => { params.delete("suite"); setParams(params); refresh(); }} />
      </div></div>
    );
  }

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader title="Hardening"
          subtitle="Fix, then keep it fixed. Promote an issue's failing cases into a permanent, versioned regression suite, then re-run to prove the fix held — with a McNemar delta so nothing silently regresses." />

        <div className="harden-flow" aria-hidden="true">
          <span className="hf-step"><span className="ic">①</span> Catch a failure</span>
          <span className="hf-arrow">→</span>
          <span className="hf-step"><span className="ic">②</span> Promote to a regression suite</span>
          <span className="hf-arrow">→</span>
          <span className="hf-step"><span className="ic">③</span> Re-run &amp; prove the fix held</span>
        </div>

        <div role="status" aria-live="polite">
          {note && (
            <div className={note.ok ? "note-ok" : "note-err"} style={{ marginBottom: 14 }}>
              {note.ok ? "✓ " : "⚠ "}{note.text}
            </div>
          )}
        </div>

        <h3>Regression suites</h3>
        {suites === null ? <Skeleton rows={3} /> : suites.length === 0 ? (
          <EmptyState icon="🛡" title="No regression suites yet"
            hint="Promote a scorecard's failing cases below to create one." />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>regression suite</th><th>agent</th><th>from</th>
                <th className="num">cases</th><th className="num">runs</th>
                <th>latest delta</th><th></th></tr></thead>
              <tbody>
                {suites.map((s) => (
                  <tr key={s.regression_suite_id}>
                    <td className="mono" style={{ maxWidth: 260, overflow: "hidden",
                      textOverflow: "ellipsis" }}>{s.regression_suite_id}</td>
                    <td className="mono">{s.agent_id}</td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {s.source === "live" ? (
                        <span title="reconstructed from live production catches — needs review"
                              style={{ color: "var(--wait)" }}>📡 live</span>
                      ) : s.source_suite_id}</td>
                    <td className="num">{s.n_cases}</td>
                    <td className="num">{s.runs}</td>
                    <td><DeltaChips d={s.latest_delta} /></td>
                    <td><button aria-label={`View regression suite ${s.regression_suite_id}`}
                      onClick={() => { params.set("suite", s.regression_suite_id);
                      setParams(params); }}>View</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h3 style={{ marginTop: 22 }}>Promote failures to a regression suite</h3>
        <p style={{ color: "var(--muted)", marginTop: -6, fontSize: 13 }}>
          Scorecards with at least one failing case. Errored cases are excluded
          (an errored case isn't a failure). Promoting de-dupes cases already
          captured.
        </p>
        {candidates === null ? <Skeleton rows={3} /> : candidates.length === 0 ? (
          <EmptyState icon="◌" title="No failing scorecards"
            hint="Run a suite that produces failures, then promote them here." />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>scorecard</th><th>agent</th><th>suite</th>
                <th className="num">success</th><th className="num">failing</th>
                <th className="num">errored</th><th>when</th><th></th></tr></thead>
              <tbody>
                {candidates.map((c) => (
                  <tr key={c.scorecard_id}>
                    <td className="mono">{c.scorecard_id}</td>
                    <td className="mono">{c.agent_id}</td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {c.suite_id} v{c.suite_version}</td>
                    <td className="num">{pct(c.task_success_rate)}</td>
                    <td className="num" style={{ color: "var(--fail)" }}>{c.n_failing}</td>
                    <td className="num" style={{ color: c.n_errored ? "var(--wait)" : "inherit" }}>
                      {c.n_errored}</td>
                    <td style={{ fontSize: 12 }}>{new Date(c.created_at).toLocaleString()}</td>
                    <td>
                      <button className="primary" disabled={busy === c.scorecard_id}
                              aria-label={`Promote failing cases from ${c.scorecard_id}`}
                              onClick={() => promote(c.scorecard_id)}>
                        {busy === c.scorecard_id ? "Promoting…" : "Promote failures"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <LiveCatches onPromoted={(text) => {
          setNote({ ok: !text.startsWith("⚠"), text: text.replace(/^⚠ /, "") });
          refresh();
        }} />
      </div>
    </div>
  );
}
