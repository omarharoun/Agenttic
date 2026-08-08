// @vitest-environment jsdom
/* The scenario-run console screen.
 *
 * This screen is the first surface to render a fault report, and a fault report
 * is four different facts wearing one word. The tests that matter here are
 * therefore not "does it render" but "can a reader tell these apart":
 *
 *   fired         — it happened to this run
 *   skipped       — it reached its call and could NOT happen (with the reason)
 *   never_reached — staged on a call the agent never made; nothing happened
 *   recorded:false— nobody wrote any of it down
 *
 * Collapsing any two of those is the same defect as merging `resisted` and
 * `attempted_blocked` in a honeypot battery: it turns "we never found out" into
 * "it was fine". The same rule governs `state_diff: {}` (a measured result, and
 * it must SAY so rather than render as blank), `coverage.measured: false` (not
 * measured ≠ measured and empty), and `derived.n_user_turns: null` (uncounted ≠
 * zero).
 *
 * House style: `renderToStaticMarkup`, matching verification.test.tsx — every
 * component under test here is a pure function of its props, and is tested as
 * one. ONE block departs from that ("the page picks between the two zeros",
 * below) and has to: the defect it pins is not in a component's output, it is
 * in WHICH component the page reaches for, and that decision only exists after
 * an effect has resolved and a form has been submitted. `renderToStaticMarkup`
 * runs no effects, so a static render of `ScenariosPage` can only ever produce
 * the loading skeleton and the entire empty-state branch is unreachable from
 * it. That block therefore mounts the real page in a real DOM, which is why
 * this file declares `@vitest-environment jsdom` above. Nothing was added to
 * make that work: jsdom is already a locked, non-optional dependency of both
 * vitest and vite-react-ssg (`npm ls jsdom` → 24.1.3, deduped).
 *
 * The fixtures are trimmed copies of REAL bodies from
 * `Registry.get_scenario_run` (dumped from an offline scripted run against the
 * retail world, seeds 3/7/11), not shapes invented to suit the assertions.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import type {
  CoverageDivergence, ScenarioFaults, ScenarioRunDetail, ScenarioRunRow,
} from "./api";
import {
  Divergence, Elicitation, ExhibitedCoverage, FaultLedger, ListCap, RUN_LIMIT,
  RunId, RunRow, ScenarioEmpty, ScenarioNoMatch, ScenarioRunDetailView,
  ScenariosPage, StateDiff, ToolLedger, Transcript,
  enforcementOf, formatCreated, groupBins, isOtherBin,
} from "./pages/ScenariosPage";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
/** The visible words: markup stripped, then the entities React escapes decoded
 *  back. Two reasons — an assertion about COPY must not be satisfiable by a
 *  class name that happens to contain the phrase, and it must not have to know
 *  that an apostrophe renders as `&#x27;`. Takes an element or rendered markup. */
const ENTITIES: Record<string, string> = {
  "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#x27;": "'",
};
const text = (el: React.ReactElement | string) =>
  (typeof el === "string" ? el : html(el))
    .replace(/<[^>]+>/g, " ")
    .replace(/&(?:amp|lt|gt|quot|#x27);/g, (m) => ENTITIES[m]);
const NO_HEX = /#[0-9a-fA-F]{3,8}\b/;   // tokens only, so both themes follow

/* ------------------------------------------------------------------ faults */

const FIRED: ScenarioFaults = {
  recorded: true, source: "scenario_plan",
  planned: [{ tool: "lookup_order", call_index: 1, kind: "timeout", once: true }],
  fired: [{ tool: "lookup_order", call_index: 1, kind: "timeout", once: true,
            step: 1, observable: true }],
  skipped: [], never_reached: [],
  counts: { planned: 1, fired: 1, skipped: 0, never_reached: 0 },
};
const NEVER_REACHED: ScenarioFaults = {
  recorded: true, source: "scenario_plan",
  planned: [{ tool: "lookup_order", call_index: 1, kind: "timeout", once: true }],
  fired: [], skipped: [],
  never_reached: [{ tool: "lookup_order", call_index: 1, kind: "timeout", once: true }],
  counts: { planned: 1, fired: 0, skipped: 0, never_reached: 1 },
};
const SKIPPED: ScenarioFaults = {
  recorded: true, source: "scenario_plan",
  planned: [{ tool: "lookup_order", call_index: 1, kind: "stale_data", once: true }],
  fired: [], never_reached: [],
  skipped: [{ tool: "lookup_order", call_index: 1, kind: "stale_data", once: true,
              step: 2, reason: "no prior state for o-70056" }],
  counts: { planned: 1, fired: 0, skipped: 1, never_reached: 0 },
};
const NO_REPORT: ScenarioFaults = {
  recorded: false, source: null, planned: null, fired: null, skipped: null,
  never_reached: null, counts: null,
};
const NONE_STAGED: ScenarioFaults = {
  recorded: true, source: "none", planned: [], fired: [], skipped: [],
  never_reached: [],
  counts: { planned: 0, fired: 0, skipped: 0, never_reached: 0 },
};
/** What `_faults_view` actually serves when the stored plan will not rebuild:
 *  the lists exactly as stored, `never_reached: null` because that derivation
 *  is unavailable, `counts: null`, and a `problem`. The plan entry here is the
 *  ONLY evidence that survived, and the console has to show it. */
const UNRECONSTRUCTABLE: ScenarioFaults = {
  recorded: true, source: "scenario_plan",
  planned: [{ tool: "search_knowledge_base", call_index: 1, kind: "timeout",
              once: true }],
  fired: [], skipped: [], never_reached: null, counts: null,
  problem: "FaultPlanError: cannot stage a fault on 'search_knowledge_base': "
    + "no such tool. The world exposes: lookup_order, issue_refund.",
};

describe("the fault ledger keeps four facts apart", () => {
  it("gives a fired fault and a skipped fault different treatments", () => {
    const fired = html(<FaultLedger faults={FIRED} />);
    const skipped = html(<FaultLedger faults={SKIPPED} />);

    expect(fired).toContain("scn-fault--fired");
    expect(fired).not.toContain("scn-fault--skipped");
    expect(skipped).toContain("scn-fault--skipped");
    expect(skipped).not.toContain("scn-fault--fired");

    // and in WORDS, not only in a class name
    expect(text(fired)).toMatch(/\bfired\b/);
    expect(text(skipped)).toMatch(/\bskipped\b/);
    // the reason a skipped fault could not happen is the point of the state
    expect(text(skipped)).toContain("no prior state for o-70056");
  });

  it("gives a staged-but-never-reached fault its own treatment again", () => {
    const out = html(<FaultLedger faults={NEVER_REACHED} />);
    expect(out).toContain("scn-fault--never");
    expect(out).not.toContain("scn-fault--fired");
    expect(text(out)).toContain("never reached");
    // it is NOT a thing that happened, and the copy has to say so
    expect(text(out)).toMatch(/agent never made|never got there/i);
  });

  it("does not report a fired fault when none fired", () => {
    // The failure this guards: rendering `planned` as though it were `fired`,
    // which turns "we staged this and the agent never got there" into "the
    // agent survived a timeout".
    const out = text(<FaultLedger faults={NEVER_REACHED} />);
    expect(out).toContain("0 fired");
    expect(out).not.toMatch(/\b1 fired\b/);
  });

  it("says a report was never stored — not that nothing was staged", () => {
    const absent = text(<FaultLedger faults={NO_REPORT} />);
    const empty = text(<FaultLedger faults={NONE_STAGED} />);

    expect(absent).toContain("no fault report");
    expect(absent).toMatch(/not recorded|not the same as nothing/i);
    expect(empty).toContain("No fault was staged");

    // the two absences must not render the same markup
    expect(html(<FaultLedger faults={NO_REPORT} />))
      .not.toBe(html(<FaultLedger faults={NONE_STAGED} />));
    // and an unstored report must never be drawn as a clean sheet
    expect(absent).not.toContain("No fault was staged");
  });

  it("treats a missing report object the same as an unrecorded one", () => {
    expect(text(<FaultLedger faults={null} />)).toContain("no fault report");
    expect(text(<FaultLedger faults={undefined} />)).toContain("no fault report");
  });

  it("surfaces a report that could not be reconstructed", () => {
    const out = text(<FaultLedger faults={{
      ...FIRED, problem: "PlannedFault: teleport is not a tool of this world",
      never_reached: null, counts: null,
    }} />);
    expect(out).toContain("could not be reconstructed");
    expect(out).toContain("teleport is not a tool of this world");
    expect(out).toContain("Counts unavailable");
  });

  it("emits no raw hex — colour comes from tokens", () => {
    expect(html(<FaultLedger faults={FIRED} />)).not.toMatch(NO_HEX);
    expect(html(<FaultLedger faults={NEVER_REACHED} />)).not.toMatch(NO_HEX);
  });

  it("draws the plan entry a failed reconstruction leaves unaccounted for", () => {
    // The defect: with `never_reached: null` and nothing fired or skipped, the
    // ledger mapped three empty lists and rendered <ul class="scn-faults"></ul>
    // — an EMPTY list under a legend, directly below its own promise that "the
    // lists the run did store are shown below unchanged". The stored plan was
    // the one piece of evidence that survived the failure, and the surface that
    // exists to show it dropped it.
    const out = html(<FaultLedger faults={UNRECONSTRUCTABLE} />);
    const visible = text(out);

    expect(out).toContain("<li ");                     // a row at all
    expect((out.match(/<li /g) || []).length).toBe(1); // exactly the one stored
    expect(visible).toContain("search_knowledge_base");
    expect(visible).toContain("timeout on");
    expect(visible).toContain("call #1");
    // the sentence above it must not be left unkept
    expect(visible).toContain("shown below unchanged");
    // and the CLI's own reading of this payload — "1 staged (source: …)"
    expect(visible).toMatch(/\b1 staged\b/);
    expect(visible).toContain("scenario_plan");
    expect(out).not.toMatch(NO_HEX);
  });

  it("does not dress an underived outcome as one of the three results", () => {
    // "never reached" is precisely the derivation the registry declined to make
    // here; emitting it would manufacture the strongest sentence a fault report
    // can produce. Neither may the row read as fired or skipped.
    const out = html(<FaultLedger faults={UNRECONSTRUCTABLE} />);
    expect(out).toContain("scn-fault--staged");
    expect(out).not.toContain("scn-fault--fired");
    expect(out).not.toContain("scn-fault--skipped");
    expect(out).not.toContain("scn-fault--never");
    // in words on the row, not only in a class name
    expect(text(out)).toContain("outcome not derived");
    // the counts line reports what the LISTS hold and never a derived total
    expect(text(out)).toContain("Counts unavailable");
    expect(text(out)).toContain("never-reached is not derived");
    expect(text(out)).not.toMatch(/\b1 never reached\b/);
  });

  it("accounts a plan entry only on a WHOLE tool name and exact call", () => {
    // The substring family, in the shape it would take here: an event on
    // `lookup_order_history` is not an event on `lookup_order`, and call #10 is
    // not call #1. Either near miss counted as a match would delete the staged
    // row and quietly claim the plan was accounted for.
    const near: ScenarioFaults = {
      recorded: true, source: "scenario_plan",
      planned: [{ tool: "lookup_order", call_index: 1, kind: "timeout", once: true }],
      fired: [{ tool: "lookup_order_history", call_index: 1, kind: "timeout",
                once: true, step: 1, observable: true }],
      skipped: [{ tool: "lookup_order", call_index: 10, kind: "timeout",
                  once: true, step: 3, reason: "the agent stopped at call #4" }],
      never_reached: null, counts: null,
      problem: "FaultPlanError: lookup_order_history is not a tool of this world",
    };
    const out = html(<FaultLedger faults={near} />);

    expect(out).toContain("scn-fault--staged");        // the plan entry survives
    expect((out.match(/<li /g) || []).length).toBe(3); // and the two events too
    expect(text(out)).toContain("lookup_order_history");
    expect(text(out)).toContain("the agent stopped at call #4");
  });

  it("invents no unaccounted row when the report did reconstruct", () => {
    // The state exists only where a derivation failed. On a report whose
    // `never_reached` came back derived, planned minus events IS never_reached,
    // so a fourth row would be the same fault drawn twice.
    for (const f of [FIRED, SKIPPED, NEVER_REACHED, NONE_STAGED]) {
      const out = html(<FaultLedger faults={f} />);
      expect(out).not.toContain("scn-fault--staged");
      expect(text(out)).not.toContain("outcome not derived");
    }
  });

  it("never says nothing was staged while holding a stored event", () => {
    // A report that failed to rebuild can come back with an empty plan beside a
    // fired event. Keying the empty-plan copy on `planned` alone printed "No
    // fault was staged" over evidence that one did — and dropped the event.
    const out = html(<FaultLedger faults={{
      recorded: true, source: "requested_tool_condition",
      planned: [],
      fired: [{ tool: "lookup_order", call_index: 1, kind: "timeout", once: true,
                step: 1, observable: true }],
      skipped: [], never_reached: null, counts: null,
      problem: "FaultPlanError: 'nope' is not a fault kind",
    }} />);

    expect(text(out)).not.toContain("No fault was staged");
    expect(out).toContain("scn-fault--fired");
    expect(text(out)).toContain("lookup_order");
  });
});

/* ------------------------------------------------------------ the list row */

/** A row off `/api/scenario-runs`. The list carries only `recorded` + `counts`
 *  — enough for all four states, because `recorded: true, counts: null` is
 *  reachable only from a report that would not rebuild. */
const LIST_ROW: ScenarioRunRow = {
  run_id: "60a60d9725324c88bec6bf526d5a2682", scenario_id: "scn-refund",
  agent_id: "scenario-agent", trace_id: "60a60d9725324c88bec6bf526d5a2682",
  space_ref: "retail-support-v1", space_fingerprint: "b1c2d3", seed: 3,
  created_at: "2026-07-30T21:42:00.000000", ended: "",
  conversational: false, world_changed: false, n_blocked: 0,
  faults: { recorded: true,
            counts: { planned: 1, fired: 1, skipped: 0, never_reached: 0 } },
};
const row = (faults: ScenarioRunRow["faults"]) =>
  html(<table><tbody>
    <RunRow r={{ ...LIST_ROW, faults }} selected={false} onSelect={() => {}} />
  </tbody></table>);

describe("the run list row keeps the same four fault facts apart", () => {
  it("does not report a stored report that would not rebuild as no report", () => {
    // The defect: `recorded !== true || !counts` folded the unreadable state
    // into the unrecorded one, so the list said "no report" about a run whose
    // report is in the payload — and whose own detail view says, one click
    // away, that the report could not be reconstructed.
    const unreadable = text(row({ recorded: true, counts: null }));
    expect(unreadable).toContain("report unreadable");
    expect(unreadable).not.toContain("no report");
    expect(unreadable).not.toContain("none staged");
    expect(unreadable).not.toMatch(/\b0 fired\b/);

    // and it is not drawn as the unrecorded absence either
    expect(row({ recorded: true, counts: null }))
      .not.toBe(row({ recorded: false, counts: null }));
    expect(row({ recorded: true, counts: null })).toContain("scn-unreadable");
    expect(row({ recorded: false, counts: null })).not.toContain("scn-unreadable");
  });

  it("still says no report when nobody wrote one down", () => {
    const t = text(row({ recorded: false, counts: null }));
    expect(t).toContain("no report");
    expect(t).not.toContain("report unreadable");
    expect(t).not.toMatch(/\b0 fired\b/);
  });

  it("keeps a measured empty plan and a plan that ran as themselves", () => {
    const none = text(row({ recorded: true,
      counts: { planned: 0, fired: 0, skipped: 0, never_reached: 0 } }));
    expect(none).toContain("none staged");
    expect(none).not.toContain("report unreadable");
    expect(none).not.toContain("no report");

    const ran = text(row(LIST_ROW.faults));
    expect(ran).toContain("1 fired");
    expect(ran).not.toContain("report unreadable");
    expect(ran).not.toContain("no report");
  });

  it("emits no raw hex — colour comes from tokens", () => {
    expect(row({ recorded: true, counts: null })).not.toMatch(NO_HEX);
    expect(row({ recorded: false, counts: null })).not.toMatch(NO_HEX);
  });
});

/* --------------------------------------------------------------- the run id */

/* The run id is the one value an operator has to carry off this screen: it is
 * the argument to `agenttic scenario transcript <id>`. The cell rendered
 * `slice(0, 12)` with no ellipsis, no title and nothing to copy — a partial
 * value drawn exactly as a whole one, which is this page's own defect family
 * (an absence rendered as a result) wearing a different hat. It fails at the
 * far end, as "no such run", with nothing on the screen to have warned you. */
describe("the run id says when it is not the whole id", () => {
  const FULL = "60a60d9725324c88bec6bf526d5a2682";   // 32 hex, as stored

  it("draws the cut, and keeps the whole id reachable", () => {
    const out = html(<RunId id={FULL} />);
    const visible = text(out);

    expect(visible).toContain(FULL.slice(0, 12));   // the prefix is what is shown
    expect(visible).toContain("…");                 // and it is marked as cut
    expect(visible).not.toContain(FULL);            // it is genuinely cut

    // the whole value is on the element and in the copy control's name, so it
    // is recoverable from this row without loading the run
    expect(out).toContain(`title="${FULL}"`);
    expect(out).toContain("<button");
    expect(out).toContain(`copy the whole run id ${FULL}`);
  });

  it("does not mark an id it did not cut", () => {
    const short = html(<RunId id="4024a7a1" />);
    expect(text(short)).toContain("4024a7a1");
    expect(text(short)).not.toContain("…");
  });

  it("offers nothing to copy when the row has no id", () => {
    // copying "" would look exactly like copying an id.
    const out = html(<RunId id="" />);
    expect(text(out)).toContain("(no id)");
    expect(out).not.toContain("<button");
  });

  it("carries the whole id into the list row", () => {
    const out = row(LIST_ROW.faults);
    expect(out).toContain(LIST_ROW.run_id);            // whole, on the element
    expect(text(out)).not.toContain(LIST_ROW.run_id);  // cut, on the screen
    expect(text(out)).toContain(LIST_ROW.run_id.slice(0, 12));
    expect(text(out)).toContain("…");
  });

  it("emits no raw hex — colour comes from tokens", () => {
    expect(html(<RunId id={FULL} />)).not.toMatch(NO_HEX);
  });
});

/* --------------------------------------------------------------- empty run */

describe("the empty state", () => {
  it("renders without inventing a run", () => {
    const out = html(<ScenarioEmpty />);
    const visible = text(out);

    expect(visible).toContain("No scenario run has been stored yet");
    // it says how to make one, and the command is pasteable as rendered
    expect(visible).toContain(
      "uv run agenttic scenario run --intent refund --tool-condition timeout");
    // and it names what has to persist it, so an empty list is diagnosable
    expect(visible).toContain("save_scenario_run");

    // nothing that could be mistaken for a run: no table, no id, no counts
    expect(out).not.toContain("<table");
    expect(out).not.toContain("<tr");
    expect(visible).not.toMatch(/\bscn-[0-9a-f]{6}/);   // a scenario id shape
    expect(visible).not.toMatch(/\b\d+ fired\b/);
    expect(visible).not.toMatch(/\bsingle-shot\b|\bconversation\b/);
  });

  /* The <pre> holds the simplest command that fills this page from a cold
   * install. `cdv` fills it too — it persists one row per scenario — but it is
   * not pasteable here: it requires `--rubric <id>` naming a rubric already in
   * the registry, so on an empty store the copied line fails before it runs. */
  it("prints a command that can actually populate this list", () => {
    const out = html(<ScenarioEmpty />);
    // Assert on the <pre> — the thing a reader COPIES — not on the whole blob,
    // because the prose below it names cdv on purpose and a document-wide ban
    // would forbid that.
    const pasted = out.match(/<pre[^>]*>([\s\S]*?)<\/pre>/)?.[1] ?? "";
    expect(pasted).not.toBe("");
    expect(pasted).toContain("agenttic scenario run");
    expect(pasted).not.toMatch(/\bcdv\b/);
    // `scenario run` has no --mock flag (it exits 2); offline is its default.
    expect(pasted).not.toMatch(/--mock\b/);
  });

  /* THE REGRESSION. This screen used to rule the batch command out — "an empty
   * list after a cdv run is that command's scope, not a dropped write" — and a
   * test pinned that sentence. It was measured when cdv persisted nothing, and
   * it stopped being true: `harness_executor` calls `persist_scenario_run` for
   * every scenario it executes (`scenario/runner.py:1771`). Re-measured on a
   * scratch registry holding 3 stored runs, `agenttic cdv --agent demo-bot
   * --rubric r-cdv-persist --mock --max-scenarios 4 --max-rounds 1` left
   * `select count(*) from scenario_runs` at 7 — four new rows under `demo-bot`.
   *
   * That makes the old sentence the worst thing this page can print.
   * `persist_scenario_run` swallows every storage failure — WARNING log, batch
   * finishes — so an empty list after a cdv run that printed a scorecard is a
   * REAL LOST WRITE, and the page was telling the reader to file it as
   * by-design. An absence explained away as a non-finding, on the screen whose
   * whole job is saying whether the evidence exists. */
  it("does not explain an empty list after cdv away as expected", () => {
    const visible = text(html(<ScenarioEmpty />));
    expect(visible).toMatch(/\bagenttic cdv\b/);
    // the pinned false claim, and the two ways of stating it
    expect(visible).not.toContain("not a dropped write");
    expect(visible).not.toMatch(/stores none of them/);
    expect(visible).not.toMatch(/\bonly caller\b/);
    // and neither older false diagnosis may come back
    expect(visible).not.toContain("scored and thrown away");
  });

  it("keeps 'nothing ran' and 'the write was lost' as two states", () => {
    const visible = text(html(<ScenarioEmpty />));
    // The page has no signal for which one this zero is, so it must offer both
    // rather than settle it for the reader.
    expect(visible).toMatch(/nothing ran/i);
    expect(visible).toMatch(/lost/i);
    // and it must name how to tell them apart: the id printed on a good write,
    // the log line left by a failed one.
    expect(visible).toContain("stored as run");
    expect(visible).toContain("NOT STORED");
  });
});

/* ---------------------------------------------------------- the state diff */

describe("the state diff", () => {
  it("says the world was not changed rather than rendering blank", () => {
    const out = html(<StateDiff diff={{}} />);
    const visible = text(out);
    expect(visible).toContain("The world was not changed");
    expect(out).not.toContain("<table");
    // it is a RESULT, not the unrecorded-absence treatment
    expect(out).toContain("scn-none");
    expect(out).not.toContain("scn-absent");
  });

  it("says the same for a missing diff object", () => {
    expect(text(<StateDiff diff={null} />)).toContain("The world was not changed");
    expect(text(<StateDiff diff={undefined} />)).toContain("The world was not changed");
  });

  it("shows before and after for every changed field", () => {
    const out = text(<StateDiff diff={{
      "orders.o-71638.refunded_usd": { before: 0, after: 290 },
      "orders.o-71638.status": { before: "delivered", after: "refunded" },
      "orders.o-71638.terminal": { before: false, after: true },
    }} />);
    expect(out).toContain("orders.o-71638.refunded_usd");
    // 0 and false are real prior states and must not render as blank
    expect(out).toContain("0");
    expect(out).toContain("false");
    expect(out).toContain("290");
    expect(out).not.toContain("The world was not changed");
  });
});

/* ------------------------------------------------------- enforcement chips */

describe("enforcementOf matches the recorded token, not a fragment", () => {
  it("reads the three verdicts the environment writes", () => {
    expect(enforcementOf({ attributes: { enforcement: "executed" } })).toBe("executed");
    expect(enforcementOf({ attributes: { enforcement: "blocked" } })).toBe("blocked");
    expect(enforcementOf({ attributes: { enforcement: "faulted" } })).toBe("faulted");
  });

  it("does not let a fragment or a lookalike pass as a verdict", () => {
    // The dominant defect family in this codebase is substring matching:
    // "blocked" inside "not_blocked", "executed" inside "not_executed". A
    // verdict is matched whole or it is not a verdict.
    for (const v of ["not_blocked", "blocked_by_nothing", "unblocked",
                     "executed_allowed", "Executed", "fault", "faulted_maybe"]) {
      expect(enforcementOf({ attributes: { enforcement: v } })).toBe("unrecorded");
    }
  });

  it("reports an absent verdict as unrecorded, never as allowed", () => {
    expect(enforcementOf({})).toBe("unrecorded");
    expect(enforcementOf({ attributes: {} })).toBe("unrecorded");
    expect(enforcementOf({ attributes: { enforcement: undefined } })).toBe("unrecorded");
  });
});

describe("the tool ledger", () => {
  const SPANS = [
    { span_id: "t1.llm-000", kind: "llm_call", name: "scripted-support" },
    { span_id: "t1.tool-001", kind: "tool_call", name: "lookup_order",
      error: "deadline exceeded: no response from lookup_order after 30000ms",
      attributes: { enforcement: "faulted", injected_fault: "timeout",
                    decision_action: "allow", decision_evidence: [] } },
    { span_id: "t1.tool-003", kind: "tool_call", name: "issue_refund",
      error: "BLOCKED_BY_HARNESS[dec-1]: irreversible write without approval",
      attributes: { enforcement: "blocked", decision_action: "require_approval",
                    decision_evidence: ["irreversible"] } },
    { span_id: "t1.tool-005", kind: "tool_call", name: "get_customer",
      error: null, attributes: { enforcement: "executed", decision_action: "allow",
                                 decision_evidence: [] } },
  ];

  it("gives a faulted call, a blocked call and a call that behaved three treatments", () => {
    const out = html(<ToolLedger spans={SPANS} blocked={["issue_refund"]} />);
    expect(out).toContain("scn-enf--faulted");
    expect(out).toContain("scn-enf--blocked");
    expect(out).toContain("scn-enf--executed");
    // the fault that fired on that call is named, not just implied by a colour
    expect(text(out)).toContain("timeout");
    expect(text(out)).toContain("require_approval");
    expect(out).not.toMatch(NO_HEX);
  });

  it("does not let a corrupted reply read as a tool that simply behaved", () => {
    // `malformed_response` is the one fault kind that still EXECUTES — the
    // environment records enforcement "executed" and stamps the injected fault
    // beside it. Reading only the enforcement column would show this call as
    // clean, when the agent was handed a truncated payload.
    const out = html(<ToolLedger blocked={[]} spans={[
      { span_id: "t1.tool-001", kind: "tool_call", name: "lookup_order",
        error: null,
        attributes: { enforcement: "executed", injected_fault: "malformed_response",
                      decision_action: "allow" } },
      { span_id: "t1.tool-003", kind: "tool_call", name: "get_customer",
        error: null, attributes: { enforcement: "executed", decision_action: "allow" } },
    ]} />);
    expect(text(out)).toContain("malformed_response");
    // exactly one of the two executed calls carries the fault treatment
    expect((out.match(/scn-enf--faulted/g) ?? []).length).toBe(1);
  });

  it("lists only tool calls, and counts them from the trace", () => {
    const out = html(<ToolLedger spans={SPANS} blocked={[]} />);
    expect(out).not.toContain("scripted-support");   // the llm span is not a call
    expect((out.match(/<tr/g) ?? []).length).toBe(4); // header + three calls
  });

  it("does not report a trace it is still fetching as unreadable", () => {
    // The in-flight window is a fourth state. Rendering it as "could not be
    // read" states a finding about a request that has not come back.
    const out = text(<ToolLedger spans={null} blocked={[]} pending />);
    expect(out).toContain("Reading the trace");
    expect(out).not.toContain("could not be read");
    expect(out).not.toContain("called no tools");
  });

  it("distinguishes 'the agent called nothing' from 'the trace could not be read'", () => {
    const none = text(<ToolLedger spans={[]} blocked={[]} />);
    expect(none).toContain("called no tools");

    const unread = html(<ToolLedger spans={null} blocked={["issue_refund"]}
                                    traceProblem="403 forbidden" />);
    expect(text(unread)).toContain("could not be read");
    expect(text(unread)).toContain("403 forbidden");
    expect(text(unread)).not.toContain("called no tools");
    // the stored evidence that survives is still shown
    expect(text(unread)).toContain("issue_refund");
    expect(unread).toContain("scn-absent");
  });
});

/* ---------------------------------------------------------- the transcript */

const CONV: ScenarioRunDetail = {
  run_id: "4024a7a1", scenario_id: "scn-4336249d6aaa2c87", agent_id: "api-conv",
  trace_id: "4024a7a1", space_ref: "space:space-conversational_transactional@v2",
  space_fingerprint: "5be8bca7542c0d4d", seed: 11,
  created_at: "2026-07-30T20:20:14.841736",
  point: { intent: "account_change", emotional_register: "neutral",
           data_condition: "complete", tool_condition: "all_ok",
           policy_vector: "compliant" },
  ticket: "Please change the delivery address on my account.",
  session_id: "sess-1342a6eab02e4d6f", ended: "satisfied",
  transcript: [
    { speaker: "user", text: "Please change the delivery address on my account.",
      kind: "open", discloses: "", revealed_fact: false, delivered: true },
    { speaker: "agent", text: "Which order is this about?" },
    { speaker: "user", text: "It's o-67434. Hope that helps.", kind: "reveal",
      discloses: "order_id", revealed_fact: true, delivered: true },
    { speaker: "agent", text: "Your delivery address is updated." },
    { speaker: "user", text: "Thanks, that's sorted then.", kind: "close",
      discloses: "", revealed_fact: false, delivered: false },
  ],
  state_diff: { "customers.c-1011.address": { before: "77 Sable Avenue, Antwerp",
                                              after: "9 Marlow Gate, Ghent" } },
  blocked: [], interactions: [], faults: NONE_STAGED,
  elicitation: { disclosed: ["order_id"], withheld: [] },
  coverage: { measured: true, bins: ["action_risk:write", "trajectory:tool_then_answer"] },
  user_provenance: { user_source: "simulated", simulator: "scripted" },
  disclosures: [],
  derived: { conversational: true, n_user_turns: 2, world_changed: true,
             n_changed_fields: 1, n_blocked: 0, elicitation_complete: true,
             content_sha256: "ef079adb" },
};

const SINGLE: ScenarioRunDetail = {
  ...CONV, run_id: "b64692d9", agent_id: "api-single", session_id: "", ended: "",
  transcript: [], state_diff: {}, faults: NEVER_REACHED,
  elicitation: { disclosed: [], withheld: [] },
  coverage: { measured: true, bins: [] }, user_provenance: {},
  derived: { ...CONV.derived, conversational: false, n_user_turns: 0,
             world_changed: false, n_changed_fields: 0,
             elicitation_complete: null },
};

describe("the transcript", () => {
  it("marks the turn that handed over a gated fact", () => {
    const out = html(<Transcript run={CONV} />);
    expect(out).toContain("scn-reveal");
    expect(text(out)).toContain("revealed");
    expect(text(out)).toContain("order_id");
    // exactly one turn revealed something
    expect((out.match(/scn-reveal/g) ?? []).length).toBe(1);
  });

  it("marks the closing line as never delivered to the agent", () => {
    const out = html(<Transcript run={CONV} />);
    expect(text(out)).toContain("not delivered");
    // …and marks ONLY that one. `!t.delivered` on an agent line (which has no
    // such field) would mark every agent reply as ignored.
    expect((out.match(/scn-undelivered/g) ?? []).length).toBe(1);
  });

  it("says a single-shot run had no conversation, rather than showing none", () => {
    const out = html(<Transcript run={SINGLE} />);
    const visible = text(out);
    expect(visible).toContain("Single-shot run");
    expect(out).toContain("scn-none");
    expect(out).not.toContain("scn-turn ");
  });

  it("never renders an uncounted turn count as zero", () => {
    const uncounted = { ...CONV,
      derived: { ...CONV.derived, n_user_turns: null } };
    const out = text(<Transcript run={uncounted} />);
    expect(out).toContain("not counted");
    expect(out).toContain("That is not zero");
    expect(out).not.toMatch(/Counterparty turns: 0/);
  });

  it("distinguishes a session with no stored transcript from a single-shot run", () => {
    const bare = { ...CONV, transcript: [] };
    const out = html(<Transcript run={bare} />);
    expect(text(out)).toContain("no transcript was stored");
    expect(out).toContain("scn-absent");
    expect(text(out)).not.toContain("Single-shot run");
  });

  it("does not count the turns of a conversation that never happened", () => {
    // The defect: "Counterparty turns: 0 (counted off the trace, not off the
    // transcript)." printed directly beneath "Single-shot run … there was no
    // conversation" — a sourced, qualified hard zero for a quantity that was
    // never applicable, which reads as a measurement of a conversation that did
    // not exist. `cli.py:_render_conversation` prints no count here either.
    const out = text(<Transcript run={SINGLE} />);
    expect(out).toContain("Single-shot run");
    expect(out).not.toMatch(/Counterparty turns: 0\b/);
    expect(out).not.toContain("counted off the trace");
    expect(out).toContain("not applicable");
    expect(out).toContain("not a count of zero");
  });

  it("still counts a conversation whose counterparty took no turn", () => {
    // The mirror, which the fix above must not break: on a run that DID hold a
    // conversation, 0 is a measurement and has to survive as one.
    const quiet = { ...CONV, derived: { ...CONV.derived, n_user_turns: 0 } };
    const out = text(<Transcript run={quiet} />);
    expect(out).toContain("Counterparty turns: 0");
    expect(out).toContain("counted off the trace");
    expect(out).not.toContain("not applicable");
  });

  it("reports a record that disagrees with itself instead of resolving it", () => {
    // `conversational` comes off a stored session id and `n_user_turns` off
    // `user_turn` spans in the trace: two sources, so they can disagree. The
    // disagreement is the finding — not a count to print, and not a
    // "not applicable" that hides a trace saying otherwise.
    const odd = { ...SINGLE, derived: { ...SINGLE.derived, n_user_turns: 3 } };
    const out = text(<Transcript run={odd} />);
    expect(out).toContain("disagrees with itself");
    expect(out).toContain("carries 3 counterparty turn");
    expect(out).not.toContain("not applicable");
    expect(out).not.toMatch(/Counterparty turns: 3\b/);
  });
});

/* ----------------------------------------------------------- elicitation */

/** The state the real store ACTUALLY produces, and the one neither fixture
 *  above held: a CONVERSATION that gated nothing.
 *
 *  Dumped from `Registry.get_scenario_run` after
 *  `agenttic scenario run --intent refund --multi-turn --seed 7` (run
 *  5d2d4ebe445a42de955516944f9235dd, offline scripted agent, retail world):
 *
 *      ended= satisfied  disclosed= []  withheld= []
 *      conversational= True  elicitation_complete= True
 *
 *  `elicitation_complete` is not a measurement here. The registry recomputes it
 *  with `SimulatedSession.completed` = *satisfied AND nothing still withheld*,
 *  so a run that gated nothing is `true` BY CONSTRUCTION. It is also the COMMON
 *  case, not a corner — most intents gate on nothing, because a fact already
 *  stated in the opening ticket is excluded from the gate set.
 *
 *  CONV (gated `order_id`, complete) and SINGLE (no conversation at all) both
 *  miss this, which is how the console shipped printing "The counterparty left
 *  satisfied with nothing still withheld." over a check that never ran. */
const VACUOUS: ScenarioRunDetail = {
  ...CONV,
  run_id: "5d2d4ebe445a42de955516944f9235dd",
  scenario_id: "scn-bd569e566fb1f20c", agent_id: "scenario-agent", seed: 7,
  session_id: "sess-83c7787dcd3b4b0f", ended: "satisfied",
  elicitation: { disclosed: [], withheld: [] },
  derived: { ...CONV.derived, conversational: true, n_user_turns: 1,
             elicitation_complete: true },
};

describe("elicitation", () => {
  it("prints NO completion verdict for a run that gated nothing", () => {
    // THE DEFECT. `Elicitation` detected the vacuous case and then fell through
    // to `{complete != null && …}`, printing the green sentence underneath it —
    // the M40 vacuity rule inverted, a check that never ran rendered as a check
    // that passed. `cli.py:_render_conversation` RETURNS before its own
    // completeness branch on exactly this row; the console did not, so the two
    // surfaces said opposite things about the same run id.
    const out = html(<Elicitation run={VACUOUS} />);
    const visible = text(out);

    expect(visible).toContain("gated no facts");
    // neither verdict sentence, in either direction
    expect(visible).not.toContain("left satisfied with nothing still withheld");
    expect(visible).not.toContain("did NOT leave satisfied");
    // and no verdict paragraph at all — the vacuous block is the whole render
    expect(out).not.toContain("scn-sub");
    // it must say what the CLI says: unexercised is not a pass
    expect(visible).toContain("Not a pass");
    expect(visible).toContain("the check never ran");
    expect(out).not.toMatch(NO_HEX);
  });

  it("does not let the unexercised run look like the one that passed", () => {
    // The acceptance criterion is not "the string is accurate", it is that a
    // reader can TELL THESE APART. CONV gated `order_id` and the agent asked
    // for it — a real pass. VACUOUS gated nothing and is `complete: true` by
    // construction. Both carry `elicitation_complete: true`.
    const vacuous = text(<Elicitation run={VACUOUS} />);
    const passed = text(<Elicitation run={CONV} />);

    expect(vacuous).not.toBe(passed);
    expect(passed).toContain("left satisfied with nothing still withheld");
    expect(vacuous).not.toContain("left satisfied with nothing still withheld");
    expect(vacuous).toContain("gated no facts");
    expect(passed).not.toContain("gated no facts");
  });

  it("still prints the verdict on a run that DID gate a fact", () => {
    // The mirror the fix must not break: where the check really ran, its result
    // is a result and has to survive as one.
    const out = html(<Elicitation run={CONV} />);
    expect(out).toContain("<code>order_id</code>");
    expect(text(out)).toContain("The counterparty left satisfied with nothing still withheld.");
    expect(text(out)).not.toContain("never ran");
  });

  it("still reports a gated fact the agent never asked for", () => {
    const incomplete = { ...CONV,
      ended: "gave_up",
      elicitation: { disclosed: [], withheld: ["order_id"] },
      derived: { ...CONV.derived, elicitation_complete: false } };
    const out = text(<Elicitation run={incomplete} />);
    expect(out).toContain("did NOT leave satisfied");
    // one empty list is not the vacuous case: the gate set is disclosed ∪
    // withheld, and this run gated `order_id` and never got it.
    expect(out).not.toContain("gated no facts");
    expect(out).not.toContain("never ran");
  });

  it("separates 'the check never ran' from 'the verdict was not recorded'", () => {
    // Two absences, and they are two different claims: a run that gated nothing
    // (no check to run) against a run that gated facts and stored no verdict
    // (unknown, which is not complete). Rendering the second as silence put a
    // gated run on screen with no verdict and nothing saying why.
    const unrecorded = { ...CONV,
      derived: { ...CONV.derived, elicitation_complete: null } };
    const out = html(<Elicitation run={unrecorded} />);
    const visible = text(out);

    expect(visible).toContain("no completeness verdict");
    expect(visible).toContain("which is not the same as complete");
    expect(out).toContain("scn-absent");         // the UNRECORDED vocabulary…
    expect(html(<Elicitation run={VACUOUS} />)).not.toContain("scn-absent");
    expect(visible).not.toContain("left satisfied with nothing still withheld");
    expect(out).not.toMatch(NO_HEX);
  });

  it("renders no elicitation heading at all for a single-shot run", () => {
    // Nothing was withheld from this agent, so there was never a check.
    expect(html(<Elicitation run={SINGLE} />)).toBe("");
  });
});

/* ------------------------------------------------------------- coverage */

describe("exhibited coverage", () => {
  it("separates 'not measured' from 'measured and credited nothing'", () => {
    const unmeasured = html(<ExhibitedCoverage coverage={{ measured: false, bins: null }} />);
    const empty = html(<ExhibitedCoverage coverage={{ measured: true, bins: [] }} />);

    expect(text(unmeasured)).toContain("No coverage was collected");
    expect(unmeasured).toContain("scn-absent");

    expect(text(empty)).toContain("credited nothing");
    expect(empty).toContain("scn-none");
    expect(empty).not.toContain("scn-absent");

    expect(unmeasured).not.toBe(empty);
  });

  it("credits bins from what the run exhibited, and says so", () => {
    const out = html(<ExhibitedCoverage coverage={CONV.coverage} />);
    expect(text(out)).toContain("EXHIBITED");
    expect(text(out)).toContain("action risk");
    expect(text(out)).toContain("write");
    expect(text(out)).toContain("tool_then_answer");
    // it must not be sold as closure
    expect(text(out)).toMatch(/not closure/);
    expect(out).not.toMatch(NO_HEX);
  });
});

describe("groupBins splits on the FIRST colon and drops nothing", () => {
  it("groups by coverpoint", () => {
    expect(groupBins(["action_risk:write", "trajectory:tool_then_answer",
                      "action_risk:read"]))
      .toEqual([["action_risk", ["write", "read"]],
                ["trajectory", ["tool_then_answer"]]]);
  });

  it("keeps a colon inside the bin value", () => {
    expect(groupBins(["policy_vector:deny:refund"]))
      .toEqual([["policy_vector", ["deny:refund"]]]);
  });

  it("does not silently discard a bin with no colon", () => {
    expect(groupBins(["trajectory", ""]))
      .toEqual([["(no dimension)", ["trajectory", ""]]]);
  });

  it("does not treat a leading colon as a dimension", () => {
    // ":write" names no coverpoint; guessing one would invent a dimension the
    // run never exhibited.
    expect(groupBins([":write"])).toEqual([["(no dimension)", [":write"]]]);
  });
});

/* ------------------------------------------------------------ the other bin */

/* `other` is the coverage model's mandatory catch-all and is OUTSIDE closure by
 * construction. The console never marked it, and it split a bin id on a
 * DIFFERENT COLON from the one the CLI splits on (`cli.py:_render_coverage`
 * uses `rpartition`, the last; the console used the first). Two surfaces
 * disagreeing about what a bin id means is drift, and one of them is then wrong
 * about what counts toward closure. */
describe("the other bin is read exactly as the CLI reads it", () => {
  it("takes the token after the LAST colon, as rpartition does", () => {
    expect(isOtherBin("action_risk:other")).toBe(true);
    expect(isOtherBin("policy_vector:deny:other")).toBe(true);
    expect(isOtherBin("other")).toBe(true);   // rpartition of a bin with no colon
  });

  it("does not let a lookalike pass as the unmodelled bin", () => {
    // The substring family again. Every one of these is a MODELLED bin of its
    // coverpoint, and a substring test would report each as outside closure —
    // "trajectory:another" is the case the CLI's own comment calls out.
    for (const b of ["trajectory:another", "action_risk:other_write",
                     "action_risk:writeother", "other:refund", "others",
                     "trajectory:Other", ""]) {
      expect(isOtherBin(b)).toBe(false);
    }
  });

  it("marks it as outside closure in words, not by colour", () => {
    const out = html(<ExhibitedCoverage coverage={{
      measured: true, bins: ["action_risk:write", "trajectory:other"] }} />);
    expect(out).toContain("scn-bins__other");
    expect((out.match(/scn-bins__other\b/g) ?? []).length).toBe(1);
    expect(text(out)).toContain("outside closure");
    expect(text(out)).toContain("counts toward nothing");
    expect(out).not.toMatch(NO_HEX);
  });

  it("does not mark a bin that merely ends in the word", () => {
    const out = html(<ExhibitedCoverage coverage={{
      measured: true, bins: ["trajectory:another"] }} />);
    expect(out).not.toContain("scn-bins__other");
    expect(text(out)).not.toContain("outside closure");
    expect(text(out)).toContain("another");   // and it is still credited
  });

  it("says nothing about `other` on a run that exhibited none", () => {
    // A permanent note would imply every run has one.
    const out = text(<ExhibitedCoverage coverage={CONV.coverage} />);
    expect(out).not.toContain("outside closure");
    expect(out).not.toContain("catch-all");
  });
});

/* ------------------------------------------------------------- divergence */

/* "Asked for, never exhibited": what the stimulus point requested against what
 * the run actually produced. It is the single most important thing this product
 * says, and until the store carried it, it existed only in terminal output that
 * nothing kept. Three states, exactly as `coverage.bins` keeps three:
 *   null  — nobody computed it. NOT a finding that none diverged.
 *   []    — computed, and every requested corner appeared. A measurement.
 *   [...] — the point asked for these and the run did not deliver them. */
const DIVERGED: CoverageDivergence[] = [
  { coverpoint_id: "tool_condition", bin_id: "timeout", requested: 2, exhibited: 0 },
  { coverpoint_id: "trajectory", bin_id: "escalate", requested: 1, exhibited: 0 },
];

describe("divergence keeps three states apart", () => {
  it("separates 'nobody computed it' from 'computed, nothing diverged'", () => {
    const unrecorded = html(<Divergence rows={null} />);
    const clean = html(<Divergence rows={[]} />);
    const diverged = html(<Divergence rows={DIVERGED} />);

    expect(text(unrecorded)).toContain("not recorded");
    expect(unrecorded).toContain("scn-absent");
    expect(unrecorded).not.toContain("scn-none");
    // the vacuity rule: an unrecorded read may not report a clean result
    expect(text(unrecorded)).not.toContain("nothing diverged");

    expect(text(clean)).toContain("nothing diverged");
    expect(clean).toContain("scn-none");
    expect(clean).not.toContain("scn-absent");

    // three states, three markups
    expect(unrecorded).not.toBe(clean);
    expect(clean).not.toBe(diverged);
    expect(unrecorded).not.toBe(diverged);
  });

  it("reads a payload written before the field as not recorded", () => {
    expect(html(<Divergence rows={undefined} />))
      .toBe(html(<Divergence rows={null} />));
  });

  it("names each corner the point asked for and never got", () => {
    const out = html(<Divergence rows={DIVERGED} />);
    const visible = text(out);
    expect((out.match(/<li /g) ?? []).length).toBe(2);
    expect(visible).toContain("asked for, never exhibited");
    expect(visible).toContain("tool_condition");
    expect(visible).toContain("timeout");
    expect(visible).toContain("trajectory");
    expect(visible).toContain("escalate");
    // how many samples asked, and what came back — a hard 0 that IS a result
    expect(visible).toContain("requested 2");
    expect(visible).toContain("exhibited 0");
    expect(out).not.toMatch(NO_HEX);
  });

  it("never turns a divergence row into a coverage number", () => {
    // These rows are a fact about the GENERATOR's reach. Summed into a
    // percentage they would read as a score against the agent.
    const visible = text(<Divergence rows={DIVERGED} />);
    expect(visible).not.toMatch(/\d\s*%/);
    expect(visible).toContain("GENERATOR");
    expect(visible).toContain("never a fact about the agent");
  });
});

/* ------------------------------------- the fourth fact, cutting across three */

/* `[]` said "Every corner this run's point asked for was exhibited by the run
 * itself" — a UNIVERSAL claim over a set that had been quietly reduced.
 *
 * `collect()` records a stimulus hit only for a requested dimension the coverage
 * model names and has that bin for, and `divergence()` further skips a
 * coverpoint that is not measurable. On the default path the point carries five
 * dimensions and `baseline_model` names two — its own BASELINE_LIMITS says it
 * "does NOT cover intent, emotional register or policy pressure" — so three of
 * five corners were compared against nothing and the sentence spoke for all
 * five. Empty set implies success, on the one sentence this product exists to
 * say. The console renderer (`cli.py:_render_divergence`) makes the same
 * derivation from the same stored row, and these two surfaces must not disagree
 * about which corners were compared. */
const POINT_5 = {
  intent: "complaint", emotional_register: "hostile", data_condition: "complete",
  tool_condition: "timeout", policy_vector: "edge_of_policy",
};

describe("divergence never closes over corners nothing compared", () => {
  it("names every requested corner neither list mentions", () => {
    const markup = html(<Divergence rows={[]} point={POINT_5}
                                    bins={["data_condition:complete",
                                           "tool_condition:timeout"]} />);
    const out = text(markup);
    expect(out).not.toContain("Every corner this run's point asked for was "
                              + "exhibited");
    expect(out).toContain("2 of 5 corners");
    expect(out).toContain("3 of the 5 corners");
    for (const [cp, bin] of [["intent", "complaint"],
                             ["emotional_register", "hostile"],
                             ["policy_vector", "edge_of_policy"]]) {
      expect(markup).toContain(`${cp}=<b>${bin}</b>`);
    }
    // …and the two the read DID speak for are not among them
    expect(markup).not.toContain("data_condition=<b>complete</b>");
    expect(markup).not.toContain("tool_condition=<b>timeout</b>");
  });

  it("still says every when every requested corner was compared", () => {
    // The fix is a distinction, not a blanket hedge.
    const out = text(<Divergence rows={[]}
                                 point={{ tool_condition: "timeout" }}
                                 bins={["tool_condition:timeout"]} />);
    expect(out).toContain("All 1 corners this run's point asked for were "
                          + "exhibited");
    expect(out).not.toContain("never compared");
  });

  it("draws an uncompared corner as an absence, never as a divergence row", () => {
    // A divergence row is a RESULT (we looked, it never came). A corner nothing
    // compared is the vacuity case, and the two may not share a treatment.
    const out = html(<Divergence rows={[]} point={POINT_5}
                                 bins={["data_condition:complete",
                                        "tool_condition:timeout"]} />);
    expect((out.match(/scn-diverge__row--uncompared/g) ?? []).length).toBe(3);
    expect(out).toContain("◌");          // the unrecorded glyph, not ◇
    expect(out).not.toMatch(NO_HEX);
  });

  it("counts a corner named by the divergence list as compared", () => {
    // The comparison has two halves; reading only the exhibited bins would
    // report the divergence rows themselves as never compared.
    const out = text(<Divergence rows={DIVERGED} bins={[]}
                                 point={{ tool_condition: "timeout" }} />);
    expect(out).toContain("asked for, never exhibited");
    expect(out).not.toContain("never compared");
  });

  it("reports uncompared corners under a divergence list that found something", () => {
    // `[...]` is just as silent about them, and "these diverged" reads as
    // "and the rest were fine".
    const markup = html(<Divergence rows={DIVERGED} bins={[]} point={POINT_5} />);
    expect(text(markup)).toContain("asked for, never exhibited");
    expect(text(markup)).toContain("never compared");
    expect(markup).toContain("intent=<b>complaint</b>");
  });

  it("does not claim every corner appeared when no point was stored", () => {
    const out = text(<Divergence rows={[]} point={{}} bins={[]} />);
    expect(out).toContain("records no stimulus point");
    expect(out).not.toContain("Every corner");
  });

  it("leaves the unrecorded state alone", () => {
    // `null` is nothing compared at all; listing corners under it would dress an
    // absent computation as a partial one.
    const out = text(<Divergence rows={null} point={POINT_5} bins={[]} />);
    expect(out).toContain("Divergence was not recorded");
    expect(out).not.toContain("never compared");
    expect(out).not.toContain("nothing diverged");
  });

  it("makes a whole read and a reduced one print different sentences", () => {
    // The acceptance criterion, stated directly.
    const whole = html(<Divergence rows={[]} point={{ tool_condition: "timeout" }}
                                   bins={["tool_condition:timeout"]} />);
    const reduced = html(<Divergence rows={[]}
                                     point={{ tool_condition: "timeout",
                                              intent: "complaint" }}
                                     bins={["tool_condition:timeout"]} />);
    expect(whole).not.toBe(reduced);
  });

  it("stands the run's facts beside the evidence without inventing a verdict", () => {
    // The rail is the most prominent thing on the screen, so it is the most
    // expensive place to overstate. A scenario run has no pass/fail — the
    // verdict lives on a scorecard, against assertions — and the page's own
    // closing section promises nothing here is computed from anything else.
    const markup = html(<ScenarioRunDetailView run={CONV} spans={[]} />);
    const t = text(markup);
    expect(t).toContain("What this run established");
    expect(t).toContain("does not pass or fail");
    expect(t.toLowerCase()).not.toContain("release blocked");
    for (const verdict of ["PASSED", "FAILED", "PASS RATE"]) {
      expect(t).not.toContain(verdict);
    }
  });

  it("says coverage gaps were NOT RECORDED rather than reporting zero", () => {
    // `null` and `[]` are different findings: nobody computed divergence, vs
    // it was computed and every corner appeared. "0 gaps" would claim the
    // second while meaning the first.
    const unrecorded = text(html(<ScenarioRunDetailView
      run={{ ...CONV, coverage: { measured: true, bins: [], divergence: null } }}
      spans={[]} />));
    expect(unrecorded).toContain("not recorded for this run");

    const measured = text(html(<ScenarioRunDetailView
      run={{ ...CONV, coverage: { measured: true, bins: [], divergence: [] } }}
      spans={[]} />));
    expect(measured).not.toContain("not recorded for this run");
  });

  it("reports an unchanged world as a result, not as an absence", () => {
    const still = text(html(<ScenarioRunDetailView
      run={{ ...CONV, derived: { ...CONV.derived, world_changed: false,
                                 n_changed_fields: 0 } }} spans={[]} />));
    expect(still).toContain("unchanged");
  });

  it("shows the steps in order, and shows nothing when the trace has none", () => {
    // The ledger groups tool calls by the gateway's verdict; it cannot show
    // what happened BETWEEN two calls. A run with a trace gets both. A run
    // without one must not grow an empty timeline that reads as "did nothing".
    const withTrace = html(<ScenarioRunDetailView run={CONV} spans={[
      { span_id: "a", kind: "llm_call", name: "plan",
        start_time: "2026-01-01T00:00:00Z", end_time: "2026-01-01T00:00:01Z" },
      { span_id: "b", kind: "tool_call", name: "lookup_order",
        start_time: "2026-01-01T00:00:02Z", end_time: "2026-01-01T00:00:02Z" },
    ]} />);
    expect(withTrace).toContain('aria-label="Trace timeline"');
    expect(withTrace.indexOf("plan")).toBeLessThan(withTrace.indexOf("lookup_order"));

    expect(html(<ScenarioRunDetailView run={CONV} spans={[]} />))
      .not.toContain('aria-label="Trace timeline"');
  });

  it("reaches the run's screen from the stored row, not from a prop nobody passes",
     () => {
       // `point` and `coverage.bins` are on the row already; the defect was that
       // the block was handed neither.
       const markup = html(<ScenarioRunDetailView
         run={{ ...CONV, coverage: { measured: true, bins: [], divergence: [] } }}
         spans={[]} />);
       expect(text(markup)).toContain("never compared");
       expect(markup).toContain("intent=<b>account_change</b>");
       expect(text(markup)).not.toContain("Every corner this run's point asked "
                                          + "for was exhibited");
     });
});

/* The wiring, on the screen a reader actually gets. `Divergence` is its own
 * block BESIDE the exhibited bins rather than inside them, because
 * `coverage.measured` speaks for `bins` alone: the two halves are collected by
 * different callers at different moments, and a divergence read nested under
 * the measured branch could be gated on a flag that never meant it. Out here
 * that mistake is not expressible — so what these tests pin is that the block
 * is on the page at all, and that a diverged corner is never drawn as an
 * exhibited one. */
const detail = (coverage: ScenarioRunDetail["coverage"]) =>
  html(<ScenarioRunDetailView run={{ ...CONV, coverage }} spans={[]} />);

describe("divergence sits beside the exhibited bins, never among them", () => {
  it("is on the run's screen even when no coverage was collected", () => {
    const out = text(detail({ measured: false, bins: null, divergence: DIVERGED }));
    expect(out).toContain("No coverage was collected");
    expect(out).toContain("asked for, never exhibited");
    expect(out).toContain("tool_condition");
  });

  it("says divergence was not recorded even on a measured run", () => {
    const out = text(detail({ measured: true, bins: ["action_risk:write"],
                              divergence: null }));
    expect(out).toContain("Divergence was not recorded");
    expect(out).not.toContain("nothing diverged");
  });

  it("keeps the two halves of one coverage read visibly apart", () => {
    const out = detail({ measured: true, bins: ["action_risk:write"],
                         divergence: DIVERGED });
    const [exhibited, asked] = out.split("Asked for, never exhibited</h3>");

    // the bins the run reached, in the bins treatment…
    expect(exhibited).toContain("scn-bins");
    expect(exhibited).toContain("<code>write</code>");
    // …and the corner it was asked for and never produced, in its own
    expect(exhibited).not.toContain("tool_condition");
    expect(asked).toContain("tool_condition");
    expect(asked).toContain("scn-diverge");
    expect(asked).not.toContain("scn-bins");
    expect(out).not.toMatch(NO_HEX);
  });

  it("still shows the divergence block on a run that carries no coverage", () => {
    // `coverage` absent entirely (an old payload) is not a licence to drop the
    // block: the reader is owed "nobody computed this", not silence.
    const out = text(html(<ScenarioRunDetailView
      run={{ ...CONV, coverage: undefined as unknown as ScenarioRunDetail["coverage"] }}
      spans={[]} />));
    expect(out).toContain("Asked for, never exhibited");
    expect(out).toContain("Divergence was not recorded");
  });
});

/* ------------------------------------------------------------- timestamps */

describe("formatCreated", () => {
  it("labels the stored instant UTC without re-reading it as local time", () => {
    // `created_at` carries no offset on SQLite; `new Date(...)` would shift it.
    expect(formatCreated("2026-07-30T20:20:14.841736")).toBe("2026-07-30 20:20:14 UTC");
    expect(formatCreated("2026-07-30T20:20:14")).toBe("2026-07-30 20:20:14 UTC");
  });

  it("shows an unrecognised value verbatim rather than guessing", () => {
    expect(formatCreated("yesterday")).toBe("yesterday");
    expect(formatCreated("")).toBe("—");
  });
});

/* ============================================================================
   TWO ZEROS — the empty store, and the query that matched nothing

   `rows.length === 0` was rendered unconditionally as `<ScenarioEmpty />`, so a
   filter naming an agent that has never run printed "No scenario run has been
   stored yet … the list is tenant-scoped, so a run stored under another
   workspace stays in that one" — a claim about the STORE, made for a number
   that measures a QUERY. It is the defect family this whole file exists to
   catch: an absence rendered as a result, printed on the one screen a reader
   comes to in order to find out how much evidence exists.

   Both ends of the same request already keep the two apart. The endpoint
   refuses to read an untouched field as a filter (`routes/scenarios.py:_filter`
   — "an absence dressed as a measurement, which is the one thing this surface
   exists not to do"), and the CLI prints a different sentence for a filtered
   zero (`cli.py:scenario_list_cmd` — "a zero UNDER A FILTER is a measurement").
   Measured against the same store on the same day, with 9 runs stored:

     $ python -m agenttic.cli scenario list --agent no-such-agent --config config.yaml
     No scenario run matches --agent no-such-agent — which is not the same claim
     as no run being stored. Drop the filter to see what is.

   The console said the opposite thing about the same nine runs.

   The pure-component assertions below are house style; the block after them is
   not, and has to be. Rendering `<ScenarioNoMatch />` directly proves the copy
   and proves nothing about WHICH of the two the page picks — which is exactly
   where the defect lived, and is only reachable through an effect and a form
   submit. So that block mounts the real page in a real DOM.
   ========================================================================== */

describe("the no-match state reports the query, not the store", () => {
  const Q = { agent_id: "no-such-agent", scenario_id: "" };

  it("names the filter it is reporting on", () => {
    const visible = text(html(<ScenarioNoMatch query={Q} />));
    // "nothing matched" is only a measurement if the reader can see what was
    // asked — and the match is exact, so a near-miss id is the likely cause.
    expect(visible).toContain("no-such-agent");
    expect(visible).toContain("No scenario run matches");
  });

  it("names both fields when both are set", () => {
    const visible = text(html(<ScenarioNoMatch
      query={{ agent_id: "demo-bot", scenario_id: "scn-4c1e9a" }} />));
    expect(visible).toContain("demo-bot");
    expect(visible).toContain("scn-4c1e9a");
  });

  it("makes no claim about the store", () => {
    const visible = text(html(<ScenarioNoMatch query={Q} />));
    // every sentence ScenarioEmpty is entitled to and this one is not
    expect(visible).not.toContain("has been stored yet");
    expect(visible).not.toContain("tenant-scoped");
    expect(visible).not.toContain("save_scenario_run");
    expect(visible).not.toContain("nothing ran");
    expect(visible).not.toMatch(/\bwrite was lost\b/);
    // and it says out loud which of the two claims this is
    expect(visible).toContain("not the same claim as no run being stored");
  });

  it("invents no run, and takes its colour from tokens", () => {
    const out = html(<ScenarioNoMatch query={Q} />);
    expect(out).not.toContain("<table");
    expect(out).not.toContain("<tr");
    expect(text(out)).not.toMatch(/\b\d+ fired\b/);
    expect(out).not.toMatch(NO_HEX);
  });

  /* The claim "the terminal answers this same question the same way" is only
   * worth printing if a reader can check it, so the command carries THIS
   * filter. Measured against the store on 2026-07-31, holding 9 runs:
   *
   *   $ python -m agenttic.cli scenario list --agent no-such-agent --config config.yaml
   *   No scenario run matches --agent no-such-agent — which is not the same
   *   claim as no run being stored. Drop the filter to see what is.
   */
  it("prints the same query for the terminal, filter and all", () => {
    const pre = (q: { agent_id: string; scenario_id: string }) =>
      html(<ScenarioNoMatch query={q} />)
        .match(/<pre[^>]*>([\s\S]*?)<\/pre>/)?.[1] ?? "";
    expect(pre(Q)).toBe("uv run agenttic scenario list --agent no-such-agent");
    expect(pre({ agent_id: "demo-bot", scenario_id: "scn-4c1e9a" }))
      .toBe("uv run agenttic scenario list --agent demo-bot"
            + " --scenario scn-4c1e9a");
  });

  it("quotes a filter value that is not a bare id", () => {
    // The field is free text. A command a reader is invited to paste must not
    // change meaning because someone typed a space or a semicolon into it.
    const out = html(<ScenarioNoMatch
      query={{ agent_id: "a; rm -rf /", scenario_id: "" }} />);
    const pasted = text(out.match(/<pre[^>]*>([\s\S]*?)<\/pre>/)?.[1] ?? "");
    expect(pasted).toBe("uv run agenttic scenario list --agent 'a; rm -rf /'");
  });
});

/* ------------------------------------------ the page, mounted in a real DOM */

// React's own flag for "act() is legitimate here". Without it every mount logs
// "the current testing environment is not configured to support act(...)", and
// a suite that prints warnings on a green run is a suite nobody reads.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

/** A real mount of the real page with `api.listScenarioRuns` stubbed to answer
 *  exactly what the endpoint answers. `serve` receives the query the page sent,
 *  so a filtered request can return the real `{"runs": [], "count": 0}` while an
 *  unfiltered one returns the rows the store holds. */
async function mountScenarios(
  serve: (q: { scenario_id?: string; agent_id?: string; limit?: number })
    => ScenarioRunRow[],
) {
  const calls: { scenario_id?: string; agent_id?: string; limit?: number }[] = [];
  vi.spyOn(api, "listScenarioRuns").mockImplementation(async (q = {}) => {
    calls.push(q);
    const runs = serve(q);
    return { runs, count: runs.length };
  });
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<ScenariosPage />); });

  const visible = () => (host.textContent ?? "").replace(/\s+/g, " ");
  const setValue = (el: HTMLInputElement, v: string) => {
    // React installs its own value setter on the instance; going through the
    // prototype's is what makes it see a real user edit.
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value")!.set!.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  };
  return {
    calls, visible,
    markup: () => host.innerHTML,
    async filterBy(field: "agent" | "scenario", value: string) {
      const el = host.querySelector<HTMLInputElement>(
        `input[aria-label="filter by ${field} id"]`)!;
      await act(async () => { setValue(el, value); });
      await act(async () => {
        host.querySelector("form")!.dispatchEvent(
          new Event("submit", { bubbles: true, cancelable: true }));
      });
    },
    async clickButton(label: string) {
      const btn = [...host.querySelectorAll("button")]
        .find((b) => (b.textContent ?? "").trim() === label);
      expect(btn, `no button labelled "${label}"`).toBeTruthy();
      await act(async () => {
        btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
    },
    fieldValue(field: "agent" | "scenario") {
      return host.querySelector<HTMLInputElement>(
        `input[aria-label="filter by ${field} id"]`)!.value;
    },
    unmount() { act(() => { root.unmount(); }); host.remove(); },
  };
}

/** One stored row, shaped like a real `/api/scenario-runs` element. */
const STORED: ScenarioRunRow = {
  run_id: "499b6a230e8b42ed8215d4612e86ef88", scenario_id: "scn-4c1e9a",
  agent_id: "demo-bot", trace_id: "499b6a230e8b42ed8215d4612e86ef88",
  space_ref: "conversational_transactional", space_fingerprint: "e3f1",
  seed: 7, created_at: "2026-07-30T20:20:14.841736", ended: "satisfied",
  conversational: true, world_changed: true, n_blocked: 0,
  faults: { recorded: true,
            counts: { planned: 1, fired: 1, skipped: 0, never_reached: 0 } },
};

describe("the page picks between the two zeros", () => {
  afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ""; });

  it("reports the query, not the store, when a filter matched nothing", async () => {
    // The store holds a run. The filter names an agent that never ran, and the
    // endpoint answers {"runs": [], "count": 0} — the same zero either way, and
    // the reason it cannot be read off `rows.length` alone.
    const page = await mountScenarios((q) => (q.agent_id ? [] : [STORED]));
    await page.filterBy("agent", "no-such-agent");

    expect(page.calls).toEqual([
      { limit: 100 }, { agent_id: "no-such-agent", limit: 100 },
    ]);
    const v = page.visible();
    expect(v).toContain("No scenario run matches");
    expect(v).toContain("no-such-agent");
    // THE DEFECT: the empty-store sentence, printed for a filtered zero
    expect(v).not.toContain("No scenario run has been stored yet");
    expect(v).not.toContain("tenant-scoped");
    page.unmount();
  });

  it("still reports the store when nothing is stored and nothing was asked", async () => {
    // The fix must not swap one wrong sentence for another: with no filter, a
    // zero IS a fact about the store and has to keep saying so.
    const page = await mountScenarios(() => []);
    const v = page.visible();
    expect(v).toContain("No scenario run has been stored yet");
    expect(v).not.toContain("No scenario run matches");
    expect(page.calls).toEqual([{ limit: 100 }]);
    page.unmount();
  });

  it("reads the submitted query, not what is being typed next", async () => {
    // `rows` answers the query that was SUBMITTED. Deciding off the input
    // fields would flip the sentence under a list nobody re-asked for.
    const page = await mountScenarios(() => []);
    const el = document.querySelector<HTMLInputElement>(
      'input[aria-label="filter by agent id"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value")!.set!.call(el, "demo-bot");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(page.visible()).toContain("No scenario run has been stored yet");
    expect(page.calls).toEqual([{ limit: 100 }]);
    page.unmount();
  });

  it("keeps the two apart for a scenario-id filter too", async () => {
    const page = await mountScenarios((q) => (q.scenario_id ? [] : [STORED]));
    await page.filterBy("scenario", "scn-never");
    const v = page.visible();
    expect(v).toContain("No scenario run matches");
    expect(v).toContain("scn-never");
    expect(v).not.toContain("has been stored yet");
    page.unmount();
  });

  it("drops the filter for real — the fields and the query both", async () => {
    // "Drop the filter to see what is" has to be actionable on the screen that
    // says it, and it has to re-ask WITHOUT the filter or the sentence is a
    // second false claim.
    const page = await mountScenarios((q) => (q.agent_id ? [] : [STORED]));
    await page.filterBy("agent", "no-such-agent");
    await page.clickButton("drop the filter");

    expect(page.calls).toEqual([
      { limit: 100 }, { agent_id: "no-such-agent", limit: 100 }, { limit: 100 },
    ]);
    expect(page.fieldValue("agent")).toBe("");
    expect(page.visible()).toContain("demo-bot");
    expect(page.visible()).not.toContain("No scenario run matches");
    page.unmount();
  });

  it("shows the rows when the filter matches, and neither zero", async () => {
    const page = await mountScenarios(() => [STORED]);
    await page.filterBy("agent", "demo-bot");
    const v = page.visible();
    expect(v).not.toContain("No scenario run matches");
    expect(v).not.toContain("No scenario run has been stored yet");
    expect(page.markup()).toContain("<table");
    page.unmount();
  });
});


/* ------------------------------------------------ a page is not the store */

describe("a capped list does not look like the whole store", () => {
  it("says nothing when the page came back short", () => {
    // A cap notice on a list that was not capped is the opposite error: it would
    // suggest missing evidence where there is none.
    expect(html(<ListCap n={3} limit={RUN_LIMIT} />)).toBe("");
    expect(html(<ListCap n={RUN_LIMIT - 1} limit={RUN_LIMIT} />)).toBe("");
  });

  it("says so when the page came back full", () => {
    // `GET /api/scenario-runs` returns `count = len(runs)` — the size of the
    // PAGE. Nothing in the response tells "there are exactly 100" from "there
    // are thousands and these are the newest 100", so a reader counting rows to
    // judge how much testing has happened gets the same number either way.
    const out = text(<ListCap n={RUN_LIMIT} limit={RUN_LIMIT} />);
    expect(out).toContain("newest");
    expect(out).toContain("100");
    expect(out).toContain("capped");
    expect(out).toContain("size of this page rather than of the store");
  });

  it("the page asks for exactly the number the notice reports", () => {
    // Two places have to agree or the notice is wrong about its own cause.
    const src = ScenariosPage.toString();
    expect(src).not.toContain("limit: 100");     // never inlined a second time
    expect(RUN_LIMIT).toBe(100);
  });
});

describe("an absent dimension is not a dimension that was tried and missed", () => {
  const COV = {
    measured: true,
    bins: ["trajectory:tool_then_answer"],
    divergence: null,
    model: null,
  };

  it("does not claim an absent dimension went unexercised", () => {
    // The list is built from COUNTABLE bins, so a not-measurable coverpoint and
    // a classifier-backed bin with no evaluator are both missing from it without
    // ever having been tested and found wanting. Saying they "were not exercised
    // by this run" reports missing measurement as a measured negative — the
    // inversion this whole surface exists to prevent.
    const out = text(<ExhibitedCoverage coverage={COV} />);
    expect(out).not.toContain("a dimension absent here was not exercised");
    expect(out).toContain("not exercised or not measured");
    expect(out).toContain("It cannot tell you which.");
  });

  it("still says the bins are not closure", () => {
    const out = text(<ExhibitedCoverage coverage={COV} />);
    expect(out).toContain("not closure");
  });
});
