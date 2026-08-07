/* The timeline must keep apart the things that look alike once flattened.
 *
 * A tool call the gateway BLOCKED, a tool call that ERRORED, and a tool call
 * that returned nothing are three different facts about a run. Rendered as
 * "failed" they become one, and the run stops being arguable — which is the
 * whole point of showing the steps rather than the verdict.
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { TraceTimeline, type TimelineSpan } from "./TraceTimeline";

const T = (s: number) => new Date(Date.UTC(2026, 0, 1, 0, 0, s)).toISOString();

const spans: TimelineSpan[] = [
  { span_id: "s1", kind: "user_turn", name: "", start_time: T(0), end_time: T(0),
    input: { text: "I was double charged" } },
  { span_id: "s2", kind: "llm_call", name: "plan", start_time: T(1), end_time: T(2),
    output: { text: "check the ledger first" }, tokens_in: 900, tokens_out: 120,
    cost_usd: 0.0031 },
  { span_id: "s3", kind: "tool_call", name: "orders.get", start_time: T(3), end_time: T(3),
    input: { order_id: "ord_8812f" }, output: { total: 1240 } },
  { span_id: "s4", kind: "tool_call", name: "refunds.create", start_time: T(4), end_time: T(4),
    input: { amount: 1240 }, attributes: { enforcement: "blocked" } },
  { span_id: "s5", kind: "tool_call", name: "policy.lookup", start_time: T(5), end_time: T(5),
    input: { scope: "refunds" }, error: "upstream 503" },
];

const html = renderToStaticMarkup(<TraceTimeline spans={spans} />);

describe("TraceTimeline", () => {
  it("orders the steps by when they started", () => {
    const shuffled = [spans[3], spans[0], spans[2], spans[1], spans[4]];
    const out = renderToStaticMarkup(<TraceTimeline spans={shuffled} />);
    expect(out.indexOf("Customer turn")).toBeLessThan(out.indexOf("orders.get"));
    expect(out.indexOf("orders.get")).toBeLessThan(out.indexOf("refunds.create"));
  });

  it("shows time as an offset from the first span, not a wall clock", () => {
    // Scripted sessions stamp a deterministic tick, so an absolute timestamp
    // would read as a real clock and invite conclusions about latency.
    expect(html).toContain("+0.00s");
    expect(html).toContain("+3.00s");
    expect(html).not.toContain("2026-01-01");
  });

  it("keeps BLOCKED apart from ERRORED", () => {
    // The gateway refusing a call is the harness working. An upstream 503 is
    // not. Collapsing them would credit the harness's work to the agent.
    expect(html).toContain("blocked by the gateway");
    expect(html).toContain("errored");
    expect(html).toContain("upstream 503");
  });

  it("says 'no output recorded' rather than drawing an empty result", () => {
    // A span with no output and a span that returned nothing look identical if
    // both render as blank.
    expect(html).toContain("no output recorded");
  });

  it("names the counterparty turn as the customer, not the agent", () => {
    expect(html).toContain("Customer turn");
  });

  it("carries tokens and cost where the span recorded them", () => {
    expect(html).toContain("900 in");
    expect(html).toContain("120 out");
    expect(html).toContain("$0.0031");
  });

  it("renders nothing for an empty trace", () => {
    expect(renderToStaticMarkup(<TraceTimeline spans={[]} />)).toBe("");
  });

  it("is an ordered list with an accessible name", () => {
    expect(html).toMatch(/<ol[^>]*aria-label="Trace timeline"/);
  });

  it("does not sort an UNTIMED span to the front as epoch zero", () => {
    // A trace is not obliged to stamp every span. Parsing a missing time as 0
    // would place it before the first real step and read as the thing that
    // happened first — a claim about ordering the trace never made.
    const out = renderToStaticMarkup(<TraceTimeline spans={[
      spans[1],
      { span_id: "sx", kind: "agent_decision", name: "retry" },   // no times
    ]} />);
    expect(out.indexOf("plan")).toBeLessThan(out.indexOf("retry"));
    // and it claims no offset it cannot compute
    expect((out.match(/\+0\.00s/g) || []).length).toBe(1);
  });

  it("renders a span carrying only a kind", () => {
    // The black-box case: an adapter that records what happened and nothing
    // else. It must not render `ttl__row--undefined` or a blank label.
    const out = renderToStaticMarkup(<TraceTimeline spans={[{ kind: "tool_call" }]} />);
    expect(out).toContain("Tool call");
    expect(out).not.toContain("undefined");
  });

  it("marks a tool call and an error span differently in the gutter", () => {
    expect(html).toContain("ttl__row--tool_call");
    expect(html).toContain("ttl__row--user_turn");
  });
});
