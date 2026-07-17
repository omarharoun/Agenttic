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
  type CopilotErrorInfo, type CopilotHandlers, type CopilotToolEvent,
} from "../api";
import { ChatThread, type ChatMsg as Msg } from "./chatThread";

/** Where "Upgrade or add credits" sends the user. The pricing/billing surface
 *  ships alongside real billing; until then this is a forward-compatible link. */
const BILLING_URL = "/pricing";

let _seq = 0;
const uid = (p: string) => `${p}_${Date.now().toString(36)}_${_seq++}`;

const SUGGESTIONS = [
  "What agents do I have?",
  "Certify ref-agent with the safety profile",
  "Is the platform healthy right now?",
  "What does “NOT ASSESSED” mean?",
];

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
            <ChatThread messages={messages} busy={busy}
                        onRetry={(t) => send(t)}
                        onUpgrade={() => { onClose(); window.location.assign(BILLING_URL); }}
                        onDecide={decide} onNavigate={onClose} />
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
