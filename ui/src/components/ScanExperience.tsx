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
  api, type ScanCheck, type ScanJob, type ScanPreview,
} from "../api";
import { badgeUrl, certUrl, gradeColor } from "../cert";
import { Seal } from "./Seal";

type Phase = "idle" | "scanning" | "graded" | "error";

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

  const start = (target: "endpoint" | "demo") => {
    setErr(null); setJob(null);
    if (target === "endpoint" && !url.trim()) {
      setErr({ kind: "other", msg: "Paste your agent's API endpoint URL first." });
      return;
    }
    setPhase("scanning");
    api.startScan({
      target, url: url.trim(), agent_name: agentName.trim(),
      ...(showAuth && headerValue.trim()
        ? { header_name: headerName.trim(), header_value: headerValue.trim() } : {}),
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
