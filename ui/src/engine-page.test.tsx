/* /engine — the scenario-engine explainer.
 *
 * This page argues that evidence must be checkable, so the tests that matter
 * are not "does it render". They are of two kinds:
 *
 * 1. THE QUOTES REPRODUCE. Three closed vocabularies on the page are quoted
 *    from the engine's source — the injector's evidence messages
 *    (`scenario/faults.py`), the counterparty's end reasons (`scenario/user.py`)
 *    and the honeypot outcomes and verdicts (`redteam/honeypot.py`). The tests
 *    below READ THOSE PYTHON FILES and fail if a literal has drifted, including
 *    the two integers on the page (30000ms, 30s), which are reconstructed from
 *    the source constants rather than trusted. A recorded number that no longer
 *    reproduces is a defect in this codebase, and a marketing page is the place
 *    it would rot unnoticed for longest.
 *
 * 2. THE ABSENCES STAY APART. Three things can be true of a live read and only
 *    one of them is a result: still loading, could not be read, and read with
 *    nothing in it. A page whose thesis is "unexercised is not pass" cannot
 *    render its own missing data as either a zero or a finding.
 *
 * House style: `renderToStaticMarkup`. There is no jsdom or testing-library
 * here, which is why every section is a pure function of its props.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { ScenarioRunRow } from "./api";
import { ScopeLine } from "./verification";
import {
  Dimensions, END_REASONS, EnginePage, FAULT_EVIDENCE, FaultBins,
  FaultEvidenceTable, HARNESS_VERDICTS, HONEYPOT_OUTCOMES, Limits,
  PropertyLayer, StoredRuns, UNSTAGED_BINS, evidenceFor, readCaps, unstagedNote,
  type CapsState, type EngineCaps, type RunsState,
} from "./pages/EnginePage";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
/** The visible words: markup stripped, entities decoded. An assertion about
 *  COPY must not be satisfiable by a class name that happens to contain the
 *  phrase, and must not have to know that an apostrophe renders `&#x27;`. */
const ENTITIES: Record<string, string> = {
  "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#x27;": "'",
  "&#39;": "'",
};
const text = (el: React.ReactElement) =>
  html(el)
    .replace(/<[^>]+>/g, " ")
    .replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (m) => ENTITIES[m])
    // stripping an inline <b> leaves two spaces mid-sentence; a copy assertion
    // should not have to know where the emphasis is.
    .replace(/\s+/g, " ");
const NO_HEX = /#[0-9a-fA-F]{3,8}\b/;   // tokens only, so both themes follow

/* ------------------------------------------------------------ the source ---
 * The engine's own files, loaded as text through Vite's `?raw` — this project
 * has no @types/node, so `fs` is not available to a test here and this is the
 * way in. If the tree is ever checked out without the Python package these
 * imports fail loudly rather than skipping, which is the behaviour an
 * unverifiable quote deserves. */
import FAULTS_PY_RAW from "../../src/agenttic/scenario/faults.py?raw";
import USER_PY_RAW from "../../src/agenttic/scenario/user.py?raw";
import HONEYPOT_PY_RAW from "../../src/agenttic/redteam/honeypot.py?raw";
/* The four files that ground what the page says about a bin the injector does
 * NOT stage, and about where the property layer actually runs. */
import EXTRACTORS_PY_RAW from "../../src/agenttic/coverage/extractors.py?raw";
import COV_MODEL_PY_RAW from "../../src/agenttic/coverage/model.py?raw";
import COLLECT_PY_RAW from "../../src/agenttic/coverage/collect.py?raw";
import CT_MODEL_PY_RAW
  from "../../src/agenttic/coverage/models/conversational_transactional.py?raw";
import CLI_PY_RAW from "../../src/agenttic/cli.py?raw";

const FAULTS_PY = String(FAULTS_PY_RAW);
const USER_PY = String(USER_PY_RAW);
const HONEYPOT_PY = String(HONEYPOT_PY_RAW);
const EXTRACTORS_PY = String(EXTRACTORS_PY_RAW);
const COV_MODEL_PY = String(COV_MODEL_PY_RAW);
const COLLECT_PY = String(COLLECT_PY_RAW);
const CT_MODEL_PY = String(CT_MODEL_PY_RAW);
const CLI_PY = String(CLI_PY_RAW);

/** Source with its line breaks flattened — a sentence a formatter wrapped is
 *  still the sentence the page quotes. */
const flat = (src: string) => src.replace(/\s+/g, " ");

/** Python splices adjacent string literals across lines:
 *      f"deadline exceeded: no response from {name} after "
 *      f"{TIMEOUT_MS}ms"
 *  is ONE string. Join those pairs so a message split for line length is still
 *  findable as the single message the environment actually writes. */
const spliced = (src: string) => src.replace(/"\s*\n?\s*f?"/g, "");

/** An `X = 30_000` style constant, as the integer it is. */
function intConst(src: string, name: string): number {
  const m = new RegExp(`^${name}\\s*[:=][^=\\n]*?=?\\s*([0-9_]+)\\s*$`, "m").exec(src);
  if (!m) throw new Error(`${name} not found in source`);
  return Number(m[1].replace(/_/g, ""));
}

describe("the quoted fault evidence reproduces from scenario/faults.py", () => {
  const joined = spliced(FAULTS_PY);
  const TIMEOUT_MS = intConst(FAULTS_PY, "TIMEOUT_MS");
  const RETRY_AFTER_S = intConst(FAULTS_PY, "RETRY_AFTER_S");

  it("uses the injector's own constants, not numbers typed on the page", () => {
    // The page prints "after 30000ms" and "retry after 30s". Both come from
    // here; if either constant moves, the page is wrong and this fails.
    expect(TIMEOUT_MS).toBe(30000);
    expect(RETRY_AFTER_S).toBe(30);
    expect(FAULT_EVIDENCE.timeout.error).toContain(`${TIMEOUT_MS}ms`);
    expect(FAULT_EVIDENCE.rate_limited.error).toContain(`${RETRY_AFTER_S}s`);
  });

  it("every quoted message is literally in the injector", () => {
    for (const [kind, e] of Object.entries(FAULT_EVIDENCE)) {
      if (e.error === null) continue;      // stale_data / malformed_response
      const source = e.error
        .replace("<tool>", "{name}")
        .replace(String(TIMEOUT_MS), "{TIMEOUT_MS}")
        .replace(`${RETRY_AFTER_S}s`, "{RETRY_AFTER_S}s");
      expect(joined, `${kind} message drifted from faults.py`).toContain(source);
    }
  });

  it("the two kinds the page says leave no error really leave none", () => {
    // apply_fault returns `error=None` for exactly these two, and the page's
    // whole point about them is that they do not announce themselves.
    expect(FAULT_EVIDENCE.stale_data.error).toBeNull();
    expect(FAULT_EVIDENCE.malformed_response.error).toBeNull();
    // ...and malformed_response is the ONE kind whose call is executed.
    const ran = Object.entries(FAULT_EVIDENCE).filter(([, e]) => e.ran);
    expect(ran.map(([k]) => k)).toEqual(["malformed_response"]);
  });

  it("a timeout carries no status code — the page must not invent a 504", () => {
    // Read the timeout branch of apply_fault itself rather than searching the
    // whole file: the module's docstring EXPLAINS that it refuses to stamp 504,
    // so a file-wide "must not contain 504" would fail on the prose that makes
    // the same point the page does.
    const branch = /if kind == "timeout":([\s\S]*?)\n {4}if kind == /.exec(FAULTS_PY);
    expect(branch, "the timeout branch was not found").toBeTruthy();
    expect(branch![1]).not.toContain("http.response.status_code");
    expect(branch![1]).toContain('"error.type": "timeout"');
    expect(FAULT_EVIDENCE.timeout.status).toBeNull();
    expect(text(<FaultEvidenceTable />)).toContain("no status code");
    // ...while the two kinds that DO carry a status still say so on the page.
    expect(FAULT_EVIDENCE.error_5xx.status).toBe("503");
    expect(FAULT_EVIDENCE.rate_limited.status).toBe("429");
  });

  it("covers exactly the injector's FAULT_KINDS — no more, no fewer", () => {
    const m = /FAULT_KINDS:[^=]*=\s*\(([^)]*)\)/.exec(FAULTS_PY);
    expect(m, "FAULT_KINDS tuple not found").toBeTruthy();
    // [a-z0-9_] and not [a-z_]: `error_5xx` carries digits, and a pattern that
    // cannot express it silently drops one kind and compares four against four.
    const kinds = [...m![1].matchAll(/"([a-z0-9_]+)"/g)].map((k) => k[1]);
    expect(kinds).toContain("error_5xx");
    expect(kinds.length).toBe(5);
    expect(Object.keys(FAULT_EVIDENCE).sort()).toEqual([...kinds].sort());
  });
});

describe("the other two quoted vocabularies reproduce", () => {
  it("END_REASONS matches scenario/user.py's EndReason", () => {
    const m = /EndReason\s*=\s*Literal\[([\s\S]*?)\]/.exec(USER_PY);
    expect(m, "EndReason literal not found").toBeTruthy();
    const reasons = [...m![1].matchAll(/"([a-z0-9_]+)"/g)].map((r) => r[1]);
    expect([...END_REASONS].sort()).toEqual([...reasons].sort());
    // the distinction the section is built on
    expect(END_REASONS).toContain("turn_cap");
    expect(END_REASONS).toContain("gave_up");
  });

  it("the honeypot outcomes and the three-valued verdict match honeypot.py", () => {
    for (const [name, value] of [
      ["RESISTED", "resisted"], ["ATTEMPTED_BLOCKED", "attempted_blocked"],
      ["EXECUTED_ALLOWED", "executed_allowed"],
    ] as const) {
      expect(HONEYPOT_PY).toContain(`${name} = "${value}"`);
    }
    expect(HONEYPOT_OUTCOMES.map((o) => o.id))
      .toEqual(["resisted", "attempted_blocked", "executed_allowed"]);

    for (const [name, value] of [
      ["HARNESS_ENFORCED", "ENFORCED"],
      ["HARNESS_NOT_ENFORCED", "NOT ENFORCED"],
      ["HARNESS_NOT_MEASURED", "NOT MEASURED"],
    ] as const) {
      expect(HONEYPOT_PY).toContain(`${name} = "${value}"`);
    }
    expect(HARNESS_VERDICTS.map((v) => v.id))
      .toEqual(["ENFORCED", "NOT ENFORCED", "NOT MEASURED"]);
  });

  it("keeps the two axes apart: resisted is about the model, not the harness", () => {
    const by = Object.fromEntries(HONEYPOT_OUTCOMES.map((o) => [o.id, o.about]));
    expect(by.resisted).toBe("the model");
    expect(by.attempted_blocked).toBe("the harness");
    expect(by.executed_allowed).toBe("the harness");
  });
});

/* ------------------------------------------------- exact-token discipline -- */

describe("evidenceFor matches whole bin ids, never fragments", () => {
  it("resolves the five staged kinds", () => {
    for (const k of Object.keys(FAULT_EVIDENCE)) {
      expect(evidenceFor(k)).not.toBeNull();
    }
  });

  it("does NOT match a bin that merely contains a kind's name", () => {
    // The failure family the last adversarial review found most of: "resolve"
    // inside a read verb, "log" inside "dialog". A bin called `timeout_retry`
    // would describe the AGENT retrying, not a staged timeout — claiming we
    // inject it would be claiming an injector that does not exist.
    for (const near of ["timeout_retry", "retry_timeout", "pre_timeout",
                        "error_5xx_or_4xx", "stale_data_v2", "malformed",
                        "malformed_response_body"]) {
      expect(evidenceFor(near), `${near} must not resolve`).toBeNull();
    }
  });

  it("is case-sensitive and whitespace-sensitive", () => {
    for (const near of ["Timeout", "TIMEOUT", " timeout", "timeout ", ""]) {
      expect(evidenceFor(near)).toBeNull();
    }
  });

  it("cannot be satisfied by an inherited property name", () => {
    for (const proto of ["constructor", "toString", "hasOwnProperty",
                         "__proto__", "valueOf"]) {
      expect(evidenceFor(proto), `${proto} must not resolve`).toBeNull();
    }
  });

  it("marks all_ok as a state, not something we do to the world", () => {
    expect(evidenceFor("all_ok")).toBeNull();
    const caps = capsWith([
      { ...DIM, id: "tool_condition",
        bins: ["all_ok", "timeout", "error_5xx", "rate_limited", "stale_data",
               "malformed_response"] },
    ]);
    const t = text(<FaultBins state={{ kind: "ok", caps }} />);
    expect(t).toContain("5 of the 6 conditions");
    expect(t).toContain("all_ok");
  });
});

/* --------------------------------------- what the injector does NOT stage -- */

describe("a bin the injector cannot stage is not thereby a benign one", () => {
  const tc = (bins: string[]) =>
    capsWith([{ ...DIM, id: "tool_condition", bins }]);
  const render = (bins: string[]) =>
    <FaultBins state={{ kind: "ok", caps: tc(bins) }} />;

  it("never prints the invented sentence again, in either number", () => {
    // THE DEFECT. `evidenceFor(b) === null` establishes exactly one thing: no
    // injector stages that bin. The page turned that into a positive claim
    // about the world — "the rest are states the world reaches by behaving" —
    // which is the opposite of the other possibility (a bin NOTHING in this
    // build produces) and the flattering one. On a page whose whole subject is
    // absence reported as a result, that was the defect wearing the page's own
    // argument as a hat.
    for (const bins of [["timeout", "all_ok"],
                        ["timeout", "all_ok", "partial_outage"],
                        ["timeout", "partial_outage"]]) {
      const t = text(render(bins));
      expect(t).not.toContain("nothing is done to it");
      expect(t).not.toContain("nothing is done to them");
      expect(t).not.toContain("The rest are states the world reaches by behaving");
      expect(t).not.toContain("The remaining one is a state the world reaches");
    }
  });

  it("says plainly that it cannot account for a bin it has no entry for", () => {
    const t = text(render(["timeout", "partial_outage"]));
    expect(t).toContain("partial_outage");
    expect(t).toContain("No injector stages it, and this page cannot say what does");
    expect(t).toContain("open question");
    // and it does NOT quietly claim the benign half of that disjunction
    expect(t).toContain("not a state the world reaches by behaving");
  });

  it("draws an unaccounted bin unlike an accounted one — texture, not wording", () => {
    const known = html(render(["timeout", "all_ok"]));
    const unknown = html(render(["timeout", "partial_outage"]));
    expect(known).toContain("eng-chip--off");
    expect(known).not.toContain("eng-chip--unknown");
    expect(known).not.toContain("is-unaccounted");
    expect(unknown).toContain("eng-chip--unknown");
    expect(unknown).toContain("is-unaccounted");
    expect(unknown).not.toMatch(NO_HEX);
    expect(known).not.toBe(unknown);
  });

  it("the all_ok account reproduces from the extractor that credits it", () => {
    // The page says all_ok needs a call to have been MADE and nothing done to
    // it. That is `_all_ok`: a non-empty tool list, none errored, none stamped.
    const m = /@predicate\("tool_all_ok"\)([\s\S]*?)@predicate\(/.exec(EXTRACTORS_PY);
    expect(m, "the tool_all_ok predicate was not found").toBeTruthy();
    expect(m![1]).toContain("bool(ts)");
    expect(m![1]).toContain("_errored(s)");
    expect(m![1]).toContain("_stamped_fault(s)");
    expect(UNSTAGED_BINS.all_ok).toContain("came back clean");
    expect(UNSTAGED_BINS.all_ok).toContain("carried no injector stamp");
    // ...and that the injector plans nothing for it is faults.py's own word
    expect(flat(FAULTS_PY))
      .toContain("a world that fails when nobody asked it to is a flaky fixture");
    expect(UNSTAGED_BINS.all_ok)
      .toContain("a world that fails when nobody asked it to is a flaky fixture");
  });

  it("the `other` account reproduces from the coverage model", () => {
    expect(flat(CT_MODEL_PY)).toContain(
      'OTHER = Bin(bin_id="other", label="unmodelled — a rising count is a finding")');
    expect(flat(COV_MODEL_PY))
      .toContain("not b.waived and b.bin_id != OTHER_BIN");
    expect(UNSTAGED_BINS.other).toContain("unmodelled catch-all");
    expect(UNSTAGED_BINS.other).toContain("a rising count in it is a finding");
    expect(UNSTAGED_BINS.other).toContain("held out of the closure denominator");
  });

  it("unstagedNote matches whole bin ids, never fragments", () => {
    for (const near of ["all_ok_v2", "ok", "all", "not_all_ok", "all_ok ",
                        " all_ok", "All_ok", "Other", "others", "other ", ""]) {
      expect(unstagedNote(near), `${near} must not resolve`).toBeNull();
    }
    for (const proto of ["constructor", "toString", "hasOwnProperty",
                         "__proto__", "valueOf"]) {
      expect(unstagedNote(proto), `${proto} must not resolve`).toBeNull();
    }
    expect(unstagedNote("all_ok")).not.toBeNull();
    expect(unstagedNote("other")).not.toBeNull();
  });

  it("keeps the two tables disjoint: nothing is both staged and unstaged", () => {
    for (const k of Object.keys(UNSTAGED_BINS)) expect(evidenceFor(k)).toBeNull();
    for (const k of Object.keys(FAULT_EVIDENCE)) expect(unstagedNote(k)).toBeNull();
  });

  it("on this tree's real bin list, the one unstaged bin is accounted for", () => {
    // GET /api/capabilities projects `bins` with `other` filtered out
    // (`server/routes/capabilities.py`), so `tool_condition` arrives as these
    // six: five staged, and `all_ok`. Nothing here is unaccounted for today —
    // the unaccounted branch exists so that a bin ADDED to the model, or the
    // `other` filter being dropped, does not silently inherit a benign story.
    const t = text(render(["all_ok", "timeout", "error_5xx", "rate_limited",
                           "stale_data", "malformed_response"]));
    expect(t).toContain("5 of the 6 conditions");
    expect(t).toContain("The world behaving, and it is not free");
    expect(t).not.toContain("No injector stages it, and this page cannot say");
    expect(html(render(["all_ok", "timeout", "error_5xx", "rate_limited",
                        "stale_data", "malformed_response"])))
      .not.toContain("is-unaccounted");
  });

  it("still counts the staged fraction off the LIVE bin list", () => {
    // the count is the one live number in this section and it stays live
    expect(text(render(["all_ok", "timeout", "error_5xx"])))
      .toContain("2 of the 3 conditions");
    expect(text(render(["timeout", "error_5xx"])))
      .toContain("Every condition it declares is one the injector stages");
  });
});

/* ------------------------------------------------------------- fixtures --- */

const DIM = {
  id: "trajectory", bins: ["happy_path"], description: "how the run went",
  measurable: true, not_measurable_reason: null, counts_toward_closure: true,
  provisional: false,
};
/** Shaped from the real GET /api/capabilities body on this tree: `session_shape`
 *  is genuinely `measurable: false` there, with that reason. */
const SESSION_SHAPE = {
  id: "session_shape", bins: ["single_turn", "multi_turn"],
  description: "single-turn, multi-turn, or resumed against prior memory",
  measurable: false,
  not_measurable_reason:
    "the run path a suite takes emits no `user_turn` span: a stored case is one "
    + "dict delivered once",
  counts_toward_closure: false, provisional: false,
};
function capsWith(coverpoints: EngineCaps["coverpoints"]): EngineCaps {
  return {
    baselineModel: "baseline-v3", baselineLimits: "structural dimensions only",
    appliesTo: "every run, automatically, with no model calls",
    coverpoints, provisionalDims: ["intent"], assertionsTotal: 8,
    notCovered: ["multi-agent interaction coverage"],
  };
}
const LOADING: CapsState = { kind: "loading" };
const UNREADABLE: CapsState = { kind: "unreadable", message: "502" };

/* ------------------------------------------------------- the vacuity rule -- */

describe("a dimension nothing can feed reads not_measurable, never 0%", () => {
  const t = text(<Dimensions state={{ kind: "ok",
                                      caps: capsWith([DIM, SESSION_SHAPE]) }} />);

  it("prints the token and the registry's reason", () => {
    expect(t).toContain("not_measurable");
    expect(t).toContain("emits no `user_turn` span");
    expect(t).toContain("outside closure");
  });

  it("prints no percentage for it — a 0% would invite someone to fix it", () => {
    expect(t).not.toMatch(/\d+\s?%/);
  });

  it("does not paint it with the measured dimensions", () => {
    const markup = html(<Dimensions state={{ kind: "ok",
                                             caps: capsWith([DIM, SESSION_SHAPE]) }} />);
    expect(markup).toContain("is-unmeasurable");
    expect(markup).not.toMatch(NO_HEX);
  });
});

describe("the three states of a live read never look alike", () => {
  it("loading is not 'could not be read'", () => {
    const t = text(<Dimensions state={LOADING} />);
    expect(t).toContain("Reading the coverage model");
    expect(t).not.toContain("could not be read");
    expect(html(<Dimensions state={LOADING} />)).toContain("eng-pending");
  });

  it("unreadable says so, and says it is not zero", () => {
    const t = text(<Dimensions state={UNREADABLE} />);
    expect(t).toContain("could not be read");
    expect(t).toContain("does not show zero of them");
    expect(html(<Dimensions state={UNREADABLE} />)).toContain("eng-absent");
  });

  it("read-but-empty is a RESULT and is styled as one", () => {
    const markup = html(<Dimensions state={{ kind: "ok", caps: capsWith([]) }} />);
    expect(markup).toContain("eng-none");
    expect(markup).not.toContain("eng-absent");
    expect(text(<Dimensions state={{ kind: "ok", caps: capsWith([]) }} />))
      .toContain("declares no dimensions");
  });
});

/* -------------------------------------------------------------- the limits - */

describe("the limits come from the deployment, verbatim and in full", () => {
  const NOT_COVERED = [
    "harness enforcement for an agent whose tool loop we do not run — the "
    + "honeypot battery works by planting a decoy tool",
    "earlier turns of a session, for most deterministic checks",
    "a simulated environment on the standard run path — a suite case is one "
    + "input dict handed to the agent once",
  ];
  const caps = { ...capsWith([DIM]), notCovered: NOT_COVERED };
  const t = text(<Limits state={{ kind: "ok", caps }} />);

  it("renders every item, unedited and unsummarised", () => {
    for (const n of NOT_COVERED) expect(t).toContain(n);
  });

  it("names the three the engine sections are bounded by", () => {
    expect(t).toContain("harness enforcement for an agent whose tool loop we do not run");
    expect(t).toContain("earlier turns of a session");
    expect(t).toContain("a simulated environment on the standard run path");
  });

  it("an unread list is not an empty one", () => {
    expect(text(<Limits state={UNREADABLE} />))
      .toContain("It is not empty; it is unread");
    expect(html(<Limits state={UNREADABLE} />)).toContain("eng-absent");
    // and a deployment that really declares none says THAT instead
    const none = { ...capsWith([DIM]), notCovered: [] };
    expect(text(<Limits state={{ kind: "ok", caps: none }} />))
      .toContain("declares no limits");
  });
});

/* --------------------------------------------------------- the stored runs - */

const ROW: ScenarioRunRow = {
  run_id: "28029c7068464938870d370ea9ed7a2e", scenario_id: "scn-refund",
  agent_id: "scenario-agent", trace_id: "28029c7068464938870d370ea9ed7a2e",
  space_ref: "retail-support-v1", space_fingerprint: "abc123", seed: 11,
  created_at: "2026-07-30T20:49:35.862922", ended: "",
  conversational: false, world_changed: false, n_blocked: 0,
  faults: { recorded: true,
            counts: { planned: 1, fired: 1, skipped: 0, never_reached: 0 } },
};
const NO_REPORT: ScenarioRunRow = {
  ...ROW, run_id: "549d5c29b7134bc5854a1baee3b68528",
  faults: { recorded: false, counts: null },
};

describe("the run section shows the reader's own runs or nothing", () => {
  it("draws no specimen when the reader is signed out", () => {
    const state: RunsState = { kind: "unauthenticated" };
    const markup = html(<StoredRuns state={state} />);
    expect(markup).toContain("eng-absent");
    expect(markup).not.toContain("eng-run");        // no fabricated row
    const t = text(<StoredRuns state={state} />);
    expect(t).toContain("not signed in");
    expect(t).toContain("It will not draw one either");
    expect(t).not.toMatch(/\b[0-9a-f]{32}\b/);      // and no invented run id
  });

  it("an empty workspace is a result, a failed read is not", () => {
    const empty = html(<StoredRuns state={{ kind: "runs", rows: [] }} />);
    expect(empty).toContain("eng-none");
    expect(empty).not.toContain("eng-absent");

    const broken = html(<StoredRuns state={{ kind: "unreadable", message: "502" }} />);
    expect(broken).toContain("eng-absent");
    expect(text(<StoredRuns state={{ kind: "unreadable", message: "502" }} />))
      .toContain("That is this page failing, not a statement about your runs");
  });

  it("renders real stored rows with their four fault facts kept apart", () => {
    const t = text(<StoredRuns state={{ kind: "runs", rows: [ROW] }} />);
    expect(t).toContain(ROW.run_id);
    expect(t).toContain("1 staged");
    expect(t).toContain("1 fired");
    expect(t).toContain("0 skipped");
    expect(t).toContain("0 never reached");
    expect(t).toContain("the world was not changed");
    expect(t).toContain("no call refused");
    expect(t).toContain("2026-07-30 20:49:35 UTC");  // shared formatCreated
  });

  it("a run with no fault report is not printed as zeroes", () => {
    const t = text(<StoredRuns state={{ kind: "runs", rows: [NO_REPORT] }} />);
    expect(t).toContain("no fault report was recorded");
    expect(t).toContain("not the same as nothing having been staged");
    expect(t).not.toContain("0 staged");
    expect(t).not.toContain("0 fired");
  });

  it("a report that would not rebuild is not a report nobody wrote", () => {
    // A real row off list_scenario_runs for a run whose stored plan names a
    // tool the world does not have: `_faults_view` returns recorded: true with
    // counts: null and a `problem`, and keeps that apart from recorded: false
    // on purpose. This row used to print it as "no fault report was recorded"
    // — denying a record that is in the payload, and saying the opposite of
    // what the run's own FaultLedger says one click away.
    const unreadable: ScenarioRunRow = {
      ...ROW, faults: { recorded: true, counts: null },
    };
    const t = text(<StoredRuns state={{ kind: "runs", rows: [unreadable] }} />);
    expect(t).toContain("a fault report was recorded for this run");
    expect(t).toContain("could not be read back");
    expect(t).toContain("not a count of zero");
    expect(t).not.toContain("no fault report was recorded");
    expect(t).not.toContain("no fault was staged");
    expect(t).not.toContain("0 staged");
    expect(t).not.toContain("0 fired");
    // three states, three treatments: not drawn as the unrecorded one either
    const markup = html(<StoredRuns state={{ kind: "runs", rows: [unreadable] }} />);
    expect(markup).toContain("is-unreadable");
    expect(markup).not.toContain("is-absent");
    expect(markup).not.toMatch(NO_HEX);
    expect(markup).not.toBe(
      html(<StoredRuns state={{ kind: "runs", rows: [NO_REPORT] }} />));
  });

  it("an empty PLAN is its own finding, not four null measurements", () => {
    // A real row off list_scenario_runs for an `all_ok` run: the report exists
    // and its plan was empty. Different from NO_REPORT above, and different
    // again from a plan that was staged and did nothing.
    const nothingStaged: ScenarioRunRow = {
      ...ROW, faults: { recorded: true,
                        counts: { planned: 0, fired: 0, skipped: 0, never_reached: 0 } },
    };
    const t = text(<StoredRuns state={{ kind: "runs", rows: [nothingStaged] }} />);
    expect(t).toContain("no fault was staged");
    expect(t).toContain("the world was left to behave");
    expect(t).not.toContain("0 staged");
    expect(t).not.toContain("no fault report was recorded");
    // and it is NOT drawn as the unrecorded state
    expect(html(<StoredRuns state={{ kind: "runs", rows: [nothingStaged] }} />))
      .not.toContain("is-absent");
  });

  it("a staged plan that never fired still shows all four counts", () => {
    const neverFired: ScenarioRunRow = {
      ...ROW, faults: { recorded: true,
                        counts: { planned: 1, fired: 0, skipped: 0, never_reached: 1 } },
    };
    const t = text(<StoredRuns state={{ kind: "runs", rows: [neverFired] }} />);
    expect(t).toContain("1 staged");
    expect(t).toContain("0 fired");
    expect(t).toContain("1 never reached");
    expect(t).not.toContain("no fault was staged");
  });

  it("does not report a blocked call as a faulted one, or vice versa", () => {
    const blocked: ScenarioRunRow = { ...ROW, n_blocked: 2 };
    const t = text(<StoredRuns state={{ kind: "runs", rows: [blocked] }} />);
    expect(t).toContain("2 refused by the gateway");
    expect(t).toContain("1 fired");                 // still its own fact
  });
});

/* ------------------------------------------------------------- readCaps ---- */

describe("readCaps refuses a payload it cannot render honestly", () => {
  const good = {
    coverage: {
      baseline: { model: "baseline-v3", limits: "L", applies_to: "every run",
                  coverpoints: [{ id: "trajectory", bins: ["a"], description: "d",
                                  measurable: true, not_measurable_reason: null,
                                  counts_toward_closure: true, provisional: false }] },
      fitted_example: { provisional: ["intent"] },
    },
    assertions: { total: 8 },
    not_covered: ["x"],
  };

  it("reads a well-formed body", () => {
    const c = readCaps(good);
    expect(c?.assertionsTotal).toBe(8);
    expect(c?.coverpoints[0].id).toBe("trajectory");
  });

  it("returns null rather than a partial when a field it renders is missing", () => {
    expect(readCaps(null)).toBeNull();
    expect(readCaps({})).toBeNull();
    expect(readCaps({ ...good, not_covered: undefined })).toBeNull();
    expect(readCaps({ ...good, assertions: {} })).toBeNull();
  });

  it("will not classify a coverpoint whose measurability is absent", () => {
    const cp = { id: "mystery", bins: [], description: "" };   // no `measurable`
    const bad = { ...good,
      coverage: { ...good.coverage,
        baseline: { ...good.coverage.baseline, coverpoints: [cp] } } };
    // Defaulting this to `true` would publish an unmeasurable dimension as a
    // measured one — the exact defect the landing's wheel once shipped.
    expect(readCaps(bad)).toBeNull();
  });
});

/* ------------------------------------------ claims made only where they hold */

describe("one exchange is an outcome, not a credited coverage bin", () => {
  const pageText = text(<MemoryRouter><EnginePage /></MemoryRouter>);

  it("no longer says a one-exchange run credits a single turn", () => {
    // `scenario/runner.py` still says "the run credits `single_turn` —
    // correctly", and that sentence predates the fix: the CLI now filters
    // stored bins through `countable()`/`exhibited()`, and `session_shape` is
    // declared measurable=False, so NOTHING credits a turn shape on any path.
    // A page that kept the old sentence would be crediting an uninstrumented
    // dimension as a measurement — the defect this whole page is about.
    expect(pageText).not.toContain("the run credits a single turn");
    expect(pageText).toContain("What the run does not do is credit a coverage bin");
    expect(pageText).toContain("no countable bins at all");
  });

  it("pins that to collect.py: a not-measurable coverpoint counts nothing", () => {
    // The CRITERION is unchanged — a not-measurable coverpoint contributes no
    // countable bin, so it can never be credited. Where the rule LIVES moved:
    // `countable()` used to open with `if not self.measurable: return []` and
    // now delegates to `uncountable_reason()`, the single statement of the
    // exclusion rule, because `divergence()` had restated a subset of the
    // conditions and invented rows for bins nothing had evaluated.
    //
    // So this pins the rule at its new home, and pins it HARDER than before:
    // it is no longer enough that countable() mentions measurability — the
    // exclusion must be asked through the one method every other list uses, so
    // closure, unhit, holes and divergence cannot drift apart again.
    const r = /def uncountable_reason\(self[\s\S]*?\n {4}def /.exec(COLLECT_PY);
    expect(r, "CoverpointCoverage.uncountable_reason was not found").toBeTruthy();
    expect(r![0]).toContain("if not self.measurable:");
    expect(r![0]).toContain("not measurable");

    const m = /def countable\(self\)[\s\S]*?\n {4}@property/.exec(COLLECT_PY);
    expect(m, "CoverpointCoverage.countable was not found").toBeTruthy();
    // countable() is DEFINED as "the bins uncountable_reason() returns nothing
    // for" — it must not re-implement any part of the test itself.
    expect(m![0]).toContain("uncountable_reason");
  });

  it("does not promise the counterparty always decides the ending", () => {
    // turn_cap is OUR ceiling and it really does end runs, so "the run ends
    // when the counterparty decides it has ended — not when a turn budget runs
    // out" collapsed two endings the closed list below it keeps apart.
    expect(pageText).not.toContain("not when a turn budget runs out");
    expect(pageText).toContain("on our own turn ceiling instead");
    expect(pageText).toContain("recorded separately");
  });
});

describe("the property layer is claimed only on the paths it runs on", () => {
  const caps = capsWith([DIM]);
  const t = text(<PropertyLayer state={{ kind: "ok", caps }} />);

  it("does not say 'every run' — the scenario command evaluates no property", () => {
    const m = /@scenario_app\.command\("run"\)([\s\S]*?)@scenario_app\.command\(/
      .exec(CLI_PY);
    expect(m, "the `scenario run` command was not found in cli.py").toBeTruthy();
    expect(m![1]).toContain("from agenttic.coverage.collect import");
    expect(m![1]).not.toContain("assertions");
    expect(m![1]).not.toContain("verify_op");

    expect(t).toContain("watched on every scored run");
    expect(t).toContain("evaluates no property");
    expect(t).not.toContain("are watched on every run here");
  });

  it("counts provisional dimensions, and does not call them what it cannot", () => {
    // The payload carries `fitted_example.provisional` and NOT how many
    // dimensions a fitted model adds. The page used to print the first number
    // under the second description; here they coincide, which is exactly how a
    // wrong derivation survives.
    expect(t).toContain("marks 1 of its own dimensions");
    expect(t).toContain("intent");
    expect(t).not.toContain("further dimensions");
    expect(t).toContain("is not stated here");
  });

  it("an empty provisional list is a measured answer, drawn as one", () => {
    const none = { ...capsWith([DIM]), provisionalDims: [] };
    const markup = html(<PropertyLayer state={{ kind: "ok", caps: none }} />);
    expect(markup).toContain("eng-none");
    expect(markup).not.toContain("eng-absent");
    expect(text(<PropertyLayer state={{ kind: "ok", caps: none }} />))
      .toContain("no dimension provisional");
  });

  it("says nothing at all when the payload could not be read", () => {
    // Dimensions, immediately above it in the same section, already reports
    // that. A zero here would be a measurement nobody took.
    expect(html(<PropertyLayer state={UNREADABLE} />)).toBe("");
    expect(html(<PropertyLayer state={LOADING} />)).toBe("");
  });
});

/* ----------------------------------------------------------- the whole page */

describe("the page as a whole", () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter><EnginePage /></MemoryRouter>);

  it("makes all five arguments", () => {
    for (const claim of [
      "It cannot answer",                       // 1 · closure over pass rate
      "Unexercised is not pass",                // 2 · vacuity
      "We inject the failure and check the recovery path",  // 3 · faults
      "does not volunteer the thing you need",  // 4 · counterparty
      "Whether the framework enforces is a separate question", // 5 · harness
    ]) {
      expect(markup).toContain(claim);
    }
  });

  it("renders the console's OWN unscoped line, so the two cannot drift", () => {
    // Not a copy of the sentence: the shared component is mounted, so an edit
    // in verification.tsx reaches this page and a paraphrase here would fail.
    const pageText = markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    const consoleLine = text(<ScopeLine sc={{}} />).trim();
    expect(consoleLine.length).toBeGreaterThan(40);
    expect(pageText).toContain(consoleLine);
    expect(markup).toContain("scope-line unscoped");
  });

  it("states its limits on the same page as its claims", () => {
    expect(markup).toContain("Where this stops.");
    expect(markup).toContain("What this still cannot test");
    // a black-box agent's tool loop: named twice, in both sections it bounds
    expect(markup).toContain("cannot be fault-injected");
    expect(markup).toContain("nowhere to plant it");
  });

  it("shows nothing but pending state before the reads come back", () => {
    // renderToStaticMarkup runs no effects, so this IS the first paint. It must
    // not yet claim anything could not be read, and must not show a run.
    expect(markup).toContain("eng-pending");
    expect(markup).not.toContain("eng-absent");
    expect(markup).not.toContain("eng-run__id");
  });

  it("carries no raw hex and no console chrome", () => {
    expect(markup).not.toMatch(NO_HEX);
    expect(markup).not.toContain("app-shell");
  });

  it("links into the console and the CLI instead of illustrating a run", () => {
    expect(markup).toContain("/app/scenarios");
    expect(markup).toContain("agenttic scenario run");
    expect(markup).toContain("agenttic scenario transcript");
  });
});
