/* The guided run narrative.
 *
 * A run used to report itself in one monospace log at the bottom of the page,
 * in machine shorthand. These pin the replacement: every event becomes a plain
 * card filed under the step that produced it, so generation reads under
 * "Generate tests" and each case under "Run the tests".
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { applyEvent, emptyExec, type LogEntry, type SSEEvent } from "./store";
import { MAX_CARDS, cardsForNode, describeEvent } from "./workflow/StepActivity";
import { STEPS, TEMPLATES } from "./workflow/templates";

const entry = (over: Partial<LogEntry>): LogEntry =>
  ({ seq: 1, type: "node_progress", nodeId: "run_suite", text: "", data: {}, ...over });

describe("the event payload survives into the log", () => {
  it("keeps the data a card needs, not just the sentence", () => {
    const evt: SSEEvent = {
      seq: 1, type: "node_progress", node_id: "run_suite",
      data: { event: "case_finished", index: 2, total: 12, ok: true, test_id: "t-003" },
    };
    const s = applyEvent(emptyExec(), evt);
    expect(s.log[0].data.test_id).toBe("t-003");
    expect(s.log[0].data.total).toBe(12);
  });
});

describe("the progress bar under concurrency", () => {
  /* Cases and generator tasks now run several at a time, so events arrive out
   * of order. Progress is counted, not read off the event's index — otherwise
   * case 9 landing first would show 90% and then jump backwards. */
  const prog = (data: Record<string, any>, seq = 1): SSEEvent =>
    ({ seq, type: "node_progress", node_id: "run_suite", data });

  it("counts completions instead of trusting the index", () => {
    let s = applyEvent(emptyExec(), prog(
      { event: "case_finished", index: 8, total: 10, ok: true, test_id: "t-9" }));
    expect(s.progress.run_suite).toEqual({ done: 1, total: 10 });
    s = applyEvent(s, prog(
      { event: "case_finished", index: 1, total: 10, ok: true, test_id: "t-2" }, 2));
    expect(s.progress.run_suite).toEqual({ done: 2, total: 10 });
  });

  it("never runs backwards, whatever order events land in", () => {
    let s = emptyExec();
    const order = [7, 2, 9, 0, 4];
    order.forEach((idx, n) => {
      s = applyEvent(s, prog(
        { event: "case_finished", index: idx, total: 10, ok: true, test_id: `t-${idx}` },
        n + 1));
      expect(s.progress.run_suite.done).toBe(n + 1);
    });
  });

  it("counts a resumed case — it is finished, it just cost nothing", () => {
    const s = applyEvent(emptyExec(), prog(
      { event: "case_resumed", index: 3, total: 4, test_id: "t-4" }));
    expect(s.progress.run_suite.done).toBe(1);
  });

  it("does not count a case merely starting", () => {
    const s = applyEvent(emptyExec(), prog(
      { event: "case_started", index: 0, total: 4, test_id: "t-1" }));
    expect(s.progress.run_suite).toEqual({ done: 0, total: 4 });
  });

  it("never exceeds the total", () => {
    let s = emptyExec();
    for (let i = 0; i < 8; i++) {
      s = applyEvent(s, prog(
        { event: "case_finished", index: i, total: 3, ok: true, test_id: `t-${i}` },
        i + 1));
    }
    expect(s.progress.run_suite.done).toBe(3);
  });

  it("counts a generator task once, not twice per task", () => {
    const gen = (event: string, seq: number): SSEEvent =>
      ({ seq, type: "node_progress", node_id: "generator",
         data: { event, index: 0, total: 3, task: "triage" } });
    let s = applyEvent(emptyExec(), gen("criteria_defined", 1));
    expect(s.progress.generator.done).toBe(0);   // half-done is not done
    s = applyEvent(s, gen("cases_generated", 2));
    expect(s.progress.generator.done).toBe(1);
  });
});

describe("events read as English", () => {
  it("numbers a case from one, the way a person counts", () => {
    const c = describeEvent(entry({
      data: { event: "case_finished", index: 0, total: 12, ok: true, test_id: "t-001" } }))!;
    expect(c.lead).toBe("Case 1 of 12");
    expect(c.tone).toBe("ok");
    expect(c.detail).toBe("t-001");
  });

  it("says what failed rather than printing FAILED", () => {
    const c = describeEvent(entry({
      data: { event: "case_finished", index: 3, total: 12, ok: false, test_id: "t-004" } }))!;
    expect(c.title).toBe("The agent errored");
    expect(c.tone).toBe("fail");
  });

  it("keeps 'not scored' distinct from 'failed' — they are not the same thing", () => {
    const c = describeEvent(entry({
      data: { event: "case_error", index: 1, total: 4, test_id: "t-002", error: "timeout" } }))!;
    expect(c.badge).toBe("Not scored");
    expect(c.tone).not.toBe("fail");
    expect(c.detail).toBe("timeout");
  });

  it("names a verdict as met / not met the requirement", () => {
    const pass = describeEvent(entry({
      data: { event: "case_scored", index: 0, total: 2, passed: true, test_id: "t-001" } }))!;
    const fail = describeEvent(entry({
      data: { event: "case_scored", index: 1, total: 2, passed: false, test_id: "t-002" } }))!;
    expect(pass.title).toBe("Met the requirement");
    expect(fail.title).toBe("Did not meet the requirement");
  });

  it("asks for approval in the second person", () => {
    const c = describeEvent(entry({
      type: "node_waiting", nodeId: "human_gate",
      data: { suite_id: "s-1", version: 2 } }))!;
    expect(c.title).toContain("approve");
    expect(c.detail).toBe("s-1 · version 2");
  });

  it("says a failed step let the run carry on, when it did", () => {
    const c = describeEvent(entry({
      type: "node_failed", data: { error: "boom", continued: true } }))!;
    expect(c.title).toContain("carried on");
    expect(c.detail).toBe("boom");
  });

  it("spends no card on a step merely starting — the border already says so", () => {
    expect(describeEvent(entry({ type: "node_started", data: {} }))).toBeNull();
  });
});

describe("cards are filed under the step that produced them", () => {
  const log: LogEntry[] = [
    entry({ seq: 1, nodeId: "generator", data: { message: "Wrote 12 test cases" } }),
    entry({ seq: 2, nodeId: "run_suite",
            data: { event: "case_finished", index: 0, total: 2, ok: true, test_id: "t-1" } }),
    entry({ seq: 3, nodeId: "run_suite",
            data: { event: "case_finished", index: 1, total: 2, ok: true, test_id: "t-2" } }),
  ];

  it("shows the generator's own events under the generator", () => {
    expect(cardsForNode(log, "generator").cards.map((c) => c.title))
      .toEqual(["Wrote 12 test cases"]);
  });

  it("shows a case under the step that ran it, not in a page-wide log", () => {
    expect(cardsForNode(log, "run_suite").cards).toHaveLength(2);
    expect(cardsForNode(log, "score").cards).toHaveLength(0);
  });

  it("states how many it dropped instead of truncating in silence", () => {
    const many = Array.from({ length: MAX_CARDS + 5 }, (_, i) => entry({
      seq: i, nodeId: "run_suite",
      data: { event: "case_finished", index: i, total: 60, ok: true, test_id: `t-${i}` } }));
    const { cards, hidden } = cardsForNode(many, "run_suite");
    expect(cards).toHaveLength(MAX_CARDS);
    expect(hidden).toBe(5);
  });
});

describe("the guided pipeline", () => {
  it("no longer ends a new evaluation with a live monitor step", () => {
    for (const t of TEMPLATES) expect(t.stepIds).not.toContain("monitor");
  });

  it("still knows what a monitor step is, so older workflows render", () => {
    expect(STEPS.some((s) => s.id === "monitor")).toBe(true);
  });
});

describe("the agent step", () => {
  it("renders no card grid until the run has something to say about it", () => {
    // cardsForNode is what the step renders from; empty means nothing is drawn
    expect(cardsForNode([], "agent").cards).toEqual([]);
  });
});

describe("a card renders", () => {
  it("puts the outcome word and the detail on the page", () => {
    const html = renderToStaticMarkup(
      <div className="sa-card ok">
        <div className="sa-top"><span className="sa-lead">Case 1 of 12</span>
          <span className="sa-badge">Answered</span></div>
        <div className="sa-title">The agent answered</div>
        <div className="sa-detail">t-001</div>
      </div>);
    expect(html).toContain("Case 1 of 12");
    expect(html).toContain("Answered");
    expect(html).not.toContain("font-mono");
  });
});
