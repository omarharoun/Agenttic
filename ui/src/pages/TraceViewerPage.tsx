import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type Trace, type Span, type SpanKind } from "../api";
import { EmptyState, PageHeader } from "../components/ui";
import { PageData } from "../components/PageData";
import {
  IconTraces, IconCopy, IconCheck, IconChevronRight, IconInfo,
} from "../icons";
import "./TraceViewer.css";

/* SPEC-4 Step 19.4 — the trace viewer.

   A trace is a timeline. Every span is a row on ONE shared time axis
   (start→end relative to the trace's window), nested under its parent, and
   coloured by kind so the reader sees where the wall-clock time — and the
   money — went. Any span expands to a pretty JSON view of its input/output
   with a copy button; LLM spans carry their token/cost annotations.

   Honesty about visibility is the whole point: a black-box trace has no
   internals to draw. Rather than render a broken/empty tree, it shows the one
   observed output as a single row plus a tier note that says so plainly. */

/* Kind → colour token + human label. Every colour is a theme.css variable. */
const KIND_COLOR: Record<SpanKind, string> = {
  llm_call: "var(--accent)",
  tool_call: "var(--info)",
  retrieval: "var(--cat-input, var(--info))",
  agent_decision: "var(--ok)",
  error: "var(--fail)",
  final_output: "var(--wait)",
  escalation: "var(--wait)",
};
const KIND_LABEL: Record<SpanKind, string> = {
  llm_call: "llm",
  tool_call: "tool",
  retrieval: "retrieval",
  agent_decision: "decision",
  error: "error",
  final_output: "output",
  escalation: "escalation",
};

/** ms since epoch for an ISO timestamp; NaN-safe (falls back to 0). */
function ms(iso: string): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
}

/** Human duration: sub-second in ms, else seconds. */
function fmtDuration(msVal: number): string {
  if (msVal < 1000) return `${Math.round(msVal)} ms`;
  return `${(msVal / 1000).toFixed(2)} s`;
}

/** A span placed in the tree: its depth, and its bar position (0–1) within the
 *  trace window. */
interface PlacedSpan {
  span: Span;
  depth: number;
  left: number; // 0–1
  width: number; // 0–1
  durationMs: number;
}

/** Order spans as a depth-first tree (parent then children), computing each
 *  span's bar geometry against the shared [t0, t1] window. Orphans (a
 *  parent_id we never saw) are treated as roots so nothing is dropped. */
function placeSpans(spans: Span[]): { rows: PlacedSpan[]; t0: number; span: number } {
  if (spans.length === 0) return { rows: [], t0: 0, span: 0 };

  const byId = new Map(spans.map((s) => [s.span_id, s]));
  const childrenOf = new Map<string | null, Span[]>();
  for (const s of spans) {
    const key = s.parent_id && byId.has(s.parent_id) ? s.parent_id : null;
    const list = childrenOf.get(key) ?? [];
    list.push(s);
    childrenOf.set(key, list);
  }
  // stable start-time order within each sibling group
  for (const list of childrenOf.values()) {
    list.sort((a, b) => ms(a.start_time) - ms(b.start_time));
  }

  const starts = spans.map((s) => ms(s.start_time));
  const ends = spans.map((s) => ms(s.end_time));
  const t0 = Math.min(...starts);
  const t1 = Math.max(...ends, t0 + 1);
  const total = Math.max(1, t1 - t0);

  const rows: PlacedSpan[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const s of childrenOf.get(parent) ?? []) {
      const start = ms(s.start_time);
      const end = Math.max(start, ms(s.end_time));
      rows.push({
        span: s,
        depth,
        left: (start - t0) / total,
        width: Math.max((end - start) / total, 0),
        durationMs: end - start,
      });
      walk(s.span_id, depth + 1);
    }
  };
  walk(null, 0);
  return { rows, t0, span: total };
}

/** A small copy-to-clipboard button that flips to a check for a beat. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    navigator.clipboard?.writeText(text).then(
      () => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); },
      () => { /* clipboard blocked — the JSON stays selectable as a fallback */ },
    );
  }, [text]);
  return (
    <button type="button" className="trace-copy-btn" onClick={onCopy}
      aria-label={copied ? `${label} copied` : `Copy ${label}`}>
      {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

/** A pretty, monospace JSON block with its own copy button. */
function JsonBlock({ title, value }: { title: string; value: unknown }) {
  const pretty = useMemo(() => JSON.stringify(value, null, 2), [value]);
  return (
    <div className="trace-json-block">
      <div className="trace-json-head">
        <span className="trace-json-title">{title}</span>
        <CopyButton text={pretty} label={title} />
      </div>
      <pre className="trace-json">{pretty}</pre>
    </div>
  );
}

/** The expanded detail for one span: LLM cost/token annotations, the error (if
 *  any), then input + output as JSON. */
function SpanDetail({ span }: { span: Span }) {
  const hasTokens = span.tokens_in != null || span.tokens_out != null;
  const hasCost = span.cost_usd != null;
  return (
    <div className="trace-detail">
      <div className="trace-detail-meta">
        {hasCost && (
          <span className="trace-anno">cost <b>${(span.cost_usd ?? 0).toFixed(4)}</b></span>
        )}
        {hasTokens && (
          <span className="trace-anno">
            tokens <b>{span.tokens_in ?? 0}</b> in · <b>{span.tokens_out ?? 0}</b> out
          </span>
        )}
        {span.error && (
          <span className="trace-anno is-error-anno">error: <b>{span.error}</b></span>
        )}
      </div>
      <JsonBlock title="input" value={span.input} />
      <JsonBlock title="output" value={span.output} />
    </div>
  );
}

/** One span row: a keyboard-expandable header (label + kind-coloured timing
 *  bar) with its collapsible detail beneath. */
function SpanRow({ placed, expanded, onToggle }: {
  placed: PlacedSpan;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { span, depth, left, width, durationMs } = placed;
  const isError = span.kind === "error" || span.error != null;
  const color = KIND_COLOR[span.kind] ?? "var(--border-strong)";
  const detailId = `trace-detail-${span.span_id}`;
  const indent = depth * 16;

  return (
    <div className="trace-row-shell" role="treeitem" aria-expanded={expanded}
      aria-level={depth + 1}>
      <button type="button"
        className={`trace-row${isError ? " is-error" : ""}`}
        aria-expanded={expanded}
        aria-controls={detailId}
        onClick={onToggle}>
        <span className="trace-row-label" style={{ paddingLeft: indent }}>
          <span className="trace-caret" aria-hidden="true"><IconChevronRight size={14} /></span>
          <span className="trace-kind-dot" style={{ ["--k-color" as string]: color }} />
          <span className="trace-row-name" title={span.name}>{span.name}</span>
          <span className="trace-row-kind">{KIND_LABEL[span.kind] ?? span.kind}</span>
        </span>
        <span className="trace-bar-track">
          <span
            className={`trace-bar${isError ? " is-error" : ""}`}
            style={{
              left: `${left * 100}%`,
              width: `${Math.max(width * 100, 1.5)}%`,
              ["--k-color" as string]: color,
            }} />
          <span className="trace-bar-dur" style={{ left: `${Math.min(left * 100 + 1, 82)}%` }}>
            {fmtDuration(durationMs)}
          </span>
        </span>
      </button>
      {expanded && (
        <div id={detailId} role="group">
          <SpanDetail span={span} />
        </div>
      )}
    </div>
  );
}

/** The full glass-box timeline: an axis header + one expandable row per span. */
function Timeline({ trace }: { trace: Trace }) {
  const { rows, span: windowMs } = useMemo(() => placeSpans(trace.spans), [trace.spans]);
  // errors expanded by default so failures aren't hidden; others collapsed.
  const [open, setOpen] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    for (const r of rows) if (r.span.kind === "error" || r.span.error != null) init[r.span.span_id] = true;
    return init;
  });
  const toggle = useCallback(
    (id: string) => setOpen((o) => ({ ...o, [id]: !o[id] })), []);

  return (
    <div className="trace-timeline" role="tree" aria-label="Trace span timeline">
      <div className="trace-axis" aria-hidden="true">
        <span>0 ms</span>
        <span>{fmtDuration(windowMs)}</span>
      </div>
      {rows.map((r) => (
        <SpanRow key={r.span.span_id} placed={r}
          expanded={!!open[r.span.span_id]}
          onToggle={() => toggle(r.span.span_id)} />
      ))}
    </div>
  );
}

/** The honest black-box rendering: no internal spans, so we show the single
 *  observed final output as one row plus a tier note — not a broken tree.
 *  If the black-box trace happens to carry a lone span we render that instead. */
function BlackBoxView({ trace }: { trace: Trace }) {
  const lone = trace.spans.length === 1 ? trace.spans[0] : null;
  return (
    <>
      <div className="trace-tier-note" role="note">
        <span className="ttn-ico"><IconInfo size={16} /></span>
        <span>
          Black-box — a single observed output, no internal spans. This agent is
          measured end-to-end; its intermediate reasoning, tool calls, and costs
          are not visible to the harness.
        </span>
      </div>
      {lone ? (
        <div className="trace-timeline" role="tree" aria-label="Trace span timeline">
          <SpanRow placed={{ span: lone, depth: 0, left: 0, width: 1, durationMs: Math.max(0, ms(lone.end_time) - ms(lone.start_time)) }}
            expanded onToggle={() => { /* single row — always open */ }} />
        </div>
      ) : (
        <div className="trace-timeline">
          <div className="trace-detail" style={{ paddingLeft: 12 }}>
            <JsonBlock title="final output" value={trace.final_output} />
          </div>
        </div>
      )}
    </>
  );
}

/** A timeline-shaped loading skeleton (matches the row grid). */
function TimelineSkeleton() {
  return (
    <div className="trace-skel" aria-busy="true" aria-label="Loading trace">
      {Array.from({ length: 6 }).map((_, i) => (
        <div className="trace-skel-row" key={i}>
          <div className="trace-skel-bar" style={{ width: `${55 + (i * 11) % 40}%` }} />
          <div className="trace-skel-bar" style={{ width: `${30 + (i * 17) % 55}%`, marginLeft: `${(i * 13) % 40}%` }} />
        </div>
      ))}
    </div>
  );
}

export function TraceViewerPage() {
  const { id = "" } = useParams();
  const [trace, setTrace] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);

  const load = useCallback(() => {
    if (!id) { setLoading(false); return; }
    setLoading(true);
    setErr(null);
    api.getTrace(id)
      .then((t) => setTrace(t))
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, [id]);
  useEffect(() => load(), [load]);

  const isBlackBox = trace?.visibility === "black_box";
  const empty = trace != null && trace.spans.length === 0 && !isBlackBox
    && !trace.final_output;

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Trace"
          subtitle="One agent run as a timeline: every span on a shared clock, nested by parent and coloured by kind, so you can see where the time and the cost went. Expand any span for its input and output; a black-box trace shows only what was observed."
          actions={
            <span className="mono muted-sm" title="Trace id">{id || "—"}</span>
          } />

        {trace && (
          <div className="trace-totals" aria-label="Trace totals">
            <div className="trace-total">
              <span className="tt-val mono">{trace.agent_id}</span>
              <span className="tt-lbl">agent</span>
            </div>
            <div className="trace-total">
              <span className="tt-val">{trace.visibility === "black_box" ? "black-box" : "glass-box"}</span>
              <span className="tt-lbl">visibility tier</span>
            </div>
            <div className="trace-total">
              <span className="tt-val">${trace.total_cost_usd.toFixed(4)}</span>
              <span className="tt-lbl">total cost (USD)</span>
            </div>
            <div className="trace-total">
              <span className="tt-val">{fmtDuration(trace.total_latency_ms)}</span>
              <span className="tt-lbl">total latency</span>
            </div>
            <div className="trace-total">
              <span className="tt-val">{trace.total_steps}</span>
              <span className="tt-lbl">steps</span>
            </div>
            {trace.escalated && (
              <div className="trace-total">
                <span className="tt-val" style={{ color: "var(--wait)" }}>escalated</span>
                <span className="tt-lbl">human-in-the-loop</span>
              </div>
            )}
          </div>
        )}

        <PageData
          loading={loading}
          error={err}
          empty={empty}
          onRetry={load}
          errorTitle="Couldn't load this trace"
          skeleton={<TimelineSkeleton />}
          emptyState={
            <EmptyState icon={<IconTraces />} title="No spans recorded for this trace"
              hint="This trace has no observed output and no spans to show. It may still be running, or nothing was captured." />
          }
        >
          {trace && (
            isBlackBox
              ? <BlackBoxView trace={trace} />
              : <Timeline trace={trace} />
          )}
        </PageData>
      </div>
    </div>
  );
}
