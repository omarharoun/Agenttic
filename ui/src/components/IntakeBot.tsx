/* ============================================================================
   IntakeBot — the real, streaming intake assistant on the public /scan page.

   This replaces the old scripted CertConversation. It is the SAME agent loop as
   the in-app Copilot, but driven through the UNAUTHENTICATED public bot API
   (/api/public/copilot/*): a plain-fetch SSE stream, the server's own key, the
   public-demo tenant, and a strict 4-tool allowlist (preview_scan,
   start_demo_scan, get_scan_status, get_scan_findings). Nothing tenant-,
   platform-, or certification-scoped is reachable here.

   It opens with the bot's greeting, takes free-form questions, streams the
   answer token-by-token, shows live tool activity, and renders the inline
   approval card when the bot proposes the free demo scan. Once a scan is
   running, it surfaces the SAME live checklist + per-probe findings the quick
   form uses (the ScanReport component, the ScanExperience check rows), polling
   the public demo-scan routes by the scan_id the bot's tool activity reports.

   Honesty by construction: if the public bot is unavailable (status), we do NOT
   fake a conversation — we say the assistant is offline and fall back to the
   quick form. The demo runs on the SERVER's key; the visitor needs no account
   and no key.
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, publicCopilotApprove, publicCopilotChat, publicCopilotStatus,
  type CopilotErrorInfo, type CopilotHandlers, type CopilotToolEvent,
  type ScanCheck, type ScanJob, type ScanPreview,
} from "../api";
import { gradeColor } from "../cert";
import { SCORE_MEANING } from "../workflow/templates";
import { ChatThread, type ChatMsg } from "../copilot/chatThread";
import { ScanReport } from "./ScanReport";
import { Seal } from "./Seal";
import { IconCheck, IconClose, IconWarning, IconArrowUp } from "../icons";

const GREETING =
  "Is your AI agent safe to ship? I'm the Agenttic safety assistant — I'll ask a " +
  "couple of quick questions about your agent so I can tell you what actually " +
  "matters for it, then we can run a scan. First: what does your agent do?";

const SUGGESTIONS = [
  "It handles customer support",
  "It writes code",
  "It does research & browsing",
  "It runs internal ops",
];

const POLL_MS = 700;

let _seq = 0;
const uid = (p: string) => `${p}_${Date.now().toString(36)}_${_seq++}`;

/** Pull a scan_id out of a tool event. The public bot's start_demo_scan /
 *  get_scan_status tools report it in their human summary as
 *  "…: scan_id=scan_…"; that's how the UI learns which demo job to follow. */
function scanIdFrom(ev: CopilotToolEvent): string | null {
  if (ev.tool !== "start_demo_scan" && ev.tool !== "get_scan_status" &&
      ev.tool !== "get_scan_findings") return null;
  const m = /scan_id=([A-Za-z0-9_-]+)/.exec(ev.summary || "");
  return m ? m[1] : null;
}

/* ---- the live checklist panel (mirrors ScanExperience's readout) ---------- */

function CheckRow({ c }: { c: ScanCheck }) {
  const icon =
    c.status === "pending" ? <span className="scan-check-spin" aria-hidden /> :
    c.passed ? <span className="scan-check-ic ok" aria-hidden><IconCheck size={14}/></span> :
    c.status === "warn" ? <span className="scan-check-ic wait" aria-hidden>!</span> :
    <span className="scan-check-ic fail" aria-hidden><IconClose size={14}/></span>;
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

/** The live scan readout beside the chat: the Seal (spinning → graded), the
 *  per-dimension checklist, and the findings expander. Rendered only once the
 *  bot has actually started a demo scan (scanId != null). */
function ScanPanel({ scanId, job, dims }: {
  scanId: string; job: ScanJob | null; dims: ScanPreview["dimensions"];
}) {
  const done = job?.status === "done";
  const running = !job || job.status === "running";
  const checks: ScanCheck[] = job?.checks ?? dims.map((d) => ({
    criterion_id: d.criterion_id, label: d.label, status: "pending" as const,
    passed: null, detail: "", critical: d.critical,
  }));
  return (
    <aside className="intake-scan" aria-label="Live safety scan">
      <div className="intake-scan-top">
        <span>SAFETY SCAN</span>
        <span className="intake-scan-mode">{done ? "GRADED" : "SCANNING"}</span>
      </div>
      <div className={`scan-seal ${done ? "revealed" : "spinning"}`}>
        <Seal grade={done ? job?.result?.grade : undefined} size={116} />
      </div>
      {running && (
        <div className="scan-progress" role="progressbar"
             aria-valuenow={Math.round((job?.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
          <div style={{ width: `${Math.round((job?.progress ?? 0.05) * 100)}%` }} />
        </div>
      )}
      {running && <div className="scan-phase">{job?.phase || "Starting the scan…"}</div>}
      {done && job?.result && (
        <div className="scan-verdict">
          <div className="scan-verdict-grade" style={{ color: gradeColor(job.result.grade) }}>
            Grade {job.result.grade}
          </div>
          <div className="scan-verdict-sub" title={SCORE_MEANING}>
            Composite safety score {job.result.composite_score}/100
          </div>
        </div>
      )}
      <ul className="scan-checks">
        {checks.map((c) => <CheckRow key={c.criterion_id} c={c} />)}
      </ul>
      {done && job?.result?.grade_capped && job.result.cap_reason && (
        <p className="scan-cap"><b>Why not higher?</b> {job.result.cap_reason}</p>
      )}
      {done && <ScanReport scanId={scanId} isDemo />}
      {done && !job?.certificate && (
        <p className="intake-scan-note">
          The demo run mints no certificate. Scan your own agent to get a signed,
          shareable one.
        </p>
      )}
    </aside>
  );
}

/* ---- the bot ------------------------------------------------------------- */

/** The streaming intake bot, plus its offline fallback. `onFallback` lets the
 *  page switch to the quick form (used both on the offline banner and the
 *  always-present "just paste a URL" escape hatch). */
export function IntakeBot({ onFallback }: { onFallback: () => void }) {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([
    { id: uid("a"), role: "assistant", text: GREETING },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [scanId, setScanId] = useState<string | null>(null);
  const [job, setJob] = useState<ScanJob | null>(null);
  const [dims, setDims] = useState<ScanPreview["dimensions"]>([]);

  const sessionId = useRef<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const threadEnd = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const pollTimer = useRef<number | undefined>(undefined);
  const scanIdRef = useRef<string | null>(null);

  useEffect(() => {
    let alive = true;
    publicCopilotStatus()
      .then((s) => { if (alive) setAvailable(s.available); })
      .catch(() => { if (alive) setAvailable(false); });
    // dimension labels for the live checklist before the first status lands
    api.publicDemoPreview()
      .then((d) => { if (alive && d.dimensions?.length) setDims(d.dimensions); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => { threadEnd.current?.scrollIntoView({ block: "end" }); }, [messages, job]);
  useEffect(() => () => {
    abortRef.current?.abort();
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
  }, []);

  const patch = useCallback((id: string, fn: (m: ChatMsg) => Partial<ChatMsg>) => {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)));
  }, []);

  /** Poll the public demo-scan routes so the checklist fills in live — the SAME
   *  routes the quick form uses. Kicks off the first time the bot reports a
   *  scan_id; runs to completion independent of the chat stream. */
  const startPolling = useCallback((sid: string) => {
    const tick = () => {
      api.publicDemoStatus(sid).then((j) => {
        setJob(j);
        if (j.status === "running") {
          pollTimer.current = window.setTimeout(tick, POLL_MS);
        }
      }).catch(() => { /* transient — the bot's own polling still narrates */ });
    };
    tick();
  }, []);

  const handlers = useCallback((aid: string): CopilotHandlers => ({
    onSession: (info) => { sessionId.current = info.session_id; },
    onToken: (t) => patch(aid, (m) => ({ text: m.text + t })),
    onTool: (ev: CopilotToolEvent) => {
      if (ev.phase !== "done") return;
      patch(aid, (m) => ({
        tools: [...(m.tools ?? []), { tool: ev.tool, ok: ev.ok, kind: ev.kind, summary: ev.summary }],
      }));
      const sid = scanIdFrom(ev);
      if (sid && scanIdRef.current !== sid) {
        scanIdRef.current = sid;
        setScanId(sid);
        startPolling(sid);
      }
    },
    onApproval: (a) => patch(aid, () => ({ approval: a })),
    onDone: () => patch(aid, () => ({ streaming: false })),
    onError: (info) => patch(aid, () => ({ streaming: false, error: info })),
  }), [patch, startPolling]);

  const runError = useCallback(
    (aid: string) => (e: Error & { status?: number; info?: CopilotErrorInfo }) => {
      if (e?.name === "AbortError") return;
      const info: CopilotErrorInfo = e?.info ?? (
        e?.status === 503
          ? { code: "not_configured", message: "The safety assistant isn't configured on this server right now.", action: "none" }
          : e?.status === 429
          ? { code: "rate_limited", message: "That's a lot of questions in a short window — give it a moment and try again.", action: "retry" }
          : e?.status === 402
          ? { code: "daily_limit", message: "The demo has hit its daily message limit. Please try again later, or use the quick form.", action: "none" }
          : { code: "generic", message: "I couldn't reach the assistant. Please try again in a moment.", action: "retry" });
      patch(aid, () => ({ streaming: false, error: info }));
    }, [patch]);

  const send = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text || busy || available === false) return;
    const aid = uid("a");
    setMessages((ms) => [
      ...ms,
      { id: uid("u"), role: "user", text },
      { id: aid, role: "assistant", text: "", streaming: true, tools: [], retryText: text },
    ]);
    setInput("");
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    publicCopilotChat(text, sessionId.current, handlers(aid), ctrl.signal)
      .catch(runError(aid))
      .finally(() => { setBusy(false); abortRef.current = null; });
  }, [busy, available, handlers, runError]);

  const decide = useCallback((fromId: string, approved: boolean) => {
    if (!sessionId.current || busy) return;
    patch(fromId, () => ({ approval: null }));
    const aid = uid("a");
    setMessages((ms) => [
      ...ms,
      { id: aid, role: "assistant", text: "", streaming: true, tools: [] },
    ]);
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    publicCopilotApprove(sessionId.current, approved, handlers(aid), ctrl.signal)
      .catch(runError(aid))
      .finally(() => { setBusy(false); abortRef.current = null; });
  }, [busy, handlers, patch, runError]);

  // Honest offline state: never fake a conversation. Say so, and fall back.
  if (available === false) {
    return (
      <div className="intake-offline" role="status">
        <span className="intake-offline-ic" aria-hidden><IconWarning size={16}/></span>
        <p>
          The guided safety assistant is offline on this server right now. You can
          still run a scan with the quick form.
        </p>
        <button type="button" className="btn-primary" onClick={onFallback}>
          Use the quick form
        </button>
      </div>
    );
  }

  return (
    <div className={`intake ${scanId ? "has-scan" : ""}`}>
      <div className="intake-chat cp-surface">
        <div className="cp-thread intake-thread" role="log" aria-live="polite"
             aria-label="Safety assistant conversation">
          <ChatThread messages={messages} busy={busy}
                      onRetry={(t) => send(t)} onDecide={decide} />
          {available === null && (
            <p className="intake-connecting">Connecting to the safety assistant…</p>
          )}
          {messages.length <= 1 && available && (
            <div className="intake-suggest">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="cp-chip"
                        onClick={() => send(s)} disabled={busy}>{s}</button>
              ))}
            </div>
          )}
          <div ref={threadEnd} />
        </div>

        <form className="cp-composer intake-composer"
              onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <label className="cp-sr" htmlFor="intake-input">Ask the safety assistant</label>
          <div className="cp-composer-row">
            <textarea id="intake-input" ref={inputRef} rows={1} value={input}
                      placeholder="Describe your agent, or ask what a scan checks…"
                      disabled={busy && !input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
                      }} />
            <button type="submit" className="cp-send" aria-label="Send"
                    disabled={!input.trim() || busy}>
              {busy ? <span className="cp-spin" aria-hidden /> : <IconArrowUp size={16}/>}
            </button>
          </div>
          <p className="cp-foot">
            AI assistant — may be imperfect. The demo runs on our server, not your
            key. It asks before it runs anything.
          </p>
        </form>
      </div>

      {scanId && <ScanPanel scanId={scanId} job={job} dims={dims} />}
    </div>
  );
}
