/* ============================================================================
   Shared presentational chat surface for the streaming Copilot bots.

   Both the app-only, right-docked Copilot drawer (CopilotPanel) and the public
   /scan intake bot (IntakeBot) stream the SAME SSE protocol — tokens, tool
   activity, inline approval cards, classified error cards. This module owns the
   presentation of ONE conversation thread so the two surfaces stay pixel- and
   behaviour-identical: the same tool rows, the same "Confirm & run" approval
   card, the same honest error card. Each surface supplies its own chrome
   (header, composer, empty state) and drives the stream; this renders the turns.

   It carries no fetch and no session state — pure props in, elements out — so it
   never touches auth and is safe on the public bundle.
   ========================================================================== */

import { useEffect, useRef } from "react";
import type { CopilotApproval, CopilotErrorInfo } from "../api";
import { Markdown } from "./markdown";

export interface ToolAct { tool: string; ok?: boolean; kind?: string; summary?: string; }

export interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  error?: CopilotErrorInfo | null;   // set → render the styled error card
  retryText?: string;                // the user message to resend on "Try again"
  tools?: ToolAct[];
  approval?: CopilotApproval | null;
}

/** Per-code presentation for the error card (title + glyph + tone). The honest
 *  body copy comes from the server so the two stay in sync. */
const ERROR_UI: Record<string, { title: string; icon: string; tone: string }> = {
  unavailable:    { title: "Assistant unavailable",   icon: "⚠", tone: "warn" },
  rate_limited:   { title: "One moment",              icon: "◔", tone: "warn" },
  out_of_credits: { title: "Out of credits",          icon: "⬡", tone: "credits" },
  daily_limit:    { title: "Daily limit reached",     icon: "◷", tone: "warn" },
  not_configured: { title: "Assistant not configured", icon: "⚙", tone: "warn" },
  generic:        { title: "Something went wrong",    icon: "⚠", tone: "warn" },
};

/** Human-readable label for a tool while it runs / after it's done. Covers both
 *  the authed Copilot tools and the public intake bot's 4-tool allowlist. */
export function toolLabel(t: ToolAct): string {
  const names: Record<string, string> = {
    // public intake-bot allowlist
    preview_scan: "Looking up what a scan checks",
    start_demo_scan: "Starting the demo scan",
    get_scan_status: "Checking scan progress",
    get_scan_findings: "Reading the per-probe findings",
    // authed copilot tools
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

/** One assistant/user turn: live tool activity, the streamed bubble, and any
 *  inline error or approval card. Presentation only — the parent owns the
 *  handlers. */
function ChatTurn({ m, busy, onRetry, onUpgrade, onDecide, onNavigate }: {
  m: ChatMsg; busy: boolean;
  onRetry?: (text: string) => void;
  onUpgrade?: () => void;
  onDecide: (fromId: string, approved: boolean) => void;
  onNavigate?: () => void;
}) {
  return (
    <div className={`cp-msg ${m.role}`}>
      {m.role === "assistant" && <span className="cp-av" aria-hidden>⬡</span>}
      <div className="cp-msg-body">
        {m.role === "assistant" && m.tools && m.tools.length > 0 && (
          <ul className="cp-tools" aria-label="What the assistant did">
            {m.tools.map((t, i) => (
              <li key={i} className={`cp-tool ${t.ok === false ? "bad" : "ok"} ${t.kind === "write" ? "write" : ""}`}>
                <span className="cp-tool-ic" aria-hidden>{t.ok === false ? "✕" : "✓"}</span>
                <span className="cp-tool-lbl">{toolLabel(t)}</span>
              </li>
            ))}
          </ul>
        )}
        {m.role === "assistant" && m.streaming && !m.text && !m.approval && !m.error && (
          <span className="cp-typing" aria-label="Assistant is working"><span /><span /><span /></span>
        )}
        {m.text && (
          <div className={`cp-bubble ${m.role}`}>
            {m.role === "assistant"
              ? <Markdown text={m.text} onNavigate={onNavigate} />
              : m.text}
            {m.streaming && m.text && <span className="cp-caret" aria-hidden />}
          </div>
        )}
        {m.role === "assistant" && m.error && (
          <ErrorCard info={m.error} busy={busy}
                     onRetry={m.retryText && onRetry ? () => onRetry(m.retryText!) : undefined}
                     onUpgrade={onUpgrade} />
        )}
        {m.role === "assistant" && m.approval && (
          <ApprovalCard a={m.approval} busy={busy}
                        onDecide={(ok) => onDecide(m.id, ok)} />
        )}
      </div>
    </div>
  );
}

/** The scrolling transcript — a list of turns with an auto-scroll anchor. */
export function ChatThread({ messages, busy, onRetry, onUpgrade, onDecide, onNavigate }: {
  messages: ChatMsg[]; busy: boolean;
  onRetry?: (text: string) => void;
  onUpgrade?: () => void;
  onDecide: (fromId: string, approved: boolean) => void;
  onNavigate?: () => void;
}) {
  return (
    <>
      {messages.map((m) => (
        <ChatTurn key={m.id} m={m} busy={busy} onRetry={onRetry} onUpgrade={onUpgrade}
                  onDecide={onDecide} onNavigate={onNavigate} />
      ))}
    </>
  );
}

/** A styled, honest error state: an icon, a per-case title, the server's safe
 *  message, and the right affordance (retry the turn, or upgrade). */
export function ErrorCard({ info, busy, onRetry, onUpgrade }: {
  info: CopilotErrorInfo; busy: boolean;
  onRetry?: () => void; onUpgrade?: () => void;
}) {
  const ui = ERROR_UI[info.code] ?? ERROR_UI.generic;
  const action = info.action ?? "retry";
  return (
    <div className={`cp-error ${ui.tone}`} role="alert">
      <span className="cp-error-ic" aria-hidden>{ui.icon}</span>
      <div className="cp-error-body">
        <p className="cp-error-title">{ui.title}</p>
        <p className="cp-error-msg">{info.message}</p>
        {action === "upgrade" && onUpgrade ? (
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
export function ApprovalCard({ a, busy, onDecide }: {
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
