/* ============================================================================
   The Scanner — the signature moment of the consumer flow.

   One confident action: point us at an agent (an API endpoint, or the built-in
   demo) and watch it run the safety battery live, culminating in a stamped A–F
   grade on the Seal + a plain-language breakdown + a shareable certificate.

   A small state machine: idle → scanning → graded (or error). Everything else on
   the page stays quiet; the boldness lives here. End-user voice throughout.
   ========================================================================== */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, type ConnectionInput, type ConnectionStatus, type ConnectionTestResult,
  type ScanCheck, type ScanJob, type ScanPreview,
} from "../api";
import { badgeUrl, certUrl, gradeColor } from "../cert";
import { Seal } from "./Seal";

type Phase = "idle" | "scanning" | "graded" | "error";

/* The scan runs async server-side and is observed by polling GET /scan/{id}.
   We intentionally poll rather than stream: the backend exposes SSE only for
   graph executions (/api/executions/{id}/events, see sse.ts), NOT for scans —
   there is no /scan/{id}/events endpoint. Streaming the scan would require a
   backend change, so until that exists this stays an honest poll. */
const POLL_MS = 700;
const INTENT_KEY = "agenttic_scan_intent";

/** Friendly mapping of a failed scan-start to what the user should do next. */
function explainError(e: any): { kind: "auth" | "key" | "other"; msg: string } {
  const status = e?.status;
  const detail = String(e?.detail ?? e?.message ?? e ?? "");
  if (status === 401) {
    return { kind: "auth", msg: "Create a free account to run your scan — it takes about ten seconds." };
  }
  if (detail.toLowerCase().includes("anthropic api key")) {
    return { kind: "key", msg: "The demo agent runs on your own Anthropic key. Add your key, then try the demo again." };
  }
  return { kind: "other", msg: detail.replace(/^\d+\s*—?\s*/, "") || "Something went wrong. Please try again." };
}

function CheckRow({ c }: { c: ScanCheck }) {
  const icon =
    c.status === "pending" ? <span className="scan-check-spin" aria-hidden /> :
    c.passed ? <span className="scan-check-ic ok" aria-hidden>✓</span> :
    c.status === "warn" ? <span className="scan-check-ic wait" aria-hidden>!</span> :
    <span className="scan-check-ic fail" aria-hidden>✗</span>;
  const state = c.status === "pending" ? "checking…"
    : c.passed ? "Passed" : c.status === "warn" ? "Weak spot" : "Failed";
  return (
    <li className={`scan-check ${c.status}`}>
      {icon}
      <span className="scan-check-body">
        <span className="scan-check-label">
          {c.label}
          {c.critical && <span className="scan-check-crit" title="Critical safety dimension">core</span>}
        </span>
        <span className="scan-check-detail">{c.detail || state}</span>
      </span>
      <span className={`scan-check-state ${c.status}`}>
        {c.status === "pending" ? "" : c.percent != null ? `${c.percent}%` : state}
      </span>
    </li>
  );
}

/** Copy-to-clipboard field for the badge embed snippet. */
function CopyField({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="scan-embed-row">
      <label>{label}</label>
      <div className="embed-field">
        <code>{value}</code>
        <button type="button" className="ghost-sm" onClick={() => {
          navigator.clipboard?.writeText(value).then(() => {
            setDone(true); setTimeout(() => setDone(false), 1400);
          });
        }}>{done ? "Copied ✓" : "Copy"}</button>
      </div>
    </div>
  );
}

export function ScanExperience({ compact = false }: { compact?: boolean }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [url, setUrl] = useState("");
  const [showAuth, setShowAuth] = useState(false);
  const [headerName, setHeaderName] = useState("Authorization");
  const [headerValue, setHeaderValue] = useState("");
  const [agentName, setAgentName] = useState("");
  const [job, setJob] = useState<ScanJob | null>(null);
  const [err, setErr] = useState<ReturnType<typeof explainError> | null>(null);
  const [preview, setPreview] = useState<ScanPreview | null>(null);
  const timer = useRef<number | undefined>(undefined);

  // restore a scan intent saved before a sign-in bounce (so the URL survives)
  useEffect(() => {
    api.scanPreview().then(setPreview).catch(() => {});
    try {
      const saved = sessionStorage.getItem(INTENT_KEY);
      if (saved) { setUrl(JSON.parse(saved).url || ""); sessionStorage.removeItem(INTENT_KEY); }
    } catch { /* ignore */ }
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, []);

  const poll = (scanId: string) => {
    api.scanStatus(scanId).then((j) => {
      setJob(j);
      if (j.status === "running") {
        timer.current = window.setTimeout(() => poll(scanId), POLL_MS);
      } else if (j.status === "error") {
        setErr({ kind: "other", msg: j.error || "The scan failed. Please try again." });
        setPhase("error");
      } else {
        setPhase("graded");
      }
    }).catch((e) => { setErr(explainError(e)); setPhase("error"); });
  };

  const start = (target: "endpoint" | "demo" | "connection") => {
    setErr(null); setJob(null);
    if (target === "endpoint" && !url.trim()) {
      setErr({ kind: "other", msg: "Paste your agent's API endpoint URL first." });
      return;
    }
    setPhase("scanning");
    api.startScan({
      target,
      // a saved connection carries its own url/auth/mapping server-side
      ...(target === "connection" ? {} : {
        url: url.trim(), agent_name: agentName.trim(),
        ...(showAuth && headerValue.trim()
          ? { header_name: headerName.trim(), header_value: headerValue.trim() } : {}),
      }),
    }).then((r) => poll(r.scan_id))
      .catch((e) => {
        const ex = explainError(e);
        if (ex.kind === "auth") {
          try { sessionStorage.setItem(INTENT_KEY, JSON.stringify({ url: url.trim() })); } catch { /* ignore */ }
        }
        setErr(ex); setPhase("error");
      });
  };

  const reset = () => { setPhase("idle"); setJob(null); setErr(null); };

  // ---- render -------------------------------------------------------------
  const checks: ScanCheck[] = job?.checks
    ?? preview?.dimensions.map((d) => ({
      criterion_id: d.criterion_id, label: d.label, status: "pending" as const,
      passed: null, detail: "", critical: d.critical,
    })) ?? [];

  return (
    <div className={`scanner ${compact ? "compact" : ""} phase-${phase}`}>
      {phase === "idle" && (
        <form className="scan-form" onSubmit={(e) => { e.preventDefault(); start("endpoint"); }}>
          <label className="scan-input-label" htmlFor="scan-url">Your agent's API endpoint</label>
          <div className="scan-input-row">
            <input id="scan-url" type="url" inputMode="url" autoComplete="off"
                   placeholder="https://your-agent.com/chat" value={url}
                   onChange={(e) => setUrl(e.target.value)} />
            <button type="submit" className="btn-primary scan-go">Scan my agent</button>
          </div>

          <div className="scan-sub-actions">
            <button type="button" className="scan-link"
                    onClick={() => setShowAuth((s) => !s)} aria-expanded={showAuth}>
              {showAuth ? "− Hide auth header" : "+ Add an auth header"}
            </button>
            <span className="scan-or">or</span>
            <button type="button" className="btn-ghost scan-demo"
                    onClick={() => start("demo")}>Try it on a demo agent</button>
          </div>

          {showAuth && (
            <div className="scan-auth">
              <div className="scan-auth-field">
                <label htmlFor="scan-hn">Header name</label>
                <input id="scan-hn" value={headerName}
                       onChange={(e) => setHeaderName(e.target.value)} placeholder="Authorization" />
              </div>
              <div className="scan-auth-field grow">
                <label htmlFor="scan-hv">Header value</label>
                <input id="scan-hv" value={headerValue} type="password"
                       onChange={(e) => setHeaderValue(e.target.value)} placeholder="Bearer sk-…" />
              </div>
            </div>
          )}

          <p className="scan-reassure">
            We send a battery of safety probes to your endpoint and grade the answers.
            <b> No Anthropic key needed</b> — your agent runs on your own infrastructure.
          </p>

          <ConnectPanel onScan={() => start("connection")} />
        </form>
      )}

      {(phase === "scanning" || phase === "graded") && (
        <div className="scan-live">
          <div className="scan-seal-wrap">
            <div className={`scan-seal ${phase === "graded" ? "revealed" : "spinning"}`}>
              <Seal grade={phase === "graded" ? job?.result?.grade : undefined} size={compact ? 132 : 150} />
            </div>
            {phase === "scanning" && (
              <>
                <div className="scan-progress" role="progressbar"
                     aria-valuenow={Math.round((job?.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
                  <div style={{ width: `${Math.round((job?.progress ?? 0.05) * 100)}%` }} />
                </div>
                <div className="scan-phase">{job?.phase || "Starting the scan…"}</div>
              </>
            )}
            {phase === "graded" && job?.result && (
              <div className="scan-verdict">
                <div className="scan-verdict-grade" style={{ color: gradeColor(job.result.grade) }}>
                  Grade {job.result.grade}
                </div>
                <div className="scan-verdict-sub">
                  Safety score {job.result.composite_score}/100
                  {job.result.cost_usd > 0 && <> · cost ${job.result.cost_usd.toFixed(2)}</>}
                </div>
                <div className="scan-rigor"
                     title="A quick scan sends ~14 short safety probes and scores the replies with lexical refusal / target-token checks — a fast screen, not a full canonical suite run (k=3, against the real attack environments). Treat it as a first look, not an exhaustive audit.">
                  ⚡ Quick scan · ~14 probes · lexical screen
                </div>
              </div>
            )}
          </div>

          <ul className="scan-checks">
            {checks.map((c) => <CheckRow key={c.criterion_id} c={c} />)}
          </ul>

          {phase === "graded" && job?.result?.grade_capped && job.result.cap_reason && (
            <p className="scan-cap"><b>Why not higher?</b> {job.result.cap_reason}</p>
          )}

          {phase === "graded" && job && <GradedActions job={job} onReset={reset} />}
        </div>
      )}

      {phase === "error" && err && (
        <div className={`scan-error ${err.kind}`}>
          <div className="scan-error-ic">{err.kind === "auth" ? "🔑" : "⚠"}</div>
          <p>{err.msg}</p>
          <div className="scan-error-actions">
            {err.kind === "auth" && (
              <Link className="btn-primary" to="/signup?next=/scan">Create a free account</Link>
            )}
            {err.kind === "auth" && <Link className="btn-ghost" to="/login?next=/scan">Log in</Link>}
            {err.kind === "key" && <Link className="btn-primary" to="/app/settings">Add your key</Link>}
            <button type="button" className="btn-ghost" onClick={reset}>Back</button>
          </div>
        </div>
      )}
    </div>
  );
}

type Preset = "openai" | "generic" | "custom";

/* ---------------------------------------------------------------------------
   Connect your agent — the reusable, safe webhook connection. A step-by-step
   panel: endpoint → optional auth → preset / mapping → Test connection → authorize
   → scan. The connection is saved so repeat scans don't re-enter it. End-user
   voice; every guard (SSRF, consent, gentle traffic) is enforced server-side.
   ------------------------------------------------------------------------- */
function ConnectPanel({ onScan }: { onScan: () => void }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<ConnectionStatus | null>(null);
  const [url, setUrl] = useState("");
  const [agentName, setAgentName] = useState("");
  const [preset, setPreset] = useState<Preset>("openai");
  const [requestField, setRequestField] = useState("input");
  const [responsePath, setResponsePath] = useState("");
  const [model, setModel] = useState("");
  const [authName, setAuthName] = useState("Authorization");
  const [authValue, setAuthValue] = useState("");
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<ConnectionTestResult | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");

  // load any previously-saved connection so repeat scans skip re-entry
  useEffect(() => {
    if (!open) return;
    api.getConnection().then((s) => {
      if (!s.connected) return;
      setSaved(s);
      setUrl(s.endpoint_url || "");
      setAgentName(s.agent_name || "");
      setPreset((s.preset as Preset) || "openai");
      setRequestField(s.request_field || "input");
      setResponsePath(s.response_path || "");
      setModel(s.model || "");
      setAuthName(s.auth_header_name || "Authorization");
      setConsent(!!s.consent);
    }).catch(() => {});
  }, [open]);

  const body = (): ConnectionInput => ({
    endpoint_url: url.trim(), agent_name: agentName.trim(), preset,
    request_field: requestField.trim(), response_path: responsePath.trim(),
    model: model.trim(), auth_header_name: authName.trim(),
    auth_header_value: authValue.trim(),
  });

  const runTest = () => {
    setError(""); setTest(null);
    if (!url.trim()) { setError("Paste your agent's endpoint URL first."); return; }
    setTesting(true);
    api.testConnection(body())
      .then(setTest)
      .catch((e) => setError(String(e?.detail ?? e?.message ?? "Test failed.")))
      .finally(() => setTesting(false));
  };

  const saveAndScan = () => {
    setError("");
    if (!url.trim()) { setError("Paste your agent's endpoint URL first."); return; }
    if (!consent) { setError("Please confirm you're authorized to test this agent."); return; }
    setBusy(true);
    api.saveConnection({ ...body(), consent: true })
      .then(() => onScan())
      .catch((e) => { setError(String(e?.detail ?? e?.message ?? "Couldn't save.")); setBusy(false); });
  };

  if (!open) {
    return (
      <button type="button" className="scan-link connect-open" onClick={() => setOpen(true)}>
        + Connect a reusable agent (presets, mapping, test connection)
      </button>
    );
  }

  return (
    <div className="connect-panel">
      <div className="connect-head">
        <h4>Connect your agent</h4>
        <button type="button" className="scan-link" onClick={() => setOpen(false)}>− Close</button>
      </div>
      {saved?.connected && (
        <p className="connect-saved">Saved connection: <b>{saved.agent_name}</b>
          {saved.auth_set && <> · auth {saved.auth_masked}</>} · you can re-test or scan again.</p>
      )}

      {/* 1 · endpoint */}
      <label className="scan-input-label" htmlFor="conn-url">1 · Your agent's endpoint URL</label>
      <input id="conn-url" type="url" className="connect-input" placeholder="https://your-agent.com/v1/chat"
             value={url} onChange={(e) => setUrl(e.target.value)} />

      {/* 2 · preset / mapping */}
      <label className="scan-input-label" htmlFor="conn-preset">2 · How to talk to it</label>
      <select id="conn-preset" className="connect-input" value={preset}
              onChange={(e) => { setPreset(e.target.value as Preset); setTest(null); }}>
        <option value="openai">OpenAI-compatible (one click)</option>
        <option value="generic">Generic webhook ({"{input}"} → {"{output}"})</option>
        <option value="custom">Custom mapping</option>
      </select>
      {preset === "openai" && (
        <div className="scan-auth">
          <div className="scan-auth-field grow">
            <label htmlFor="conn-model">Model</label>
            <input id="conn-model" value={model} placeholder="gpt-4o-mini / claude-…"
                   onChange={(e) => setModel(e.target.value)} />
          </div>
        </div>
      )}
      {(preset === "generic" || preset === "custom") && (
        <div className="scan-auth">
          <div className="scan-auth-field grow">
            <label htmlFor="conn-rf">Request field (prompt goes here)</label>
            <input id="conn-rf" value={requestField} placeholder="input"
                   onChange={(e) => setRequestField(e.target.value)} />
          </div>
          <div className="scan-auth-field grow">
            <label htmlFor="conn-rp">Reply path in the response</label>
            <input id="conn-rp" value={responsePath} placeholder="output"
                   onChange={(e) => setResponsePath(e.target.value)} />
          </div>
        </div>
      )}

      {/* 3 · optional auth header (secret — stored encrypted) */}
      <label className="scan-input-label">3 · Auth header (optional — stored encrypted)</label>
      <div className="scan-auth">
        <div className="scan-auth-field">
          <label htmlFor="conn-an">Header name</label>
          <input id="conn-an" value={authName} placeholder="Authorization"
                 onChange={(e) => setAuthName(e.target.value)} />
        </div>
        <div className="scan-auth-field grow">
          <label htmlFor="conn-av">Header value</label>
          <input id="conn-av" type="password" value={authValue}
                 placeholder={saved?.auth_set ? "•••• (saved — leave blank to keep)" : "Bearer sk-…"}
                 onChange={(e) => setAuthValue(e.target.value)} />
        </div>
      </div>

      {/* 4 · test connection */}
      <div className="connect-actions">
        <button type="button" className="btn-ghost" onClick={runTest} disabled={testing}>
          {testing ? "Testing…" : "Test connection"}
        </button>
      </div>
      {test && test.ok && (
        <div className="connect-test ok">
          <b>✓ Connected.</b> Your agent replied:
          <blockquote>{test.reply.slice(0, 240) || "(empty reply)"}</blockquote>
        </div>
      )}
      {test && !test.ok && (
        <div className="connect-test err"><b>Couldn't connect.</b> {test.error}</div>
      )}

      {/* 5 · authorize */}
      <label className="connect-consent">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>I own this agent, or I'm authorized to run a safety test against it.</span>
      </label>

      {/* 6 · scan */}
      {error && <p className="connect-error">{error}</p>}
      <div className="connect-actions">
        <button type="button" className="btn-primary" onClick={saveAndScan}
                disabled={busy || !consent}>
          {busy ? "Saving…" : "Save & scan my agent"}
        </button>
      </div>
      <p className="connect-scope">
        This sends ~14 short safety prompts to your agent, one at a time, each tagged
        <code> X-Agenttic-Safety-Test: true</code>. We only send text and read replies —
        we never run your tools.
      </p>
    </div>
  );
}

function GradedActions({ job, onReset }: { job: ScanJob; onReset: () => void }) {
  const crt = job.certificate;
  const grade = job.result?.grade ?? "F";
  const name = crt?.agent_name || job.agent_name || "your agent";
  if (!crt) {
    return (
      <div className="scan-actions">
        {job.cert_note && <p className="scan-cert-note">{job.cert_note}</p>}
        <button type="button" className="btn-ghost" onClick={onReset}>Scan another agent</button>
      </div>
    );
  }
  const page = certUrl(crt.cert_id);
  const badge = badgeUrl(crt.cert_id);
  const md = `[![Tested with Agenttic — ${name}](${badge})](${page})`;
  return (
    <div className="scan-actions">
      <div className="scan-cert-head">
        <span className="seal-mark"><span className="sm-hex" aria-hidden>⬡</span> Tested with Agenttic</span>
        <span className="scan-cert-grade" style={{ color: gradeColor(grade) }}>Grade {grade}</span>
      </div>
      <p className="scan-cert-blurb">
        We minted a signed, verifiable certificate for this result — pinned to the
        exact agent version we tested. Share the badge or the public page.
      </p>
      <p className="scan-cert-caveat">
        This grade is from a <b>quick scan</b> (~14 lexical safety probes), not a full
        canonical suite run — a fast screen, not an exhaustive audit.
      </p>
      <div className="scan-embed-preview">
        <img src={badge} alt={`Agenttic Safety grade ${grade}`} height={28} />
        <a href={page} target="_blank" rel="noreferrer" className="embed-link">View certificate ↗</a>
      </div>
      <CopyField label="Badge for your README" value={md} />
      <div className="scan-actions-row">
        <Link className="btn-primary" to={`/certified/${crt.cert_id}`}>Open certificate</Link>
        <button type="button" className="btn-ghost" onClick={onReset}>Scan another agent</button>
      </div>
    </div>
  );
}
