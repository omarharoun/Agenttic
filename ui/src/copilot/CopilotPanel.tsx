/* ============================================================================
   Agenttic Copilot — right-docked, slide-out AGENTIC assistant (app-only, lazy).

   An AI agent whose tools are the platform API, scoped to the signed-in user. It
   reads freely and PROPOSES write/cost actions the user must confirm. The drawer
   streams the agent's answer token-by-token, shows live tool activity ("Fetching
   dossier…", "Running certification…"), renders inline approval cards for
   write/cost actions (Confirm / Deny), and handles clarifying questions as
   ordinary messages. Honest ("AI assistant — may be imperfect"), accessible
   (focus mgmt, aria, Esc-to-close), reduced-motion friendly.

   Code-split (see AppShell): the chunk loads only on first open, so it never
   touches the public landing bundle or the app-shell's initial chunk.
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, copilotApprove, copilotChat,
  type CopilotApproval, type CopilotErrorInfo, type CopilotHandlers,
  type CopilotToolEvent,
} from "../api";
import { Markdown } from "./markdown";

interface ToolAct { tool: string; ok?: boolean; kind?: string; summary?: string; }

interface Msg {
  id: string;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  error?: CopilotErrorInfo | null;   // set → render the styled error card
  retryText?: string;                // the user message to resend on "Try again"
  tools?: ToolAct[];
  approval?: CopilotApproval | null;
}

/** Where "Upgrade or add credits" sends the user. The pricing/billing surface
 *  ships alongside real billing; until then this is a forward-compatible link. */
const BILLING_URL = "/pricing";

/** Per-code presentation for the error card (title + glyph + tone). The honest
 *  body copy comes from the server so the two stay in sync. */
const ERROR_UI: Record<string, { title: string; icon: string; tone: string }> = {
  unavailable:    { title: "Copilot unavailable",    icon: "⚠", tone: "warn" },
  rate_limited:   { title: "One moment",             icon: "◔", tone: "warn" },
  out_of_credits: { title: "Out of credits",         icon: "⬡", tone: "credits" },
  daily_limit:    { title: "Daily limit reached",    icon: "◷", tone: "warn" },
  not_configured: { title: "Copilot not configured", icon: "⚙", tone: "warn" },
  generic:        { title: "Something went wrong",   icon: "⚠", tone: "warn" },
};

let _seq = 0;
const uid = (p: string) => `${p}_${Date.now().toString(36)}_${_seq++}`;

const SUGGESTIONS = [
  "What agents do I have?",
  "Certify ref-agent with the safety profile",
  "Is the platform healthy right now?",
  "What does “NOT ASSESSED” mean?",
];

/** Human-readable label for a tool while it runs / after it's done. */
function toolLabel(t: ToolAct): string {
  const names: Record<string, string> = {
    platform_status: "Checking platform status",
    list_agents: "Listing your agents",
    list_certification_profiles: "Listing certification profiles",
    get_certification_profile: "Fetching profile",
    list_dossiers: "Listing dossiers",
    get_dossier: "Fetching dossier",
    verify_dossier: "Verifying dossier",
    get_certification_job: "Checking certification job",
    anthropic_key_status: "Checking API-key status",
    start_certification: "Running certification",
    revoke_certification: "Revoking certificate",
  };
  return names[t.tool] ?? t.tool;
}

export default function CopilotPanel({ open, onClose }: {
  open: boolean; onClose: () => void;
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [available, setAvailable] = useState<boolean | null>(null);
  const sessionId = useRef<string | null>(null);
  const target = useRef<string>("");        // assistant msg id the stream writes to
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const threadEnd = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let alive = true;
    api.copilotStatus()
      .then((s) => { if (alive) setAvailable(s.available); })
      .catch(() => { if (alive) setAvailable(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => inputRef.current?.focus(), 60);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(t); window.removeEventListener("keydown", onKey); };
  }, [open, onClose]);

  useEffect(() => { threadEnd.current?.scrollIntoView({ block: "end" }); }, [messages]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const patch = useCallback((id: string, fn: (m: Msg) => Partial<Msg>) => {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)));
  }, []);

  /** Shared stream handlers writing into the current target assistant message. */
  const handlers = useCallback((aid: string): CopilotHandlers => ({
    onSession: (info) => { sessionId.current = info.session_id; },
    onToken: (t) => patch(aid, (m) => ({ text: m.text + t })),
    onTool: (ev: CopilotToolEvent) => {
      if (ev.phase !== "done") return;
      patch(aid, (m) => ({
        tools: [...(m.tools ?? []), { tool: ev.tool, ok: ev.ok, kind: ev.kind, summary: ev.summary }],
      }));
    },
    onApproval: (a) => patch(aid, () => ({ approval: a })),
    onDone: () => patch(aid, () => ({ streaming: false })),
    onError: (info) => patch(aid, () => ({ streaming: false, error: info })),
  }), [patch]);

  // A refused/failed request (thrown before the stream): build the same
  // classified error card. Prefer the server's structured detail; otherwise map
  // the status code to an honest fallback.
  const runError = useCallback(
    (aid: string) => (e: Error & { status?: number; info?: CopilotErrorInfo }) => {
      const info: CopilotErrorInfo = e?.info ?? (
        e?.status === 503
          ? { code: "not_configured", message: "The Copilot isn't configured on this server yet.", action: "none" }
          : e?.status === 429
          ? { code: "rate_limited", message: "You're sending messages too fast — give it a moment and try again.", action: "retry" }
          : e?.status === 402
          ? { code: "out_of_credits", message: "You're out of credits. Upgrade your plan or add credits to keep using the Copilot.", action: "upgrade" }
          : { code: "generic", message: "I couldn't reach the Copilot service. Please try again in a moment.", action: "retry" });
      patch(aid, () => ({ streaming: false, error: info }));
    }, [patch]);

  const send = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text || busy) return;
    const aid = uid("a");
    target.current = aid;
    setMessages((ms) => [
      ...ms,
      { id: uid("u"), role: "user", text },
      { id: aid, role: "assistant", text: "", streaming: true, tools: [], retryText: text },
    ]);
    setInput("");
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    copilotChat(text, sessionId.current, handlers(aid), ctrl.signal)
      .catch(runError(aid))
      .finally(() => { setBusy(false); abortRef.current = null; });
  }, [busy, handlers, runError]);

  const decide = useCallback((fromId: string, approved: boolean) => {
    if (!sessionId.current || busy) return;
    patch(fromId, () => ({ approval: null }));   // consume the card
    const aid = uid("a");
    target.current = aid;
    setMessages((ms) => [
      ...ms,
      { id: aid, role: "assistant", text: "", streaming: true, tools: [] },
    ]);
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    copilotApprove(sessionId.current, approved, handlers(aid), ctrl.signal)
      .catch(runError(aid))
      .finally(() => { setBusy(false); abortRef.current = null; });
  }, [busy, handlers, patch, runError]);

  const empty = messages.length === 0;

  return (
    <>
      <div className={`cp-scrim ${open ? "open" : ""}`} onClick={onClose} aria-hidden />
      <aside className={`cp-panel ${open ? "open" : ""}`} role="complementary"
             aria-label="Agenttic Copilot" aria-hidden={!open}>
        <header className="cp-head">
          <span className="cp-brand"><span className="cp-brand-ic" aria-hidden>⬡</span>Copilot</span>
          <span className="cp-brand-sub">Agent</span>
          <span style={{ flex: 1 }} />
          <button className="cp-x" onClick={onClose} aria-label="Close Copilot" title="Close (Esc)">✕</button>
        </header>

        {available === false && (
          <div className="cp-banner" role="status">
            The Copilot isn't configured on this server yet. An administrator needs
            to set the Copilot's Anthropic key.
          </div>
        )}

        <div className="cp-thread" role="log" aria-live="polite" aria-label="Copilot conversation">
          {empty ? (
            <div className="cp-empty">
              <div className="cp-empty-ic" aria-hidden>⬡</div>
              <h2 className="cp-empty-title">What can I help you do?</h2>
              <p className="cp-empty-sub">
                I can look things up and run tasks for you — list your agents, check
                a dossier, start a certification. I always ask before spending your
                budget or changing anything.
              </p>
              <div className="cp-suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="cp-chip" onClick={() => send(s)}
                          disabled={available === false || busy}>{s}</button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`cp-msg ${m.role}`}>
                {m.role === "assistant" && <span className="cp-av" aria-hidden>⬡</span>}
                <div className="cp-msg-body">
                  {/* live tool activity */}
                  {m.role === "assistant" && m.tools && m.tools.length > 0 && (
                    <ul className="cp-tools" aria-label="What the Copilot did">
                      {m.tools.map((t, i) => (
                        <li key={i} className={`cp-tool ${t.ok === false ? "bad" : "ok"} ${t.kind === "write" ? "write" : ""}`}>
                          <span className="cp-tool-ic" aria-hidden>{t.ok === false ? "✕" : "✓"}</span>
                          <span className="cp-tool-lbl">{toolLabel(t)}</span>
                          {t.summary && <span className="cp-tool-sum">{t.summary}</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                  {/* running indicator while streaming with no text yet */}
                  {m.role === "assistant" && m.streaming && !m.text && !m.approval && !m.error && (
                    <span className="cp-typing" aria-label="Copilot is working"><span /><span /><span /></span>
                  )}
                  {/* the message text */}
                  {m.text && (
                    <div className={`cp-bubble ${m.role}`}>
                      {m.role === "assistant"
                        ? <Markdown text={m.text} onNavigate={onClose} />
                        : m.text}
                      {m.streaming && m.text && <span className="cp-caret" aria-hidden />}
                    </div>
                  )}
                  {/* styled inline error card — honest, per-case, with an affordance */}
                  {m.role === "assistant" && m.error && (
                    <ErrorCard info={m.error} busy={busy}
                               onRetry={m.retryText ? () => send(m.retryText!) : undefined}
                               onUpgrade={() => { onClose(); window.location.assign(BILLING_URL); }} />
                  )}
                  {/* inline approval card for a proposed write/cost action */}
                  {m.role === "assistant" && m.approval && (
                    <ApprovalCard a={m.approval} busy={busy}
                                  onDecide={(ok) => decide(m.id, ok)} />
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={threadEnd} />
        </div>

        <form className="cp-composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <label className="cp-sr" htmlFor="cp-input">Ask the Copilot</label>
          <div className="cp-composer-row">
            <textarea id="cp-input" ref={inputRef} rows={1} value={input}
                      placeholder="Ask the Copilot to look something up or run a task…"
                      disabled={available === false}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
                      }} />
            <button type="submit" className="cp-send" aria-label="Send"
                    disabled={!input.trim() || busy || available === false}>
              {busy ? <span className="cp-spin" aria-hidden /> : "↑"}
            </button>
          </div>
          <p className="cp-foot">
            AI assistant — may be imperfect. It can be wrong; verify important
            details. It asks before spending your budget or changing anything.
          </p>
        </form>
      </aside>
    </>
  );
}

/** A styled, honest error state for the drawer: an icon, a per-case title, the
 *  server's safe message, and the right affordance (retry the turn, or upgrade).
 *  Uses the Chronometer tokens so it reads as part of the panel, light + dark. */
function ErrorCard({ info, busy, onRetry, onUpgrade }: {
  info: CopilotErrorInfo; busy: boolean;
  onRetry?: () => void; onUpgrade: () => void;
}) {
  const ui = ERROR_UI[info.code] ?? ERROR_UI.generic;
  const action = info.action ?? "retry";
  return (
    <div className={`cp-error ${ui.tone}`} role="alert">
      <span className="cp-error-ic" aria-hidden>{ui.icon}</span>
      <div className="cp-error-body">
        <p className="cp-error-title">{ui.title}</p>
        <p className="cp-error-msg">{info.message}</p>
        {action === "upgrade" ? (
          <button type="button" className="cp-error-btn primary" onClick={onUpgrade}>
            Upgrade or add credits
          </button>
        ) : action === "retry" && onRetry ? (
          <button type="button" className="cp-error-btn" disabled={busy} onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** The confirmation card for a proposed write/cost action. Blocks the turn until
 *  the user decides; keyboard-focusable. */
function ApprovalCard({ a, busy, onDecide }: {
  a: CopilotApproval; busy: boolean; onDecide: (approved: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  const risk = a.card.risk ?? "medium";
  return (
    <div className={`cp-approval risk-${risk}`} role="group" tabIndex={-1} ref={ref}
         aria-label="Action needs your confirmation">
      <div className="cp-approval-head">
        <span className="cp-approval-ic" aria-hidden>🔐</span>
        <span className="cp-approval-tag">Confirm before running</span>
        <span className={`cp-approval-risk risk-${risk}`}>{risk} risk</span>
      </div>
      <h3 className="cp-approval-title">{a.card.title ?? `Run ${a.tool}?`}</h3>
      {a.card.detail && <p className="cp-approval-detail">{a.card.detail}</p>}
      {a.card.cost_note && (
        <p className="cp-approval-cost"><span aria-hidden>💳</span> {a.card.cost_note}</p>
      )}
      <p className="cp-approval-reassure">Nothing happens until you choose.</p>
      <div className="cp-approval-actions">
        <button type="button" className="cp-btn-confirm" disabled={busy}
                onClick={() => onDecide(true)}>Confirm &amp; run</button>
        <button type="button" className="cp-btn-deny" disabled={busy}
                onClick={() => onDecide(false)}>Cancel</button>
      </div>
    </div>
  );
}
