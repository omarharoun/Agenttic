import { useEffect, useState } from "react";
import { api, downloadBlob } from "../api";
import { EmptyState, PageHeader, Skeleton, Spinner } from "../components/ui";
import { Term } from "../components/Term";

const TERMINAL = new Set(["succeeded", "failed"]);

function StatusChip({ run }: { run: any }) {
  const s = run.status;
  if (s === "running") {
    const p = run.total_episodes
      ? ` ${run.episodes_completed}/${run.total_episodes}` : "";
    return <span className="status-chip running">running{p}</span>;
  }
  const cls = s === "succeeded" ? "succeeded" : "failed";
  return <span className={`status-chip ${cls}`}>{s}</span>;
}

/* ============================================================================
   Training Camp — /app/training-camp.

   The folded-in AgentCamp training/eval layer. Run an agent against a task N
   times, grade every attempt deterministically, and read back:
     • accuracy with a Wilson 95% LOWER BOUND (a lucky 99/100 doesn't sneak past
       a 99% floor),
     • a two-condition PROMOTION GATE — a hard, non-overridable accuracy floor
       AND a required human sign-off,
     • the graded-episode MEMORY (and a distillation-dataset export),
     • the self-improving loop with a frozen-holdout ratchet + collapse guard.

   Honesty posture is surfaced, not hidden: the floor can't be waved through, and
   the anti-collapse "degenerate" run is a first-class thing you can watch refuse
   to promote.
   ========================================================================== */

function pct(x: number | null | undefined): string {
  return x == null ? "—" : `${(x * 100).toFixed(1)}%`;
}

function Yes({ ok, yes = "✓ yes", no = "✗ no" }: {
  ok: boolean; yes?: string; no?: string;
}) {
  return <b style={{ color: ok ? "var(--ok)" : "var(--fail)" }}>{ok ? yes : no}</b>;
}

function Stat({ label, value, tone }: {
  label: string; value: React.ReactNode; tone?: string;
}) {
  return (
    <div style={{ minWidth: 130 }}>
      <div className="muted-sm">{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: tone }}>{value}</div>
    </div>
  );
}

function GateBadge({ gate }: { gate: any }) {
  const promoted = gate?.promoted;
  const cls = promoted ? "succeeded" : gate?.floor_met ? "waiting_approval" : "failed";
  const label = promoted ? "PROMOTED"
    : gate?.floor_met ? "floor met · awaiting sign-off" : "BLOCKED";
  return <span className={`status-chip ${cls}`}>{label}</span>;
}

export function TrainingCampPage() {
  const [runs, setRuns] = useState<any[] | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [tasks, setTasks] = useState<{ task_id: string; name: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // start-camp form
  const [kind, setKind] = useState<"single" | "improve">("single");
  const [taskId, setTaskId] = useState("support_triage");
  const [mode, setMode] = useState("mock");
  const [episodes, setEpisodes] = useState(500);
  const [threshold, setThreshold] = useState(0.99);
  const [seed, setSeed] = useState(0);
  // improve-only
  const [rounds, setRounds] = useState(5);
  const [epPerRound, setEpPerRound] = useState(300);
  const [holdout, setHoldout] = useState(600);
  const [degenerate, setDegenerate] = useState(false);

  const load = () =>
    api.listCamps().then((r) => setRuns(r.runs)).catch(() => setRuns([]));
  useEffect(() => {
    load();
    api.campTasks().then((r) => setTasks(r.tasks)).catch(() => setTasks([]));
  }, []);

  const inspect = (id: string) => {
    setDetail(null);
    api.getCamp(id).then(setDetail).catch(() => {});
  };

  // Poll a run that's still working (async): refresh detail + list until it
  // reaches a terminal state, then render results. No long-held request, so no
  // Cloudflare 524.
  useEffect(() => {
    if (!detail || TERMINAL.has(detail.status)) return;
    const id = detail.run_id;
    const t = setInterval(() => {
      api.getCamp(id).then((r) => {
        setDetail(r);
        if (TERMINAL.has(r.status)) load();
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [detail?.run_id, detail?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the list fresh while any run is still working.
  const anyRunning = (runs ?? []).some((r) => !TERMINAL.has(r.status));
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [anyRunning]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setBusy(true); setMsg(null);
    try {
      const run = kind === "improve"
        ? await api.startImprove({
            task_id: taskId, rounds, episodes_per_round: epPerRound,
            threshold, holdout, seed, degenerate })
        : await api.startCamp({
            task_id: taskId, mode, episodes, threshold, seed });
      // 202 + a running row; the polling effect takes over and shows progress.
      setMsg({ kind: "ok", text: `Camp ${run.run_id} started — running…` });
      load();
      setDetail(run);
    } catch (e: any) {
      setMsg({ kind: "err", text: `Could not start camp: ${String(e?.message ?? e)}` });
    } finally { setBusy(false); }
  };

  const approve = async (id: string) => {
    try {
      const updated = await api.approveCamp(id);
      setDetail(updated);
      load();
      setMsg({
        kind: updated.gate?.promoted ? "ok" : "err",
        text: updated.gate?.promoted
          ? "Signed off — agent PROMOTED (floor cleared + human approval)."
          : "Sign-off recorded, but the hard accuracy floor is not met — "
            + "promotion is blocked. The floor is non-overridable.",
      });
    } catch (e: any) {
      setMsg({ kind: "err", text: `Approve failed: ${String(e?.message ?? e)}` });
    }
  };

  const exportDistill = async (id: string) => {
    try {
      const blob = await api.exportCampDistillation(id);
      downloadBlob(blob, `camp-${id}-distillation.jsonl`);
    } catch (e: any) {
      setMsg({ kind: "err", text: `Export failed: ${String(e?.message ?? e)}` });
    }
  };

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Training Camp"
          subtitle={<>Run an agent against a task many times, grade every attempt,
            and measure real accuracy with a <b><Term name="wilson">Wilson 95% lower bound</Term></b>. Promotion
            needs <b>both</b> a hard, non-overridable accuracy floor <i>and</i> a human
            sign-off. Export the passing episodes as a distillation dataset, or run the
            self-improving loop with its <Term name="ratchet">anti-collapse ratchet</Term>.</>}
        />

        {/* start panel */}
        <div className="card" style={{ marginBottom: 22 }}>
          <div className="card-head"><h2>Start a camp</h2>
            <p>Mock mode uses a deterministic baseline (fast, reproducible, no key).
              Agent mode runs your BYO-Anthropic-key agent as the thing under camp.</p>
          </div>
          <div className="card-body">
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
              <label>Kind<br />
                <select value={kind} onChange={(e) => setKind(e.target.value as any)}>
                  <option value="single">Single camp</option>
                  <option value="improve">Self-improving loop</option>
                </select>
              </label>
              <label>Task<br />
                <select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
                  {tasks.map((t) => (
                    <option key={t.task_id} value={t.task_id}>{t.name}</option>
                  ))}
                </select>
              </label>
              {kind === "single" && (
                <>
                  <label>Mode<br />
                    <select value={mode} onChange={(e) => setMode(e.target.value)}>
                      <option value="mock">mock (baseline)</option>
                      <option value="agent">agent (your key)</option>
                    </select>
                  </label>
                  <label>Episodes<br />
                    <input type="number" value={episodes} min={1}
                           onChange={(e) => setEpisodes(+e.target.value)}
                           style={{ width: 90 }} />
                  </label>
                </>
              )}
              {kind === "improve" && (
                <>
                  <label>Rounds<br />
                    <input type="number" value={rounds} min={1}
                           onChange={(e) => setRounds(+e.target.value)}
                           style={{ width: 70 }} />
                  </label>
                  <label>Episodes / round<br />
                    <input type="number" value={epPerRound} min={1}
                           onChange={(e) => setEpPerRound(+e.target.value)}
                           style={{ width: 90 }} />
                  </label>
                  <label>Holdout<br />
                    <input type="number" value={holdout} min={1}
                           onChange={(e) => setHoldout(+e.target.value)}
                           style={{ width: 80 }} />
                  </label>
                  <label title="Learn from the agent's OWN unverified outputs — demonstrates collapse; the ratchet refuses it.">
                    <input type="checkbox" checked={degenerate}
                           onChange={(e) => setDegenerate(e.target.checked)} />
                    {" "}degenerate (collapse demo)
                  </label>
                </>
              )}
              <label>Floor (threshold)<br />
                <input type="number" value={threshold} step={0.01} min={0} max={1}
                       onChange={(e) => setThreshold(+e.target.value)}
                       style={{ width: 80 }} />
              </label>
              <label>Seed<br />
                <input type="number" value={seed}
                       onChange={(e) => setSeed(+e.target.value)}
                       style={{ width: 70 }} />
              </label>
              <button className="primary" disabled={busy} onClick={start}>
                {busy ? "Running…" : "🎯 Run camp"}
              </button>
            </div>
            {msg && <div className={msg.kind === "ok" ? "note-ok" : "note-err"}
                         style={{ marginTop: 12 }}>{msg.text}</div>}
          </div>
        </div>

        {/* runs list */}
        {runs === null ? <Skeleton rows={4} /> : runs.length === 0 ? (
          <EmptyState icon="🎯" title="No camp runs yet"
            hint="Start a camp above — the baseline scores ~85% on support triage, so you can watch a 99% floor correctly block it." />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>run</th><th>kind</th><th>status</th><th>mode</th>
                  <th>accuracy</th><th>wilson₉₅ low</th><th>floor</th>
                  <th>gate</th><th></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const done = r.status === "succeeded";
                  return (
                    <tr key={r.run_id}>
                      <td className="mono">{r.run_id}</td>
                      <td>{r.kind}</td>
                      <td><StatusChip run={r} /></td>
                      <td>{r.mode}</td>
                      <td>{done ? pct(r.pass_rate) : "—"}</td>
                      <td><b>{done ? pct(r.wilson_lower_95) : "—"}</b></td>
                      <td>{pct(r.threshold)}</td>
                      <td>{done ? <GateBadge gate={r.gate} /> : "—"}</td>
                      <td><button className="ghost-sm" onClick={() => inspect(r.run_id)}>inspect</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {detail && <CampDetail run={detail} onApprove={approve} onExport={exportDistill} />}
      </div>
    </div>
  );
}

function RunningView({ run }: { run: any }) {
  const total = run.total_episodes || 0;
  const done = run.episodes_completed || 0;
  const frac = total ? Math.min(1, done / total) : 0;
  return (
    <div style={{ marginTop: 26 }}>
      <h2 style={{ marginBottom: 4 }}>
        <span className="mono">{run.run_id}</span>{" "}
        <span className="muted-sm">· {run.kind} · {run.task_id} · {run.mode}</span>
      </h2>
      <div className="card">
        <div className="card-head"><h3><Spinner /> Running…</h3>
          <p>The camp runs in the background — this page polls for progress, so a
            long run won't time out. {run.mode === "agent"
              && "Agent mode makes one model call per episode."}</p>
        </div>
        <div className="card-body">
          <div style={{ marginBottom: 8 }}>
            <b>{run.phase || "working"}</b>
            {total ? ` — ${done}/${total} episodes` : ""}
          </div>
          <div style={{ height: 10, borderRadius: 6, background: "var(--wait-soft)",
                        overflow: "hidden", maxWidth: 460 }}>
            <div style={{ width: `${Math.round(frac * 100)}%`, height: "100%",
                          background: "var(--accent)", transition: "width .4s" }} />
          </div>
        </div>
      </div>
    </div>
  );
}

function FailedView({ run }: { run: any }) {
  return (
    <div style={{ marginTop: 26 }}>
      <h2 style={{ marginBottom: 4 }}>
        <span className="mono">{run.run_id}</span>{" "}
        <span className="muted-sm">· {run.kind} · {run.task_id} · {run.mode}</span>
      </h2>
      <div className="card">
        <div className="card-head"><h3 style={{ color: "var(--fail)" }}>Run failed</h3></div>
        <div className="card-body">
          <div className="note-err">{run.error || "The run failed with no message."}</div>
        </div>
      </div>
    </div>
  );
}

function CampDetail({ run, onApprove, onExport }: {
  run: any; onApprove: (id: string) => void; onExport: (id: string) => void;
}) {
  if (run.status === "running" || run.status === "queued") return <RunningView run={run} />;
  if (run.status === "failed") return <FailedView run={run} />;
  const g = run.gate ?? {};
  const rep = run.report ?? {};
  const floorPromoteHint = !g.floor_met
    ? "The hard accuracy floor (Wilson lower bound) is not met. A human sign-off "
      + "cannot override it — this is by design."
    : g.human_approved ? "Floor met and a human has signed off."
      : "Floor met — a human operator sign-off is the one remaining condition.";

  return (
    <div style={{ marginTop: 26 }}>
      <h2 style={{ marginBottom: 4 }}>
        <span className="mono">{run.run_id}</span>{" "}
        <span className="muted-sm">· {run.kind} · {run.task_id} · {run.mode}</span>
      </h2>

      {/* accuracy + Wilson */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h3>Accuracy</h3>
          <p>The floor is judged against the <b>lower bound</b>, not the point estimate.</p>
        </div>
        <div className="card-body" style={{ display: "flex", gap: 28, flexWrap: "wrap" }}>
          <Stat label="Pass rate" value={pct(run.pass_rate)} />
          <Stat label="Wilson 95% lower bound" value={pct(run.wilson_lower_95)}
                tone="var(--accent)" />
          <Stat label="Floor (threshold)" value={pct(run.threshold)} />
          <Stat label="Episodes" value={`${run.passes}/${run.episodes}`} />
          <Stat label="Enough data" value={<Yes ok={!!g.enough_data} />} />
        </div>
      </div>

      {/* promotion gate */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h3>Promotion gate</h3>
          <p>Two required conditions. The floor is non-overridable.</p>
        </div>
        <div className="card-body">
          <div style={{ display: "flex", gap: 40, flexWrap: "wrap", marginBottom: 12 }}>
            <Stat label="① Accuracy floor met" value={<Yes ok={!!g.floor_met} />} />
            <Stat label="② Human sign-off"
                  value={<Yes ok={!!g.human_approved} yes="✓ approved" no="— pending" />} />
            <Stat label="Decision" value={<GateBadge gate={g} />} />
          </div>
          {run.approved_by && (
            <p className="muted-sm">Signed off by <b>{run.approved_by}</b>
              {run.approved_at ? ` · ${new Date(run.approved_at).toLocaleString()}` : ""}.</p>
          )}
          {Array.isArray(g.reasons) && g.reasons.length > 0 && (
            <ul className="muted-sm" style={{ margin: "6px 0 12px 18px" }}>
              {g.reasons.map((why: string, i: number) => <li key={i}>{why}</li>)}
            </ul>
          )}
          <p className="muted-sm" style={{ marginBottom: 12 }}>{floorPromoteHint}</p>
          <button className="primary" onClick={() => onApprove(run.run_id)}
                  disabled={g.promoted}>
            {g.promoted ? "Promoted ✓" : "🖊 Sign off as operator"}
          </button>
          <button className="ghost-sm" style={{ marginLeft: 10 }}
                  onClick={() => onExport(run.run_id)}>
            📦 Export distillation ({run.distillation_count ?? 0})
          </button>
        </div>
      </div>

      {/* improve loop: ratchet + review queue */}
      {run.kind === "improve" && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head"><h3>Self-improving loop</h3>
            <p>Challengers only replace the champion if they beat it on the frozen
              held-out anchor. {rep.degenerate && <b>Degenerate run: no ground-truth anchor.</b>}</p>
          </div>
          <div className="card-body">
            <div style={{ display: "flex", gap: 28, flexWrap: "wrap", marginBottom: 12 }}>
              <Stat label="Final champion gen" value={rep.final_champion_gen ?? "—"} />
              <Stat label="Holdout rate" value={pct(rep.final_holdout_rate)} />
              <Stat label="Holdout wilson₉₅" value={pct(rep.final_holdout_wilson)} />
            </div>
            <p className="muted-sm" style={{ marginBottom: 12 }}>
              <b>Halted:</b> {rep.halted_reason ?? "—"}
            </p>
            {Array.isArray(run.rounds) && run.rounds.length > 0 && (
              <div className="table-wrap" style={{ marginBottom: 14 }}>
                <table className="data">
                  <thead><tr>
                    <th>round</th><th>champ</th><th>challenger</th>
                    <th>ratchet</th><th>note</th>
                  </tr></thead>
                  <tbody>
                    {run.rounds.map((rd: any) => (
                      <tr key={rd.round}>
                        <td>{rd.round}</td>
                        <td>gen{rd.champion_gen} · {pct(rd.champion_rate)}</td>
                        <td>gen{rd.challenger_gen} · {pct(rd.challenger_rate)}</td>
                        <td><b style={{ color: rd.accepted ? "var(--ok)" : "var(--muted)" }}>
                          {rd.accepted ? "ACCEPT" : "refuse"}</b></td>
                        <td className="muted-sm">{rd.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {Array.isArray(run.review_queue) && run.review_queue.length > 0 && (
              <>
                <h4 style={{ margin: "6px 0" }}>Human curriculum — champion's remaining failures ({run.review_queue.length})</h4>
                <div className="table-wrap">
                  <table className="data">
                    <thead><tr><th>message</th><th>agent action</th><th>correct</th></tr></thead>
                    <tbody>
                      {run.review_queue.slice(0, 20).map((q: any, i: number) => (
                        <tr key={i}>
                          <td>{q.message}</td>
                          <td className="mono">{q.agent_action?.action ?? "—"}</td>
                          <td className="mono">{q.correct?.action ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* memory / traces */}
      {Array.isArray(run.episode_sample) && run.episode_sample.length > 0 && (
        <div className="card">
          <div className="card-head"><h3>Memory — graded episodes</h3>
            <p>{run.episode_count} episodes recorded; {run.distillation_count} passing
              ones feed the distillation export. Showing a sample.</p>
          </div>
          <div className="card-body">
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th></th><th>input</th><th>action</th><th>score</th></tr></thead>
                <tbody>
                  {run.episode_sample.map((ep: any) => (
                    <tr key={ep.episode_id}>
                      <td>{ep.passed
                        ? <span style={{ color: "var(--ok)" }}>✓</span>
                        : <span style={{ color: "var(--fail)" }}>✗</span>}</td>
                      <td>{ep.inputs?.message ?? JSON.stringify(ep.inputs)}</td>
                      <td className="mono">{ep.action?.action ?? JSON.stringify(ep.action)}</td>
                      <td>{ep.score?.toFixed?.(2) ?? ep.score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
