/* ============================================================================
   The Safe Assistant — agenttic's flagship consumer chat.

   A friendly personal assistant that makes its *safety* visible: it shows every
   tool it uses ("opened a web page", "did the math"), and — the trust-defining
   moment — it pauses for an inline approval before doing anything sensitive. A
   safety-posture rail keeps the differentiator in view; a safety grade is shown
   only once a real certificate is issued (otherwise "certification pending").
   End-user voice throughout; mobile-first; keyboard accessible.

   Talks to the sibling backend (POST sessions / message / approve, GET session)
   and degrades gracefully to a clearly-labelled local preview if it's absent.
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import {
  type AssistantMessage, type AssistantStep, type ApprovalDecision,
  buildPreviewTurn, DEFAULT_POSTURE, EXAMPLE_PROMPTS, localId,
  normalizeSession, type PendingApproval, type PreviewTurn, postureLine,
  riskTone, type SafetyPosture, type SessionStatus,
} from "../assistant";
import { Seal } from "./Seal";
import { type AssistantCert, useAssistantCert } from "../useAssistantCert";

const POLL_MS = 800;

/* --------------------------- presentational ------------------------------ */

function StepRow({ s }: { s: AssistantStep }) {
  const ic =
    s.status === "running" ? <span className="asst-step-spin" aria-hidden /> :
    s.kind === "thought" ? <span className="asst-step-ic think" aria-hidden>✎</span> :
    s.kind === "tool_result" ? <span className="asst-step-ic ok" aria-hidden>✓</span> :
    <span className="asst-step-ic tool" aria-hidden>⚙</span>;
  return (
    <li className="asst-step">
      {ic}
      <span className="asst-step-body">
        <span className="asst-step-summary">{s.summary}</span>
        {s.detail && <span className="asst-step-detail">{s.detail}</span>}
      </span>
    </li>
  );
}

/** The human-in-the-loop approval card — blocks the turn until the user picks. */
function ApprovalCard({ p, onDecide, busy }: {
  p: PendingApproval; onDecide: (d: ApprovalDecision) => void; busy: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  const tone = riskTone(p.risk);
  return (
    <div className={`asst-approval ${tone}`} role="alertdialog" aria-modal="false"
         aria-labelledby="asst-approval-title" tabIndex={-1} ref={ref}>
      <div className="asst-approval-head">
        <span className="asst-approval-ic" aria-hidden>🔐</span>
        <span className="asst-approval-tag">Your approval needed</span>
        <span className={`asst-approval-risk ${tone}`}>{p.risk ?? "low"} risk</span>
      </div>
      <h3 id="asst-approval-title" className="asst-approval-title">{p.title}</h3>
      {p.target && <code className="asst-approval-target">{p.target}</code>}
      {p.description && <p className="asst-approval-desc">{p.description}</p>}
      <p className="asst-approval-reassure">
        Nothing happens until you decide. The assistant is paused.
      </p>
      <div className="asst-approval-actions">
        <button type="button" className="btn-primary" disabled={busy}
                onClick={() => onDecide("allow")}>Allow once</button>
        <button type="button" className="btn-ghost" disabled={busy}
                onClick={() => onDecide("deny")}>Deny</button>
      </div>
    </div>
  );
}

function MessageBubble({ m, onDecide, decideBusy }: {
  m: AssistantMessage;
  onDecide: (d: ApprovalDecision) => void;
  decideBusy: boolean;
}) {
  const isUser = m.role === "user";
  const thinking = !isUser && m.status === "streaming" && !m.text;
  return (
    <div className={`asst-msg ${isUser ? "user" : "assistant"}`}>
      {!isUser && <span className="asst-avatar" aria-hidden>⬡</span>}
      <div className="asst-msg-body">
        {/* transparent tool-use trail */}
        {!isUser && m.steps && m.steps.length > 0 && (
          <ul className="asst-steps" aria-label="What the assistant did">
            {m.steps.map((s) => <StepRow key={s.id} s={s} />)}
          </ul>
        )}
        {/* pending sensitive action → approval card, inline, blocking */}
        {!isUser && m.pending && (
          <ApprovalCard p={m.pending} onDecide={onDecide} busy={decideBusy} />
        )}
        {/* the message text */}
        {thinking ? (
          <div className="asst-typing" aria-label="Assistant is thinking">
            <span /><span /><span />
          </div>
        ) : m.text ? (
          <div className={`asst-bubble ${isUser ? "user" : "assistant"}`}>{m.text}</div>
        ) : null}
      </div>
    </div>
  );
}

/** The always-visible safety differentiator: posture + tools + safety seal.
 *  The safety GRADE comes from the single-source ``useAssistantCert`` hook (the
 *  same one the assistant page's top line + the landing page read), so the rail
 *  card and footer can never contradict the headline. A grade is shown ONLY when
 *  a real, verifiable certificate backs it; otherwise an honest "pending" state. */
function SafetyRail({ posture, cert }: { posture: SafetyPosture; cert: AssistantCert | null }) {
  const certified = Boolean(cert);
  return (
    <aside className="asst-rail" aria-label="Safety posture">
      <div className="asst-rail-seal">
        {certified ? (
          <>
            <Link to={`/certified/${cert!.cert_id}`}
                  title="View the public safety certificate" className="asst-seal-link">
              <Seal grade={cert!.grade} size={104} />
            </Link>
            <div className="asst-rail-cert">
              <b>Agenttic Safety Certified — Grade {cert!.grade}</b>
              <Link to={`/certified/${cert!.cert_id}`} className="asst-cert-link">
                View certificate ↗</Link>
            </div>
          </>
        ) : (
          <>
            <Seal size={104} />
            <div className="asst-rail-cert">
              <b>Safety certification pending</b>
              <Link to="/methodology" className="asst-cert-link">How grading works ↗</Link>
            </div>
          </>
        )}
      </div>

      <ul className="asst-posture">
        <li className="ok"><span className="ap-ic" aria-hidden>🛡</span>
          <span>{posture.sandboxed ? "Runs in a sandbox" : "Sandbox status unknown"}</span></li>
        <li className="ok"><span className="ap-ic" aria-hidden>📁</span>
          <span>{posture.file_access ? "Can read files" : "Can't touch your files"}</span></li>
        <li className="ok"><span className="ap-ic" aria-hidden>🔑</span>
          <span>{posture.credential_access ? "Has credential access" : "No access to your secrets"}</span></li>
        <li className="ok"><span className="ap-ic" aria-hidden>✋</span>
          <span>{posture.approval_required ? "Sensitive actions need your OK" : "No approval gate"}</span></li>
      </ul>

      <div className="asst-tools">
        <span className="asst-tools-cap">What it can do</span>
        <ul>
          {posture.tools.map((t) => (
            <li key={t.name}>
              <span className="at-dot" aria-hidden />{t.label}
              {t.sensitive && <span className="at-gate" title="Asks before using">needs OK</span>}
            </li>
          ))}
        </ul>
      </div>

      <p className="asst-rail-foot">
        Safe by construction — sandboxed, least-privilege, and human-in-the-loop.
        {certified
          ? <> Its safety grade has been independently measured and published:{" "}
              <Link to={`/certified/${cert!.cert_id}`}>Grade {cert!.grade}</Link>.{" "}</>
          : " Its safety grade is published only once independently measured. "}
        <Link to="/methodology">How grading works ↗</Link>
      </p>
    </aside>
  );
}

/* ------------------------------ the chat --------------------------------- */

export function AssistantChat() {
  // Single source of truth for the safety grade — shared with the assistant
  // page's top line + the landing page, so the rail can't drift out of sync.
  const cert = useAssistantCert();
  const [mode, setMode] = useState<"init" | "live" | "preview">("init");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [posture, setPosture] = useState<SafetyPosture>(DEFAULT_POSTURE);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [decideBusy, setDecideBusy] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");

  const timers = useRef<number[]>([]);
  const pollTimer = useRef<number | undefined>(undefined);
  // preview: action_id → { turn, aid } so allow/deny can resume the right turn
  const previewTurns = useRef<Record<string, { turn: PreviewTurn; aid: string }>>({});
  const threadEnd = useRef<HTMLDivElement>(null);

  const schedule = useCallback((fn: () => void, ms: number) => {
    const id = window.setTimeout(fn, ms);
    timers.current.push(id);
  }, []);

  const patchMessage = useCallback(
    (id: string, patch: (m: AssistantMessage) => Partial<AssistantMessage>) => {
      setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)));
    }, []);

  // create a session up-front so the safety posture shows on the empty state;
  // fall back to a labelled local preview if the backend isn't reachable.
  useEffect(() => {
    let alive = true;
    api.createAssistantSession()
      .then((raw) => {
        if (!alive) return;
        const s = normalizeSession(raw);
        setSessionId(s.session_id || raw?.session_id || null);
        if (s.posture) setPosture(s.posture);
        setMode(s.session_id ? "live" : "preview");
      })
      .catch(() => { if (alive) { setMode("preview"); setPosture(DEFAULT_POSTURE); } });
    // The safety grade + cert id come from the shared useAssistantCert hook
    // (single source of truth), not a second fetch here — so the rail's seal,
    // card, and footer always match the assistant-page headline.
    return () => {
      alive = false;
      timers.current.forEach(window.clearTimeout);
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, []);

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  /* ---- apply a normalized server turn into the live assistant placeholder -- */
  const applyServerTurn = useCallback((raw: any, aid: string) => {
    const s = normalizeSession(raw, sessionId ?? "");
    if (s.posture) setPosture(s.posture);
    const last = [...s.messages].reverse().find((m) => m.role === "assistant");
    patchMessage(aid, () => ({
      steps: last?.steps ?? [],
      text: last?.text ?? "",
      pending: s.pending,
      status: s.status === "idle" || s.status === "error" ? "complete" : "streaming",
    }));
    setPending(s.pending);
    setStatus(s.status);
    return s;
  }, [patchMessage, sessionId]);

  const poll = useCallback((sid: string, aid: string) => {
    api.getAssistantSession(sid)
      .then((raw) => {
        const s = applyServerTurn(raw, aid);
        if (s.status === "thinking") {
          pollTimer.current = window.setTimeout(() => poll(sid, aid), POLL_MS);
        }
      })
      .catch(() => {
        patchMessage(aid, () => ({
          text: "I lost the connection to the assistant service. Please try again in a moment.",
          status: "error",
        }));
        setStatus("idle");
      });
  }, [applyServerTurn, patchMessage]);

  /* ---- preview engine (offline, deterministic, honestly labelled) --------- */
  const runPreview = useCallback((text: string, aid: string) => {
    const turn = buildPreviewTurn(text);
    setStatus("thinking");
    let delay = 420;
    turn.steps.forEach((step) => {
      schedule(() => patchMessage(aid, (m) => ({ steps: [...(m.steps ?? []), step] })), delay);
      delay += 580;
    });
    if (turn.pending) {
      const p = turn.pending;
      previewTurns.current[p.action_id] = { turn, aid };
      schedule(() => {
        patchMessage(aid, () => ({ pending: p }));
        setPending(p);
        setStatus("awaiting_approval");
      }, delay);
    } else {
      schedule(() => {
        patchMessage(aid, () => ({ text: turn.answer, status: "complete" }));
        setStatus("idle");
      }, delay + 220);
    }
  }, [patchMessage, schedule]);

  const resolvePreview = useCallback((actionId: string, decision: ApprovalDecision) => {
    const rec = previewTurns.current[actionId];
    if (!rec) return;
    const { turn, aid } = rec;
    delete previewTurns.current[actionId];
    patchMessage(aid, () => ({ pending: null }));
    setPending(null);
    setStatus("thinking");
    if (decision === "deny") {
      schedule(() => {
        patchMessage(aid, () => ({ text: turn.denyAnswer, status: "complete" }));
        setStatus("idle");
      }, 480);
    } else {
      let delay = 480;
      turn.afterAllow.forEach((step) => {
        schedule(() => patchMessage(aid, (m) => ({ steps: [...(m.steps ?? []), step] })), delay);
        delay += 560;
      });
      schedule(() => {
        patchMessage(aid, () => ({ text: turn.answer, status: "complete" }));
        setStatus("idle");
      }, delay + 200);
    }
  }, [patchMessage, schedule]);

  /* ---- send / approve ----------------------------------------------------- */
  const send = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed || status === "thinking" || status === "awaiting_approval") return;
    setError("");
    const aid = localId("a");
    setMessages((ms) => [
      ...ms,
      { id: localId("u"), role: "user", text: trimmed, status: "complete" },
      { id: aid, role: "assistant", text: "", steps: [], pending: null, status: "streaming" },
    ]);
    setInput("");

    if (mode === "preview" || !sessionId) {
      runPreview(trimmed, aid);
      return;
    }
    setStatus("thinking");
    api.sendAssistantMessage(sessionId, trimmed)
      .then((raw) => {
        const s = applyServerTurn(raw, aid);
        if (s.status === "thinking") poll(sessionId, aid);
      })
      .catch(() => {
        // backend went away mid-session — degrade to preview for this turn
        setMode("preview");
        runPreview(trimmed, aid);
      });
  }, [mode, sessionId, status, applyServerTurn, poll, runPreview]);

  const decide = useCallback((decision: ApprovalDecision) => {
    if (!pending) return;
    const actionId = pending.action_id;
    setDecideBusy(true);
    const done = () => setDecideBusy(false);

    if (mode === "preview" || !sessionId) {
      resolvePreview(actionId, decision);
      done();
      return;
    }
    // find the assistant placeholder carrying this pending action
    const aid = [...messages].reverse().find((m) => m.pending?.action_id === actionId)?.id
      ?? messages[messages.length - 1]?.id ?? localId("a");
    api.approveAssistantAction(sessionId, actionId, decision)
      .then((raw) => {
        const s = applyServerTurn(raw, aid);
        if (s.status === "thinking") poll(sessionId, aid);
      })
      .catch(() => {
        patchMessage(aid, () => ({
          text: "I couldn't reach the assistant service to act on that. Please try again.",
          status: "error", pending: null,
        }));
        setPending(null);
        setStatus("idle");
      })
      .finally(done);
  }, [pending, mode, sessionId, messages, applyServerTurn, poll, patchMessage, resolvePreview]);

  const busy = status === "thinking" || status === "awaiting_approval";
  const empty = messages.length === 0;

  return (
    <div className="asst-shell">
      <div className="asst-main">
        {/* compact posture badge — the differentiator, always in view */}
        <div className="asst-posture-badge" role="note">
          <span className="apb-ic" aria-hidden>🛡</span>
          <span className="apb-text">{postureLine(posture)}</span>
          {mode === "preview" && (
            <span className="apb-preview" title="The assistant service isn't connected in this build">
              Preview
            </span>
          )}
        </div>

        <div className="asst-thread" role="log" aria-live="polite" aria-label="Conversation">
          {empty ? (
            <div className="asst-empty">
              <Seal grade={cert?.grade} size={92} />
              <h2>Your safe AI assistant</h2>
              <p className="asst-empty-sub">
                It asks before doing anything sensitive, and it can't touch your
                files or secrets. Everything it does, you can see.
              </p>
              {mode === "preview" && (
                <p className="asst-empty-preview">
                  You're trying a local preview — replies are illustrative, but the
                  safety behaviour (showing its work, asking permission) is real.
                </p>
              )}
              <div className="asst-examples">
                {EXAMPLE_PROMPTS.map((ex) => (
                  <button type="button" key={ex.label} className="asst-example"
                          disabled={busy} onClick={() => send(ex.text)}>
                    <b>{ex.label}</b><span>{ex.text}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <MessageBubble key={m.id} m={m} onDecide={decide} decideBusy={decideBusy} />
            ))
          )}
          <div ref={threadEnd} />
        </div>

        <form className="asst-composer"
              onSubmit={(e) => { e.preventDefault(); send(input); }}>
          {error && <p className="asst-error">{error}</p>}
          <label className="sr-only" htmlFor="asst-input">Message the assistant</label>
          <div className="asst-composer-row">
            <textarea id="asst-input" rows={1} value={input}
                      placeholder={status === "awaiting_approval"
                        ? "Respond to the approval above to continue…"
                        : "Ask your safe assistant anything…"}
                      disabled={status === "awaiting_approval"}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault(); send(input);
                        }
                      }} />
            <button type="submit" className="btn-primary asst-send"
                    disabled={!input.trim() || busy}
                    aria-label="Send message">
              {status === "thinking" ? <span className="spinner" /> : "Send"}
            </button>
          </div>
          <p className="asst-composer-foot">
            Sandboxed · sensitive actions pause for your approval · press Enter to send
          </p>
        </form>
      </div>

      <SafetyRail posture={posture} cert={cert} />
    </div>
  );
}
