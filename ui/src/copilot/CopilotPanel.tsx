/* ============================================================================
   Agenttic Copilot — right-docked, slide-out guide panel (app-only, lazy).

   A read-only helper that explains the platform and deep-links you to the right
   page. Streams Sonnet 4.6 token-by-token from POST /api/copilot/chat, renders
   Markdown (incl. clickable in-app deep-links that close the drawer on
   navigation), keeps the session's history, and shows an honest "AI assistant —
   may be imperfect" note. Accessible (focus management, aria, Esc to close) and
   reduced-motion friendly (the transition is CSS-gated).

   This whole module is code-split (see AppShell): its JS loads only when the
   user first opens the panel, so it never touches the public landing bundle or
   the app-shell's initial chunk.
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, copilotChat, type CopilotMsg } from "../api";
import { Markdown } from "./markdown";

interface Msg {
  id: string;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  error?: boolean;
}

let _seq = 0;
const uid = (p: string) => `${p}_${Date.now().toString(36)}_${_seq++}`;

const SUGGESTIONS = [
  "How does agent grading work?",
  "What does “NOT ASSESSED” mean?",
  "How do I certify my agent?",
  "Where do I add my Anthropic API key?",
];

export default function CopilotPanel({ open, onClose }: {
  open: boolean; onClose: () => void;
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [available, setAvailable] = useState<boolean | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const threadEnd = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // availability probe (does this server have the Copilot key wired?)
  useEffect(() => {
    let alive = true;
    api.copilotStatus()
      .then((s) => { if (alive) setAvailable(s.available); })
      .catch(() => { if (alive) setAvailable(false); });
    return () => { alive = false; };
  }, []);

  // focus the composer when opened; Esc closes
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => inputRef.current?.focus(), 60);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(t); window.removeEventListener("keydown", onKey); };
  }, [open, onClose]);

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  // cancel any in-flight stream if the panel unmounts
  useEffect(() => () => abortRef.current?.abort(), []);

  const patch = useCallback((id: string, fn: (m: Msg) => Partial<Msg>) => {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)));
  }, []);

  const send = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text || sending) return;
    const history: CopilotMsg[] = messages
      .filter((m) => !m.error && m.text.trim())
      .map((m) => ({ role: m.role, content: m.text }));
    history.push({ role: "user", content: text });

    const aid = uid("a");
    setMessages((ms) => [
      ...ms,
      { id: uid("u"), role: "user", text },
      { id: aid, role: "assistant", text: "", streaming: true },
    ]);
    setInput("");
    setSending(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    copilotChat(history, {
      onToken: (t) => patch(aid, (m) => ({ text: m.text + t })),
      onError: (msg) => patch(aid, () => ({ text: msg, streaming: false, error: true })),
      onDone: () => patch(aid, (m) => ({
        streaming: false,
        text: m.text || "I don't have an answer for that. Try the "
          + "[Methodology](/methodology) page or rephrase your question.",
      })),
    }, ctrl.signal)
      .catch((e: Error & { status?: number }) => {
        const msg = e?.status === 503
          ? "The Copilot isn't configured on this server yet."
          : e?.status === 429
          ? "You're sending messages a little fast — give it a few seconds."
          : "I couldn't reach the Copilot service. Please try again in a moment.";
        patch(aid, () => ({ text: msg, streaming: false, error: true }));
      })
      .finally(() => { setSending(false); abortRef.current = null; });
  }, [messages, sending, patch]);

  const empty = messages.length === 0;

  return (
    <>
      <div className={`cp-scrim ${open ? "open" : ""}`} onClick={onClose} aria-hidden />
      <aside
        className={`cp-panel ${open ? "open" : ""}`}
        role="complementary"
        aria-label="Agenttic Copilot"
        aria-hidden={!open}
      >
        <header className="cp-head">
          <span className="cp-brand">
            <span className="cp-brand-ic" aria-hidden>⬡</span>
            Copilot
          </span>
          <span className="cp-brand-sub">Platform guide</span>
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
              <h2 className="cp-empty-title">How can I help you use Agenttic?</h2>
              <p className="cp-empty-sub">
                Ask about scanning &amp; grading agents, the methodology, certification,
                dossiers, passports, or the <code className="cp-code">ascore</code> CLI.
                I explain and point you to the right place — I don't change anything.
              </p>
              <div className="cp-suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="cp-chip" onClick={() => send(s)}
                          disabled={available === false}>{s}</button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`cp-msg ${m.role}`}>
                {m.role === "assistant" && <span className="cp-av" aria-hidden>⬡</span>}
                <div className={`cp-bubble ${m.role} ${m.error ? "error" : ""}`}>
                  {m.role === "assistant" ? (
                    m.text
                      ? <Markdown text={m.text} onNavigate={onClose} />
                      : <span className="cp-typing" aria-label="Copilot is thinking"><span /><span /><span /></span>
                  ) : m.text}
                  {m.streaming && m.text && <span className="cp-caret" aria-hidden />}
                </div>
              </div>
            ))
          )}
          <div ref={threadEnd} />
        </div>

        <form className="cp-composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <label className="cp-sr" htmlFor="cp-input">Ask the Copilot</label>
          <div className="cp-composer-row">
            <textarea
              id="cp-input" ref={inputRef} rows={1} value={input}
              placeholder="Ask about the platform…"
              disabled={available === false}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
              }}
            />
            <button type="submit" className="cp-send" aria-label="Send"
                    disabled={!input.trim() || sending || available === false}>
              {sending ? <span className="cp-spin" aria-hidden /> : "↑"}
            </button>
          </div>
          <p className="cp-foot">
            AI assistant — may be imperfect. It can be wrong; verify important
            details on the linked pages. It can't run scans or change your settings.
          </p>
        </form>
      </aside>
    </>
  );
}
