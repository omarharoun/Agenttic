/* ============================================================================
   Safe Assistant — shared types, safety-posture model, and pure helpers.

   The consumer-facing personal assistant is agenttic's flagship surface: a
   friendly chat that makes its *safety* visible — it shows what it did, it asks
   before doing anything sensitive, and it can't touch your files or secrets.

   This module is deliberately pure (no React, no DOM-at-import) so the
   normalisation, labelling, and the offline-preview "engine" are unit-testable.
   The network calls live alongside the rest in api.ts.
   ========================================================================== */

/** A transparency step the assistant surfaces as it works ("looked up X"). */
export interface AssistantStep {
  id: string;
  kind: "thought" | "tool_use" | "tool_result" | "note";
  tool?: string;                 // machine name, e.g. "web.fetch"
  summary: string;               // plain-language, e.g. "Looked up the weather"
  detail?: string;               // optional extra context (a URL, a result)
  status?: "running" | "done" | "error";
}

/** A sensitive action the assistant is asking permission to take. The
 *  trust-defining interaction — the turn blocks until the user decides. */
export interface PendingApproval {
  action_id: string;
  tool: string;                  // "web.fetch"
  title: string;                 // "The assistant wants to fetch example.com"
  description?: string;          // why it wants to, in plain language
  target?: string;               // the concrete object (a URL, a host)
  risk?: "low" | "medium" | "high";
}

export type ApprovalDecision = "allow" | "deny";

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  steps?: AssistantStep[];       // tool-use transparency on assistant turns
  pending?: PendingApproval | null;
  status?: "streaming" | "complete" | "error";
}

/** What the assistant can do and what it can't — rendered as the trust panel. */
export interface AssistantTool {
  name: string;
  label: string;
  sensitive?: boolean;           // sensitive tools require per-use approval
}

export interface SafetyPosture {
  sandboxed: boolean;
  approval_required: boolean;
  file_access: boolean;
  credential_access: boolean;
  tools: AssistantTool[];
  grade?: string;                // a real, issued safety grade — ONLY set when the
                                 // backend reports a verified certificate. Never a
                                 // placeholder: absent => "certification pending".
  cert_id?: string | null;       // public certificate id (badge links here)
  note?: string;
}

export type SessionStatus =
  | "idle" | "thinking" | "awaiting_approval" | "error";

export interface AssistantSession {
  session_id: string;
  status: SessionStatus;
  messages: AssistantMessage[];
  posture: SafetyPosture;
  pending: PendingApproval | null;
}

/* ----------------------------- safe defaults ----------------------------- */

/** The assistant's safety posture — also the graceful-degradation default used
 *  when the backend hasn't reported one yet. Safe-by-default: sandboxed, no
 *  file/credential access, sensitive actions gated. */
export const DEFAULT_POSTURE: SafetyPosture = {
  sandboxed: true,
  approval_required: true,
  file_access: false,
  credential_access: false,
  // No placeholder grade: the assistant has not been issued a verified safety
  // grade, so we show "certification pending" rather than a fabricated letter.
  grade: undefined,
  cert_id: null,
  tools: [
    { name: "web.search", label: "Search the web", sensitive: false },
    { name: "web.fetch", label: "Open a web page", sensitive: true },
    { name: "calculator", label: "Do math", sensitive: false },
    { name: "datetime", label: "Check the date & time", sensitive: false },
  ],
  note:
    "Runs in a sandbox with no access to your files, device, or saved credentials. " +
    "Anything that reaches outside — like opening a web page — pauses for your OK.",
};

/** A few starter prompts for the empty state, in plain end-user voice. */
export const EXAMPLE_PROMPTS: { label: string; text: string }[] = [
  { label: "Look something up", text: "Look up today's top technology headline and summarize it for me." },
  { label: "Do some math", text: "If I save $45 a week, how much will I have in a year?" },
  { label: "Plan my day", text: "Help me plan a focused 3-hour study block this afternoon." },
  { label: "Explain simply", text: "Explain what a prompt injection attack is, like I'm new to this." },
];

/* ------------------------------- labelling ------------------------------- */

const TOOL_LABELS: Record<string, string> = {
  "web.fetch": "Open a web page",
  "web.search": "Search the web",
  "http.get": "Open a web page",
  calculator: "Do math",
  calc: "Do math",
  datetime: "Check the date & time",
  clock: "Check the date & time",
  memory: "Recall earlier context",
};

/** Friendly label for a tool name; humanizes unknown names as a fallback. */
export function toolLabel(tool: string | undefined | null): string {
  if (!tool) return "a tool";
  return (
    TOOL_LABELS[tool] ??
    tool.replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/** Semantic tone token for an approval's risk level (matches Noor --ok/wait/fail). */
export function riskTone(risk: PendingApproval["risk"]): "ok" | "wait" | "fail" {
  switch (risk) {
    case "high": return "fail";
    case "medium": return "wait";
    default: return "ok";          // low / undefined
  }
}

/** One-line posture summary — the visible differentiator line under the title. */
export function postureLine(p: SafetyPosture): string {
  const parts: string[] = [];
  if (p.sandboxed) parts.push("Sandboxed");
  if (!p.file_access && !p.credential_access) {
    parts.push("no file or credential access");
  } else if (!p.file_access) {
    parts.push("no file access");
  } else if (!p.credential_access) {
    parts.push("no credential access");
  }
  if (p.approval_required) parts.push("sensitive actions need your approval");
  return parts.join(" · ");
}

/* ----------------------------- normalisation ----------------------------- */

let _id = 0;
/** Stable-ish id for client-minted messages/steps (no Date.now/Math.random —
 *  those are unavailable in some test/sandbox contexts and would be flaky). */
export function localId(prefix = "m"): string {
  _id += 1;
  return `${prefix}_${_id}`;
}

function asArray<T>(v: any): T[] {
  return Array.isArray(v) ? v : [];
}

/** Normalise a raw approval payload (several plausible backend shapes). */
export function normalizeApproval(raw: any): PendingApproval | null {
  if (!raw) return null;
  const tool = raw.tool ?? raw.tool_name ?? raw.action ?? "";
  const target = raw.target ?? raw.url ?? raw.host ?? raw.argument ?? "";
  const title =
    raw.title ??
    (target
      ? `The assistant wants to ${toolLabel(tool).toLowerCase()}: ${target}`
      : `The assistant wants to ${toolLabel(tool).toLowerCase()}`);
  return {
    action_id: String(raw.action_id ?? raw.id ?? localId("act")),
    tool: String(tool),
    title,
    description: raw.description ?? raw.reason ?? raw.why ?? undefined,
    target: target || undefined,
    risk: raw.risk ?? (tool.includes("fetch") || tool.includes("http") ? "medium" : "low"),
  };
}

function normalizeStep(raw: any): AssistantStep {
  const kind: AssistantStep["kind"] =
    raw.kind ?? (raw.tool || raw.tool_name ? "tool_use" : "note");
  const tool = raw.tool ?? raw.tool_name ?? undefined;
  return {
    id: String(raw.id ?? localId("s")),
    kind,
    tool,
    summary:
      raw.summary ??
      raw.label ??
      raw.text ??
      (tool ? toolLabel(tool) : "Working…"),
    detail: raw.detail ?? raw.result ?? raw.url ?? undefined,
    status: raw.status ?? "done",
  };
}

function normalizeMessage(raw: any): AssistantMessage {
  return {
    id: String(raw.id ?? localId("m")),
    role: raw.role === "user" ? "user" : "assistant",
    text: String(raw.text ?? raw.content ?? raw.answer ?? ""),
    steps: asArray(raw.steps).map(normalizeStep),
    pending: normalizeApproval(raw.pending ?? raw.pending_approval),
    status: raw.status ?? "complete",
  };
}

/** Merge a raw posture onto the safe defaults so missing fields degrade safely. */
export function normalizePosture(raw: any): SafetyPosture {
  if (!raw) return DEFAULT_POSTURE;
  const tools = asArray<any>(raw.tools).map((t) =>
    typeof t === "string"
      ? { name: t, label: toolLabel(t) }
      : { name: t.name ?? "", label: t.label ?? toolLabel(t.name), sensitive: !!t.sensitive },
  );
  return {
    sandboxed: raw.sandboxed ?? DEFAULT_POSTURE.sandboxed,
    approval_required: raw.approval_required ?? DEFAULT_POSTURE.approval_required,
    file_access: raw.file_access ?? DEFAULT_POSTURE.file_access,
    credential_access: raw.credential_access ?? DEFAULT_POSTURE.credential_access,
    tools: tools.length ? tools : DEFAULT_POSTURE.tools,
    grade: raw.grade ?? DEFAULT_POSTURE.grade,
    cert_id: raw.cert_id ?? raw.certificate_id ?? null,
    note: raw.note ?? DEFAULT_POSTURE.note,
  };
}

/** Normalise a raw GET /sessions/{id} (or message) response into a session.
 *  Lenient about field names so it survives a still-evolving backend. */
export function normalizeSession(raw: any, fallbackId = ""): AssistantSession {
  const messages = asArray<any>(raw?.messages ?? raw?.history).map(normalizeMessage);
  const pending = normalizeApproval(raw?.pending ?? raw?.pending_approval);
  let status: SessionStatus = raw?.status ?? "idle";
  if (pending && status !== "awaiting_approval") status = "awaiting_approval";
  return {
    session_id: String(raw?.session_id ?? raw?.id ?? fallbackId),
    status,
    messages,
    posture: normalizePosture(raw?.posture ?? raw?.safety),
    pending,
  };
}

/* --------------------------- offline preview ----------------------------- */
/* When the assistant backend isn't reachable (e.g. this UI branch deployed
   ahead of the service), the chat still works as a clearly-labelled local
   preview. The preview is deterministic and *honest* — it never fabricates a
   safety result — and exists so the approval interaction stays demonstrable.   */

export interface PreviewTurn {
  /** Steps revealed before any approval / answer. */
  steps: AssistantStep[];
  /** If set, the turn pauses here for the user's decision. */
  pending: PendingApproval | null;
  /** Steps revealed after the user *allows* the pending action. */
  afterAllow: AssistantStep[];
  /** Final answer when allowed (or when no approval was needed). */
  answer: string;
  /** Final answer when the user *denies* the pending action. */
  denyAnswer: string;
}

/** Build a deterministic preview turn for a user message. Recognises a couple
 *  of intents to demonstrate (a) transparent non-sensitive tool use and (b) the
 *  human-in-the-loop approval gate for a sensitive action. */
export function buildPreviewTurn(text: string): PreviewTurn {
  const t = text.toLowerCase();
  const wantsWeb =
    /\b(look up|search|fetch|open|headline|news|website|url|http|weather|latest)\b/.test(t);
  const wantsMath =
    /\$\s*\d|\d\s*[-+*/x]\s*\d|\b(calculate|how much|average|sum of|save \$?\d)\b/.test(t);

  if (wantsWeb) {
    return {
      steps: [
        { id: localId("s"), kind: "thought", summary: "Worked out that this needs a live web page" },
      ],
      pending: {
        action_id: localId("act"),
        tool: "web.fetch",
        title: "The assistant wants to open a web page",
        description:
          "To answer this it needs to read a page on the open web. It can't do that without your OK.",
        target: "https://news.example.com/top",
        risk: "medium",
      },
      afterAllow: [
        { id: localId("s"), kind: "tool_use", tool: "web.fetch", summary: "Opened news.example.com", detail: "https://news.example.com/top", status: "done" },
        { id: localId("s"), kind: "tool_result", summary: "Read the page and pulled out the headline", status: "done" },
      ],
      answer:
        "Here's what I found (preview): the top item was a piece on AI safety tooling. " +
        "I only read the page you approved — nothing else.\n\n" +
        "This is a local preview, so the result is illustrative — but notice I asked before reaching the web.",
      denyAnswer:
        "No problem — I won't open that page. I've stopped here and haven't touched the web.\n\n" +
        "Want me to try a different approach, or answer from what I already know?",
    };
  }

  if (wantsMath) {
    return {
      steps: [
        { id: localId("s"), kind: "tool_use", tool: "calculator", summary: "Did the math", detail: "no network, no approval needed", status: "done" },
      ],
      pending: null,
      afterAllow: [],
      answer:
        "Saving $45 a week for 52 weeks comes to $2,340 (preview math). " +
        "Math runs locally in the sandbox, so I didn't need to ask permission for it.",
      denyAnswer: "",
    };
  }

  return {
    steps: [
      { id: localId("s"), kind: "thought", summary: "Answered from what I already know — no tools needed" },
    ],
    pending: null,
    afterAllow: [],
    answer:
      "Happy to help! This is a local preview of the Safe Assistant, so I'm answering " +
      "from general knowledge.\n\nThe real assistant runs in a sandbox, shows every tool it " +
      "uses, and asks before doing anything sensitive — try asking me to look something up on " +
      "the web to see the approval step.",
    denyAnswer: "",
  };
}
