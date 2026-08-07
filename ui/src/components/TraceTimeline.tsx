import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * A run as a step-by-step timeline — what the model decided, which tool it
 * reached for, what came back, and what the harness did about it.
 *
 * The console could already tell you a run FAILED and which assertions broke.
 * It could not show you the sequence that produced it without reading raw JSON,
 * which is the one view an engineer can actually argue with.
 *
 * Three things this refuses to smooth over, because each is a different fact
 * and they look identical once flattened:
 *
 *   * a tool call the gateway BLOCKED is not a tool call that failed, and
 *     neither is a call that errored — `enforcement` and `error` are separate;
 *   * a span with no output is not a span that returned nothing;
 *   * elapsed time is shown as an offset from the FIRST span, not wall-clock,
 *     because the absolute timestamps are a deterministic tick in a scripted
 *     session (scenario/env.py EPOCH + n) and would read as a real clock.
 */

/** One span of a stored trace, exactly as `/api/traces/:id` returns it.
 *
 *  Every field is optional because a real trace is not obliged to carry them:
 *  a black-box adapter records a `kind` and nothing else, and a span that the
 *  harness synthesised has no timing at all. A required field here would be a
 *  claim about the wire format that the wire format does not make, and the type
 *  would be enforcing it in the one place that cannot check — the browser. */
export interface TimelineSpan {
  span_id?: string;
  parent_id?: string | null;
  kind?: string;
  name?: string;
  start_time?: string;
  end_time?: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | null;
  attributes?: Record<string, unknown>;
}

/** Display name per span kind. `user_turn` is the counterparty, not the agent. */
const KIND_LABEL: Record<string, string> = {
  user_turn: "Customer turn",
  llm_call: "Model reasoning",
  tool_call: "Tool call",
  retrieval: "Retrieval",
  agent_decision: "Decision",
  error: "Error",
  final_output: "Final answer",
};

/** Milliseconds, or NaN when the span carries no usable start. Kept in one
 *  place so the sort and the label agree about what "untimed" means. */
function startedAt(s: TimelineSpan): number {
  return s.start_time ? new Date(s.start_time).getTime() : NaN;
}

function offset(t: number, base: number): string {
  const ms = t - base;
  if (!Number.isFinite(ms)) return "";
  return `+${(ms / 1000).toFixed(2)}s`;
}

function preview(v: unknown, max = 400): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v.slice(0, max);
  try {
    const s = JSON.stringify(v);
    return s === "{}" ? "" : s.slice(0, max);
  } catch { return String(v).slice(0, max); }
}

export function TraceTimeline({ spans }: { spans: TimelineSpan[] }) {
  if (!spans.length) return null;
  // Untimed spans keep the order the trace stored them in rather than sorting
  // to the front as epoch zero, which would put them BEFORE the first real
  // step and read as the thing that happened first.
  const ordered = [...spans]
    .map((s, i) => ({ s, i, t: startedAt(s) }))
    .sort((a, b) => (Number.isFinite(a.t) && Number.isFinite(b.t)
      ? a.t - b.t : a.i - b.i))
    .map((e) => e.s);
  const base = ordered.map(startedAt).find(Number.isFinite) ?? NaN;

  return (
    <ol className="ttl" aria-label="Trace timeline">
      {ordered.map((s, i) => {
        const enforcement = String(s.attributes?.enforcement ?? "");
        const blocked = enforcement === "blocked";
        const inp = preview(s.input);
        const out = preview(s.output);
        return (
          <li className={`ttl__row ttl__row--${s.kind ?? "unknown"}`}
              key={s.span_id ?? i}>
            <div className="ttl__t">{offset(startedAt(s), base)}</div>
            <div className="ttl__body">
              <div className="ttl__k">
                {(s.kind && KIND_LABEL[s.kind]) ?? s.kind ?? "step"}
                {s.name ? <span className="ttl__name"> · {s.name}</span> : null}
                {/* Three distinct outcomes, never collapsed into "failed". */}
                {blocked && <span className="ttl__tag ttl__tag--blocked">blocked by the gateway</span>}
                {s.error && <span className="ttl__tag ttl__tag--error">errored</span>}
              </div>
              {inp && <pre className="ttl__io">{inp}</pre>}
              {out ? (
                <pre className="ttl__io ttl__io--out">→ {out}</pre>
              ) : (
                <div className="ttl__none">no output recorded</div>
              )}
              {s.error && <div className="ttl__err">{s.error}</div>}
              {(s.tokens_in || s.tokens_out || s.cost_usd) && (
                <div className="ttl__meta">
                  {s.tokens_in ? `${s.tokens_in} in` : ""}
                  {s.tokens_out ? ` · ${s.tokens_out} out` : ""}
                  {s.cost_usd ? ` · $${s.cost_usd.toFixed(4)}` : ""}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Self-fetching form. Renders NOTHING when the trace has no spans — a
 *  black-box run legitimately has none, and an empty timeline would read as a
 *  run that did nothing rather than one we cannot see inside. */
export function TraceTimelineFor({ traceId }: { traceId: string }) {
  const [spans, setSpans] = useState<TimelineSpan[] | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  useEffect(() => {
    if (!traceId) return;
    let live = true;
    setState("loading");
    api.getTrace(traceId)
      .then((t: { spans?: TimelineSpan[]; visibility?: string }) => {
        if (!live) return;
        setSpans(t?.spans ?? []);
        setState("ok");
      })
      .catch(() => { if (live) setState("error"); });
    return () => { live = false; };
  }, [traceId]);

  if (state === "error") return null;
  if (state === "loading") return <div className="ttl__load">loading the trace…</div>;
  if (!spans || !spans.length) {
    return (
      <div className="ttl__none">
        No spans on this trace — a black-box run records an answer, not the steps
        that produced it.
      </div>
    );
  }
  return <TraceTimeline spans={spans} />;
}

export default TraceTimeline;
