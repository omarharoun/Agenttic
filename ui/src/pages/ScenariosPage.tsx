import { useEffect, useState } from "react";
import {
  api, type CoverageDivergence, type ScenarioFault, type ScenarioFaults,
  type ScenarioRunDetail, type ScenarioRunRow, type TranscriptEntry,
} from "../api";
import { EmptyState, PageHeader, RawToggle, Skeleton } from "../components/ui";
import "./ScenariosPage.css";

/* ============================================================================
   Scenario runs — the console surface for the scenario engine.

   The engine has been able to drive a stateful world, refuse a call at the
   gateway, stage a fault on a named tool at a named call index and hold a
   conversation with a counterparty that withholds facts until asked. None of it
   was visible: `Registry.save_scenario_run` made a run durable and
   `/api/scenario-runs` served it, and this is the first screen that reads it.

   Everything below is stored evidence or is derived by the registry from stored
   evidence. This page computes two things of its own and both are presentation:
   how a coverage bin id splits on its colon, and how an ISO timestamp is
   spaced out. There is nowhere here for a number to be invented.

   THE THREE ABSENCES, which this file exists to keep apart
   -------------------------------------------------------
   * `faults.recorded === false` — this run stored NO fault report. Its four
     lists are null, not []. "Nobody wrote down what was staged" is not "nothing
     was staged", and a run with an empty plan (recorded, counts all zero) is a
     third thing again. `recorded: true` with `counts: null` is a FOURTH: the
     report is stored and would not rebuild on read, so the lists are shown and
     the derivation is reported as unavailable. Never "no report" — the report
     is right there in the payload.
   * `coverage.measured === false` — no coverage was collected, `bins` is null.
     A run whose bins were collected and came back empty is `measured: true,
     bins: []` and reads differently.
   * `derived.n_user_turns === null` — the turn count comes off the trace and the
     trace could not be read. Zero would be a measurement.

   And the four fault lists are four different facts. Only `fired` is a thing
   that happened to the run; `skipped` reached its call and could not happen;
   `never_reached` was staged on a call the agent never made. Merging any two of
   them is the same defect as merging `resisted` and `attempted_blocked` in a
   honeypot battery — it turns "we never found out" into "it was fine".

   Dropping one is the same defect wearing a different hat, and it is the one
   this file got wrong first: with `never_reached: null` the ledger mapped three
   lists that were all empty and drew a legend over NO ROWS, discarding the
   stored plan the registry had gone out of its way to hand back untouched. A
   plan entry no list accounts for is now its own row state (`staged`), which
   says what is stored and nothing about whether the call was reached.
   ========================================================================== */

/* -------------------------------------------------------------------------- */
/* small shared bits                                                          */
/* -------------------------------------------------------------------------- */

function Section({ eyebrow, title, sub, children }: {
  eyebrow?: string; title: string; sub?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="scn-sec">
      {eyebrow && <div className="scn-eyebrow">{eyebrow}</div>}
      <h3>{title}</h3>
      {sub && <p className="scn-sub">{sub}</p>}
      {children}
    </section>
  );
}

/** A MEASURED absence: the run was watched and the thing did not happen. */
function Nothing({ children }: { children: React.ReactNode }) {
  return <div className="scn-none">{children}</div>;
}

/** An UNRECORDED absence: nobody wrote it down, so there is no result. Styled
 *  unlike {@link Nothing} on purpose — the vacuity rule is that these two must
 *  never be mistaken for each other. */
function NotRecorded({ children }: { children: React.ReactNode }) {
  return <div className="scn-absent">{children}</div>;
}

/** `created_at` is ISO-8601 in UTC and carries no offset suffix on SQLite, so
 *  `new Date(...)` would read it as LOCAL time and print an hour that never
 *  happened. It is spaced out as text instead, and labelled with the zone the
 *  backend documents. A string that is not that shape is shown verbatim rather
 *  than guessed at. */
export function formatCreated(iso: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/.exec(iso || "");
  return m ? `${m[1]} ${m[2]} UTC` : (iso || "—");
}

/** A stored value as text, keeping the type visible: `""` and `0` and `false`
 *  are all real prior states of a field and none of them may render as blank.
 *  `undefined` (the key was absent) is the one case that is not a value. */
function show(v: unknown): string {
  if (v === undefined) return "—";
  const s = JSON.stringify(v);
  return s === undefined ? String(v) : s;   // e.g. a function/symbol
}

/** A value as prose: a string as itself, anything else as JSON. `String(obj)`
 *  yields "[object Object]", which is a field silently discarded in front of
 *  the reader — the one thing this codebase treats as worse than an error. */
function plain(v: unknown): string {
  return typeof v === "string" ? v : show(v);
}

/* -------------------------------------------------------------------------- */
/* 1. WHAT WAS ASKED — the stimulus                                           */
/* -------------------------------------------------------------------------- */

export function Stimulus({ run }: { run: ScenarioRunDetail }) {
  const point = Object.entries(run.point ?? {});
  return (
    <Section eyebrow="1 · What was asked" title="The stimulus"
      sub="The ticket the agent received, the abstract point of the stimulus space it was drawn from, and the seed that drew it. Same space, same point, same seed — same ticket.">
      {run.ticket
        ? <blockquote className="scn-ticket">{run.ticket}</blockquote>
        : <NotRecorded>This run stored <b>no ticket text</b>. The scenario it
            names can still be identified by its point and seed below.</NotRecorded>}

      {point.length === 0 ? (
        <NotRecorded>No <b>abstract point</b> was stored, so which bins this
          ticket was drawn from is not recoverable from this run.</NotRecorded>
      ) : (
        <div className="scn-point">
          {point.map(([k, v]) => (
            <span className="scn-bin" key={k}>
              <span className="scn-bin__k">{k.replace(/_/g, " ")}</span>
              <span className="scn-bin__v">{String(v)}</span>
            </span>
          ))}
        </div>
      )}

      <div className="scn-meta">
        <div><span className="scn-meta__k">seed</span>
             <span className="scn-meta__v">{run.seed}</span></div>
        <div><span className="scn-meta__k">scenario</span>
             <span className="scn-meta__v">{run.scenario_id || "—"}</span></div>
        <div><span className="scn-meta__k">space</span>
             <span className="scn-meta__v">{run.space_ref || "—"}</span></div>
        <div><span className="scn-meta__k">space fingerprint</span>
             <span className="scn-meta__v">{run.space_fingerprint || "—"}</span></div>
        <div><span className="scn-meta__k">scenario sha256</span>
             <span className="scn-meta__v">{run.derived?.content_sha256 || "—"}</span></div>
      </div>
    </Section>
  );
}

/* -------------------------------------------------------------------------- */
/* 2. WHAT HAPPENED — the transcript                                          */
/* -------------------------------------------------------------------------- */

/** Whether this line handed a gated fact over. The backend derives
 *  `revealed_fact` from the `discloses` key (the key IS the evidence); the
 *  fallback covers a payload written before that flag existed and never
 *  invents one. */
function revealed(t: TranscriptEntry): boolean {
  return t.revealed_fact ?? ((t.discloses ?? "") !== "");
}

/** `delivered` is a fact about a COUNTERPARTY turn: false exactly for the
 *  closing line, which the agent was never handed. An agent line has no such
 *  field, and `!t.delivered` on `undefined` would mark every agent reply as
 *  undelivered — so this tests for the explicit false. */
function undelivered(t: TranscriptEntry): boolean {
  return t.delivered === false;
}

export function Transcript({ run }: { run: ScenarioRunDetail }) {
  const turns = run.transcript ?? [];
  const conv = run.derived?.conversational ?? !!run.session_id;
  const nTurns = run.derived?.n_user_turns;
  // A single-shot run had no counterparty, so no turn was ever APPLICABLE. The
  // footer used to print "Counterparty turns: 0 (counted off the trace…)"
  // directly under "there was no conversation" — a hard zero, sourced and
  // qualified like a reading, for a quantity nothing could have measured. `cli.py`
  // does not print one either (`_render_conversation` returns at the single-shot
  // line). Not-applicable is a third state beside counted and uncounted.
  //
  // …unless the record contradicts itself. `conversational` and `n_user_turns`
  // are derived from two different things (a stored session id and a count of
  // `user_turn` spans on the trace), so they CAN disagree, and the disagreement
  // is the finding. It is reported as one rather than resolved in favour of
  // either half.
  const strayTurns = !conv && typeof nTurns === "number" && nTurns > 0;

  return (
    <>
      <h4>Transcript</h4>
      {!conv ? (
        <Nothing><b>Single-shot run.</b> The ticket went in as one message and
          the agent answered once — there was no conversation, so there is no
          transcript. Nothing was withheld from this agent and nothing had to be
          asked for.</Nothing>
      ) : turns.length === 0 ? (
        <NotRecorded>This run carries a session id but <b>no transcript was
          stored</b>. What was said is not recoverable from this record.</NotRecorded>
      ) : (
        <div className="scn-turns">
          {turns.map((t, i) => {
            const user = t.speaker === "user";
            const gone = undelivered(t);
            return (
              <div key={i}
                   className={`scn-turn scn-turn--${user ? "user" : "agent"}`
                     + (gone ? " is-undelivered" : "")}>
                <div className="scn-turn__head">
                  <span className="scn-who">{user ? "customer" : (t.speaker || "agent")}</span>
                  {t.kind && <span className="scn-kind">{t.kind}</span>}
                  {revealed(t) && (
                    <span className="scn-reveal" title={
                      "The simulated counterparty handed over a gated fact on this "
                      + "turn. It withholds until asked correctly, so an agent that "
                      + "never asks is never given this."}>
                      revealed <code>{t.discloses}</code>
                    </span>
                  )}
                  {gone && (
                    <span className="scn-undelivered" title={
                      "The closing line, spoken after the agent's last answer. It "
                      + "was never handed to the agent, so it is not a message the "
                      + "agent ignored."}>
                      not delivered
                    </span>
                  )}
                </div>
                <div className={`scn-turn__text${t.text ? "" : " is-silent"}`}>
                  {t.text || "(the agent said nothing on this turn)"}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="scn-sub">
        {!conv
          ? (strayTurns
            ? `No conversation was held, and yet the trace carries ${nTurns} `
              + "counterparty turn(s). The record disagrees with itself, so "
              + "neither half is reported here as a result."
            : "Counterparty turns: not applicable — nothing drove a conversation, "
              + "so there was never a turn to count. That is not a count of zero.")
          : nTurns == null
            ? "Counterparty turns: not counted — the trace this count comes from "
              + "could not be read. That is not zero."
            : `Counterparty turns: ${nTurns} (counted off the trace, not off the `
              + "transcript). "}
        {run.ended ? `The session ended “${run.ended}”.` : ""}
      </p>
    </>
  );
}

/** What the counterparty gave up, and what it still holds. This is the
 *  measurement of whether the agent asked well, and it only exists for a run
 *  that had a counterparty at all.
 *
 *  FOUR states, and only one of them is the green sentence:
 *
 *  * **no conversation** — nothing was ever withheld from this agent, so there
 *    is no heading at all.
 *  * **nothing was gated** — the check NEVER RAN. `derived.elicitation_complete`
 *    is recomputed by `SimulatedSession.completed`, which is *satisfied AND
 *    nothing still withheld* (`scenario/user.py`), so a run that gated nothing
 *    is `true` BY CONSTRUCTION. This block used to render that vacuous `true` as
 *    "The counterparty left satisfied with nothing still withheld." — the M40
 *    vacuity rule inverted, a check that never ran printed as a check that
 *    passed. It is the common case and not a corner: `scenario/user.py` drops a
 *    fact already stated in the opening ticket, and `realize()` puts the order
 *    id into most templates, so most intents gate on nothing. So this branch
 *    RETURNS, exactly as `cli.py:_render_conversation` returns before its own
 *    completeness branch — the two surfaces must not say opposite things about
 *    the same run id.
 *  * **gated, completeness not recorded** — `null` on a run that DID gate. The
 *    verdict is unknown, which is not complete, and it is drawn in the
 *    unrecorded vocabulary rather than rendered as no sentence at all.
 *  * **gated, and recorded** — the one real result, pass or fail.
 *
 *  Whether anything was gated is read off the run's OWN record: the gate set is
 *  exactly `disclosed` ∪ `withheld` (`converse()` writes `withheld = held −
 *  disclosed`), so both lists empty is the counterparty having held nothing
 *  back. That is the same test `cli.py` makes, off the same stored row. */
export function Elicitation({ run }: { run: ScenarioRunDetail }) {
  const { disclosed = [], withheld = [] } = run.elicitation ?? {};
  const complete = run.derived?.elicitation_complete;
  // A single-shot run elicited nothing because it was never asked to; an
  // "elicitation" heading over two empty lists would read as a measurement.
  if (!(run.derived?.conversational ?? !!run.session_id)) return null;

  // NOT EXERCISED. No verdict may be printed under this — see the note above.
  if (disclosed.length === 0 && withheld.length === 0) {
    return (
      <>
        <h4>Elicitation</h4>
        <Nothing>This scenario <b>gated no facts</b>. There was nothing the
          agent had to ask for, so nothing here measures whether it would have —
          and nothing here could have failed. <b>Not a pass: the check never
          ran.</b> (Why a declared hidden fact does not gate is under
          disclosures.)</Nothing>
      </>
    );
  }

  return (
    <>
      <h4>Elicitation</h4>
      <div className="scn-bins">
        <div className="scn-bins__dim">
          <span className="scn-bins__k">disclosed</span>
          {disclosed.length
            ? disclosed.map((k) => <code key={k}>{k}</code>)
            : <span className="scn-why">none — the agent asked for nothing it was
                holding</span>}
        </div>
        <div className="scn-bins__dim">
          <span className="scn-bins__k">still withheld</span>
          {withheld.length
            ? withheld.map((k) => <code key={k}>{k}</code>)
            : <span className="scn-why">none</span>}
        </div>
      </div>
      {complete == null ? (
        <NotRecorded>This run gated {disclosed.length + withheld.length} fact(s)
          and stored <b>no completeness verdict</b>. Whether the agent elicited
          them all is unknown for this run, which is not the same as
          complete.</NotRecorded>
      ) : (
        <p className="scn-sub">
          {complete
            ? "The counterparty left satisfied with nothing still withheld."
            : "The counterparty did NOT leave satisfied, or is still holding a "
              + "fact the agent never asked for."}
        </p>
      )}
    </>
  );
}

/** Escalations and confirmations the agent raised. Each carries its own `kind`;
 *  the payload is rendered from whatever keys it has rather than from an
 *  assumed shape, so a kind this page has never seen is still shown. */
export function Interactions({ run }: { run: ScenarioRunDetail }) {
  const items = run.interactions ?? [];
  if (items.length === 0) return null;
  return (
    <>
      <h4>What the agent raised</h4>
      <ul className="scn-disc">
        {items.map((it, i) => (
          <li key={i}>
            <b>{plain(it.kind ?? "interaction")}</b>{" "}
            {Object.entries(it)
              .filter(([k]) => k !== "kind")
              .map(([k, v]) => `${k}: ${plain(v)}`)
              .join(" · ")}
          </li>
        ))}
      </ul>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* 3. WHAT THE WORLD DID — enforcement, faults, state                         */
/* -------------------------------------------------------------------------- */

/** The enforcement vocabulary the environment actually writes onto a tool span
 *  (`scenario/env.py`). Matched by EXACT equality against this frozen set — a
 *  substring test would let `not_blocked` read as `blocked`, and an unknown
 *  value must fall through to "not recorded" rather than to a verdict. */
const ENFORCEMENT = ["executed", "blocked", "faulted"] as const;
type Enforcement = (typeof ENFORCEMENT)[number] | "unrecorded";

export interface TraceSpan {
  span_id?: string;
  kind?: string;
  name?: string;
  error?: string | null;
  attributes?: Record<string, unknown>;
}

export function enforcementOf(span: TraceSpan): Enforcement {
  const v = span?.attributes?.enforcement;
  return (ENFORCEMENT as readonly string[]).includes(v as string)
    ? (v as Enforcement) : "unrecorded";
}

const ENF_TITLE: Record<Enforcement, string> = {
  executed: "The gateway allowed this call and the world ran it.",
  blocked: "The gateway refused this call, or the tool was never a candidate to "
    + "run. Nothing reached the world.",
  faulted: "Allowed by the gateway, then the staged fault replaced what the tool "
    + "would have returned.",
  unrecorded: "This span carries no enforcement attribute. That is not the same "
    + "as allowed — nothing was recorded either way.",
};

/** The calls the agent attempted, with what the gateway ruled on each.
 *
 *  Read off the TRACE, because the gateway's verdict is written onto the span
 *  and the run record stores only the names it refused. When the trace cannot be
 *  read the stored names are still shown, labelled as the partial evidence they
 *  are — a silently empty ledger would read as "the agent called nothing". */
export function ToolLedger({ spans, blocked, traceProblem, pending = false }: {
  spans: TraceSpan[] | null; blocked: string[]; traceProblem?: string;
  /** the trace request is still in flight — a fourth state, and not a finding.
   *  Without it the in-flight window renders as "the trace could not be read",
   *  which is a claim about a request that has not come back yet. */
  pending?: boolean;
}) {
  const calls = (spans ?? []).filter((s) => s.kind === "tool_call");

  if (pending && spans == null) {
    return (
      <>
        <h4>Tool calls</h4>
        <p className="scn-sub" aria-busy="true">Reading the trace…</p>
      </>
    );
  }

  if (spans == null) {
    return (
      <>
        <h4>Tool calls</h4>
        <NotRecorded>
          The trace holding this run&apos;s calls <b>could not be read</b>
          {traceProblem ? ` (${traceProblem})` : ""}, so what the gateway ruled
          on each call is not available here. Nothing on this screen should be
          read as &ldquo;the agent called nothing&rdquo;.
        </NotRecorded>
        {blocked.length > 0 && (
          <p className="scn-sub">
            The run record does name the tools the gateway refused:{" "}
            {blocked.map((b) => <code key={b}>{b}</code>)}
          </p>
        )}
      </>
    );
  }

  if (calls.length === 0) {
    return (
      <>
        <h4>Tool calls</h4>
        <Nothing>The agent <b>called no tools</b>. The trace was read and holds
          no tool call — it answered, escalated or gave up without touching the
          world.</Nothing>
      </>
    );
  }

  return (
    <>
      <h4>Tool calls</h4>
      <div className="scn-scroll">
        <table className="scn-tbl">
          <thead>
            <tr><th>#</th><th>tool</th><th>enforcement</th><th>fault</th>
                <th>what came back</th></tr>
          </thead>
          <tbody>
            {calls.map((s, i) => {
              const enf = enforcementOf(s);
              const attrs = s.attributes ?? {};
              const fault = attrs.injected_fault;
              const action = attrs.decision_action;
              const evidence = Array.isArray(attrs.decision_evidence)
                ? (attrs.decision_evidence as unknown[]).map(String) : [];
              return (
                <tr key={s.span_id ?? i}>
                  <td className="num">{i + 1}</td>
                  <td className="mono">{s.name}</td>
                  <td>
                    <span className={`scn-enf scn-enf--${enf}`} title={ENF_TITLE[enf]}>
                      {enf === "unrecorded" ? "not recorded" : enf}
                    </span>
                    {action != null && (
                      <div className="scn-why">gateway: {String(action)}
                        {evidence.length ? ` — ${evidence.join(", ")}` : ""}</div>
                    )}
                  </td>
                  <td className="mono">
                    {fault == null
                      ? <span className="scn-why">—</span>
                      : <span className="scn-enf scn-enf--faulted">{String(fault)}</span>}
                  </td>
                  <td>
                    {s.error
                      ? <span className="scn-why">{s.error}</span>
                      : <span className="scn-why">no error</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="scn-sub">
        <em>executed</em> — the gateway allowed it and the world ran it.{" "}
        <em>blocked</em> — nothing reached the world.{" "}
        <em>faulted</em> — allowed, then the staged fault replaced the result.{" "}
        <em>not recorded</em> — the span carries no verdict, which is not a
        verdict.
      </p>
    </>
  );
}

/* The legend copy is DEFINITIONAL, never assertive. "fired — it happened to
 * this run" printed beside a report where nothing fired would be a sentence
 * claiming the opposite of the evidence above it. */
const FAULT_STATES = {
  /* `staged` is the FIFTH thing and exists only because a report can fail to
   * reconstruct. When the registry cannot rebuild the plan it returns
   * `never_reached: null` and the stored lists untouched — so a fault that is
   * in the plan and that no stored event mentions has an outcome nobody
   * derived. It is NOT "never reached": that is the derivation the registry
   * declined to make, and printing it here would manufacture the single most
   * load-bearing sentence a fault report can produce. It is also not nothing —
   * dropping the row is how a staged fault disappears from the console
   * entirely. So it gets its own state, and the row says only what is stored. */
  fired: {
    tag: "fired", glyph: "●",
    note: "the environment did it, and the agent had to deal with the result.",
  },
  skipped: {
    tag: "skipped", glyph: "◐",
    note: "it reached its call and could NOT happen — the reason is on the row.",
  },
  never: {
    tag: "never reached", glyph: "○",
    note: "staged on a call the agent never made, so nothing happened. This "
      + "measures the agent's path, not the environment's behaviour.",
  },
  staged: {
    tag: "staged, outcome not derived", glyph: "◌",
    note: "it is in the stored plan and no stored event or derivation accounts "
      + "for it. Whether the agent ever reached that call is NOT established by "
      + "this report, so it is not reported as never reached.",
  },
} as const;

function FaultRow({ f, state }: { f: ScenarioFault; state: keyof typeof FAULT_STATES }) {
  const s = FAULT_STATES[state];
  return (
    <li className={`scn-fault scn-fault--${state}`}>
      <span className="scn-fault__glyph" aria-hidden>{s.glyph}</span>
      <span className="scn-fault__tag">{s.tag}</span>
      <span className="scn-fault__what">
        {f.kind} on <b>{f.tool}</b> call #{f.call_index}
        {f.truncate_pct != null ? ` (truncated to ${f.truncate_pct}%)` : ""}
        {f.step != null ? ` · step ${f.step}` : ""}
        {f.observable === false ? " · not observable to the agent" : ""}
      </span>
      {state === "skipped" && (
        <span className="scn-fault__why">reason: {f.reason || "not recorded"}</span>
      )}
    </li>
  );
}

/** Identity of a staged fault, and the ONLY key by which an event is taken to
 *  be about it: `FaultPlan.report` keys `never_reached` on exactly this pair
 *  (`(f.tool, f.call_index) not in hit`), so the console cannot decide a plan
 *  entry is accounted for on a rule the registry does not use.
 *
 *  Compared as whole values, never as substrings — `lookup_order` must not be
 *  matched by an event on `lookup`, and call #1 must not be matched by #10. */
const faultKey = (f: ScenarioFault) => JSON.stringify([f.tool, f.call_index]);

/** The fault report, kept as the four separate facts it is.
 *
 *  `recorded: false` is its own screen state and not an empty list: a run that
 *  stored no report has not told you that nothing was staged.
 *
 *  Every stored row is rendered. That is a requirement and not a nicety: when
 *  the registry cannot reconstruct a plan it returns `never_reached: null` and
 *  `counts: null` and hands back the lists it did store "exactly as stored so
 *  no input is discarded" — and a renderer that only maps fired/skipped/
 *  never_reached then draws an EMPTY list under a legend, below a sentence
 *  promising "the lists the run did store are shown below unchanged". The one
 *  surviving piece of evidence would be dropped by the surface that exists to
 *  show it. Plan entries no list accounts for are therefore drawn in the
 *  `staged` state, which claims nothing about whether the call was reached. */
export function FaultLedger({ faults }: { faults: ScenarioFaults | null | undefined }) {
  const f = faults;
  if (!f || f.recorded !== true) {
    return (
      <>
        <h4>Faults</h4>
        <NotRecorded>
          This run stored <b>no fault report</b>. Whether anything was staged
          against it is not recorded — which is not the same as nothing having
          been staged, and not the same as nothing having fired.
        </NotRecorded>
      </>
    );
  }

  const fired = f.fired ?? [];
  const skipped = f.skipped ?? [];
  const never = f.never_reached ?? [];
  const planned = f.planned ?? [];

  // Which plan entries some stored list speaks for. On a report that
  // reconstructed, `never_reached` is planned-minus-events and this is empty by
  // construction; it fills only when a derivation was unavailable (or when a
  // stored report contradicts itself), which is exactly when the row would
  // otherwise vanish.
  const accounted = new Set([...fired, ...skipped, ...never].map(faultKey));
  const unaccounted = planned.filter((p) => !accounted.has(faultKey(p)));
  // "Nothing was staged" is a claim about the PLAN, and it may not be made over
  // stored events. A plan that failed to reconstruct can come back empty beside
  // a fired event, and the empty-plan copy would then delete that event from the
  // screen while asserting the world was left alone.
  const nothingStored = planned.length === 0 && fired.length === 0
    && skipped.length === 0 && never.length === 0;

  return (
    <>
      <h4>Faults</h4>
      {f.problem && (
        <NotRecorded>The stored plan <b>could not be reconstructed</b>{" "}
          ({f.problem}), so the staged-but-never-reached list and the counts are
          unavailable. The lists the run did store are shown below
          unchanged.</NotRecorded>
      )}
      {nothingStored ? (
        <Nothing><b>No fault was staged.</b> The environment was asked to behave
          normally for the whole run, so nothing here is a claim about how this
          agent handles a broken tool.</Nothing>
      ) : (
        <>
          <ul className="scn-faults">
            {fired.map((x, i) => <FaultRow key={`f${i}`} f={x} state="fired" />)}
            {skipped.map((x, i) => <FaultRow key={`s${i}`} f={x} state="skipped" />)}
            {never.map((x, i) => <FaultRow key={`n${i}`} f={x} state="never" />)}
            {unaccounted.map((x, i) => (
              <FaultRow key={`p${i}`} f={x} state="staged" />))}
          </ul>
          <div className="scn-legend">
            <span><em>{FAULT_STATES.fired.tag}</em> — {FAULT_STATES.fired.note}</span>
            <span><em>{FAULT_STATES.skipped.tag}</em> — {FAULT_STATES.skipped.note}</span>
            <span><em>{FAULT_STATES.never.tag}</em> — {FAULT_STATES.never.note}</span>
            {/* Only when a row is in that state: the other three are categories
                this report HAS, and a permanent "outcome not derived" entry
                would imply every report carries one. */}
            {unaccounted.length > 0 && (
              <span><em>{FAULT_STATES.staged.tag}</em> — {FAULT_STATES.staged.note}</span>
            )}
          </div>
        </>
      )}
      <p className="scn-sub">
        {f.counts
          ? `${f.counts.planned} staged · ${f.counts.fired} fired · `
            + `${f.counts.skipped} skipped · ${f.counts.never_reached} never reached`
          : `Counts unavailable for this report — the stored lists hold `
            + `${planned.length} staged, ${fired.length} fired, `
            + `${skipped.length} skipped, and `
            + (f.never_reached == null
              ? "never-reached is not derived."
              : `${never.length} never reached.`)}
        {f.source ? ` — plan source: ${f.source}.` : ""}
      </p>
    </>
  );
}

/** What moved in the world. `{}` is a RESULT and says so in words: a blank
 *  panel reads as missing data, and "the agent changed nothing" is one of the
 *  more important things this engine can tell you. */
export function StateDiff({ diff }: {
  diff: Record<string, { before: unknown; after: unknown }> | null | undefined;
}) {
  const entries = Object.entries(diff ?? {});
  return (
    <>
      <h4>What changed in the world</h4>
      {entries.length === 0 ? (
        <Nothing>
          <b>The world was not changed.</b> No field of the store this run opened
          against differs from the state it started in — nothing was refunded,
          cancelled, or rewritten.
        </Nothing>
      ) : (
        <div className="scn-scroll">
          <table className="scn-tbl">
            <thead><tr><th>field</th><th>before</th><th></th><th>after</th></tr></thead>
            <tbody>
              {entries.map(([path, ch]) => (
                <tr key={path}>
                  <td className="mono">{path}</td>
                  <td className="mono scn-before">{show(ch?.before)}</td>
                  <td className="scn-arrow"><span aria-label="became">&rarr;</span></td>
                  <td className="mono scn-after">{show(ch?.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* 4. WHAT IT PROVED — the coverage this run exhibited                        */
/* -------------------------------------------------------------------------- */

/** The heading a bin with no colon is filed under. Not a coverpoint name: it
 *  says the input had no dimension rather than guessing one. */
const NO_DIM = "(no dimension)";

/** Split `"<coverpoint>:<bin>"` on its FIRST colon only, so a bin value that
 *  contains one keeps it. A bin with no colon is not silently dropped and not
 *  guessed at — it is grouped under an explicit "(no dimension)" heading, since
 *  discarding an input without saying so is the defect this codebase treats as
 *  worst. */
export function groupBins(bins: string[]): [string, string[]][] {
  const out = new Map<string, string[]>();
  for (const b of bins) {
    const i = b.indexOf(":");
    const dim = i > 0 ? b.slice(0, i) : NO_DIM;
    const rest = i > 0 ? b.slice(i + 1) : b;
    const list = out.get(dim);
    if (list) list.push(rest); else out.set(dim, [rest]);
  }
  return [...out.entries()];
}

/** Recover the bin id `groupBins` took apart. The `other` test below is defined
 *  on a WHOLE bin id, and it has to be handed one. */
function fullBin(dim: string, value: string): string {
  return dim === NO_DIM ? value : `${dim}:${value}`;
}

/** The coverage model's explicit catch-all bin, by the name the model gives it
 *  (`coverage/model.py: OTHER_BIN`). Every coverpoint is required to declare
 *  one, and it is outside closure by construction: a run landing here exhibited
 *  something the model has no bin for. */
export const OTHER_BIN = "other";

/** Is this bin id the unmodelled `other` bin?
 *
 *  Matched on the token after the LAST colon, which is what the CLI's renderer
 *  does (`cli.py:_render_coverage`, `str(b).rpartition(":")`). The console used
 *  to split on the FIRST colon and never made this test at all — two surfaces
 *  disagreeing about what a bin id means is drift, and one of them is then
 *  wrong about which bins count toward closure.
 *
 *  Whole token, never a substring, which is the failure this repo has had most
 *  often: `trajectory:another` ENDS IN "other" and is not the other bin;
 *  `action_risk:other_write` contains it and is not one either; `other:refund`
 *  names a coverpoint called `other` and is a modelled bin of it. A bare
 *  `other` with no coverpoint prefix IS one, exactly as `rpartition` reads it. */
export function isOtherBin(bin: string): boolean {
  const s = String(bin ?? "");
  return s.slice(s.lastIndexOf(":") + 1) === OTHER_BIN;
}

const OTHER_WHY =
  "The model has no bin for what this run exhibited on this coverpoint, so it "
  + "landed in the explicit `other` bin. `other` is OUTSIDE closure by "
  + "construction and is credited to nothing — a run that exhibits it has told "
  + "you about the coverage MODEL, not about the agent.";

/** ASKED FOR, NEVER EXHIBITED — the corners the stimulus point requested and
 *  the run did not produce. Until now this existed only as a line of terminal
 *  output that nothing stored, and it is the single most important thing this
 *  product says: what we asked to test, against what the run actually did.
 *
 *  THREE STATES, and they are three different claims:
 *    null/absent — nobody computed divergence for this run. NOT a finding that
 *                  none diverged, and drawn with the unrecorded vocabulary.
 *    []          — computed, and every requested corner appeared. A RESULT.
 *    [...]       — the point asked for these and the run did not deliver.
 *
 *  A divergence row is the OPPOSITE of an exhibited bin and never borrows its
 *  treatment: `.scn-bins code` is a corner the run reached, and these are
 *  corners it did not. They are a fact about the GENERATOR's reach, never about
 *  the agent's behaviour, and they are never summed into a coverage number.
 *
 *  It is its OWN block, beside {@link ExhibitedCoverage} and not inside it, for
 *  the reason the registry gives for storing it under its own null: the two
 *  halves are collected by different callers at different moments, and
 *  `coverage.measured` speaks for `bins` alone. Kept inside, this block would
 *  sit under whichever branch `measured` chose — and the "measured, credited
 *  nothing" state of the bins would carry an unrecorded divergence read inside
 *  its own markup, which is exactly the confusion of two absences this page
 *  exists to prevent. Out here, gating one on the other is not expressible. */
export function cornersNeverCompared(
  point: Record<string, string> | null | undefined,
  bins: string[] | null | undefined,
  rows: CoverageDivergence[] | null | undefined,
): [string, string][] {
  const mentioned = new Set<string>();
  // the same split the exhibited list uses, on the LAST colon, so a coverpoint
  // and its bin are rejoined exactly as they were printed.
  for (const b of bins ?? []) mentioned.add(String(b ?? ""));
  for (const d of rows ?? []) {
    mentioned.add(`${String(d?.coverpoint_id ?? "")}:${String(d?.bin_id ?? "")}`);
  }
  return Object.entries(point ?? {})
    .map(([k, v]): [string, string] => [String(k), String(v)])
    .filter(([k, v]) => !mentioned.has(`${k}:${v}`))
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
}

export function Divergence({ rows, point, bins }: {
  rows: CoverageDivergence[] | null | undefined;
  point?: Record<string, string> | null;
  bins?: string[] | null;
}) {
  const never = cornersNeverCompared(point, bins, rows);
  const nRequested = Object.keys(point ?? {}).length;
  const nCompared = nRequested - never.length;
  return (
    <Section title="Asked for, never exhibited"
      sub="What the stimulus point requested of this run, against what the run actually produced. A row here is a corner the generator asked for and never reached — it is not a score, and it is never a finding about the agent.">
      {rows == null ? (
        <NotRecorded>
          <b>Divergence was not recorded for this run.</b> Nobody worked out
          which requested corners it failed to produce, so nothing here says
          none diverged. A run that WAS computed and found nothing looks
          different from this.
        </NotRecorded>
      ) : rows.length === 0 ? (
        nRequested === 0 ? (
          <Nothing>
            <b>Computed, and nothing diverged.</b> This row records no stimulus
            point, so there is nothing here it could have been compared
            against.
          </Nothing>
        ) : never.length > 0 ? (
          <Nothing>
            <b>Computed, and nothing diverged</b> among the {nCompared} of{" "}
            {nRequested} corners this run&apos;s point asked for that the
            coverage read could speak for.
          </Nothing>
        ) : (
          <Nothing>
            <b>Computed, and nothing diverged.</b> All {nRequested} corners this
            run&apos;s point asked for were exhibited by the run itself.
          </Nothing>
        )
      ) : (
        <>
          <ul className="scn-diverge">
            {rows.map((d, i) => (
              <li className="scn-diverge__row"
                  key={`${d?.coverpoint_id}:${d?.bin_id}:${i}`}>
                {/* its own mark: ● ◐ ○ ◌ are the four FAULT states, and ◌ there
                    means "nobody derived this outcome". A divergence row is a
                    result, not a vacuity, and must not borrow that glyph. */}
                <span className="scn-diverge__glyph" aria-hidden>◇</span>
                <span className="scn-diverge__tag">asked for, never exhibited</span>
                <span className="scn-diverge__what">
                  {plain(d?.coverpoint_id)}=<b>{plain(d?.bin_id)}</b>
                </span>
                <span className="scn-diverge__count">
                  requested {show(d?.requested)} · exhibited {show(d?.exhibited)}
                </span>
              </li>
            ))}
          </ul>
          <p className="scn-sub">
            The point asked for these corners and the run did not produce them.
            That is a fact about the reach of the GENERATOR — what the stimulus
            asked of the world and never got back — and never a fact about the
            agent&apos;s behaviour. It is not a coverage score and is not summed
            into one.
          </p>
        </>
      )}
      {/* Under EVERY branch above, including `[...]`: a divergence list that
          found something is just as silent about the corners nothing compared,
          and "these diverged" reads as "and the rest were fine". Drawn with the
          UNRECORDED vocabulary and never the divergence one — a corner nothing
          compared is an absence, not the result a `◇` row is. */}
      {never.length > 0 && rows != null && (
        <>
          <ul className="scn-diverge scn-diverge--uncompared">
            {never.map(([cp, bin]) => (
              <li className="scn-diverge__row scn-diverge__row--uncompared"
                  key={`uncompared:${cp}:${bin}`}>
                <span className="scn-diverge__glyph" aria-hidden>◌</span>
                <span className="scn-diverge__tag">never compared</span>
                <span className="scn-diverge__what">
                  {plain(cp)}=<b>{plain(bin)}</b>
                </span>
                <span className="scn-diverge__count">
                  requested · never measured
                </span>
              </li>
            ))}
          </ul>
          <p className="scn-sub">
            {never.length} of the {nRequested} corners this run&apos;s point
            asked for are named by neither list above, so nothing on this page
            says whether the run produced them. A dimension the coverage model
            does not name, has no bin for, or cannot measure reaches no
            comparison at all — it is ABSENT from this read, not clean in it.
          </p>
        </>
      )}
    </Section>
  );
}

/** The bins this run EXHIBITED. It answers for `coverage.measured` and
 *  `coverage.bins` and for nothing else — the divergence half is
 *  {@link Divergence}, rendered beside this and never within it. */
export function ExhibitedCoverage({ coverage }: {
  coverage: { measured: boolean; bins: string[] | null } | null | undefined;
}) {
  const c = coverage;
  if (!c || c.measured !== true) {
    return (
      <Section eyebrow="4 · What it proved" title="Coverage exhibited">
        <NotRecorded>
          <b>No coverage was collected for this run.</b> Nothing here says the
          run exercised nothing — it says nobody measured. A run that WAS
          measured and credited nothing looks different from this.
        </NotRecorded>
      </Section>
    );
  }

  const bins = c.bins ?? [];
  return (
    <Section eyebrow="4 · What it proved" title="Coverage exhibited"
      sub="Credited from what the run EXHIBITED in its trace, never from what the scenario asked for. These are bins this one run reached; they are not closure. A dimension absent here was either not exercised or not measured — this list is built from countable bins only, so a coverpoint the model declares not measurable, and a classifier-backed bin collected with no evaluator, are both missing from it without having been tested and found wanting. It cannot tell you which.">
      {bins.length === 0 ? (
        <Nothing><b>Measured, and credited nothing.</b> Coverage was collected
          for this run and it exhibited no bin at all.</Nothing>
      ) : (
        <div className="scn-bins">
          {groupBins(bins).map(([dim, vals]) => (
            <div className="scn-bins__dim" key={dim}>
              <span className="scn-bins__k">{dim.replace(/_/g, " ")}</span>
              {vals.map((v) => (isOtherBin(fullBin(dim, v))
                ? <span className="scn-bins__other" key={v} title={OTHER_WHY}>
                    <code>{v}</code>
                    <span className="scn-bins__outside">outside closure</span>
                  </span>
                : <code key={v}>{v}</code>))}
            </div>
          ))}
        </div>
      )}
      {/* Only when a bin is actually in it: a permanent note about `other`
          would imply every run exhibits one. */}
      {bins.some(isOtherBin) && (
        <p className="scn-sub">
          The <code>other</code> bin is the model&apos;s declared catch-all. It
          sits OUTSIDE closure and counts toward nothing; exhibiting it says the
          model has no bin for what this run did.
        </p>
      )}
    </Section>
  );
}

/** Anything the run recorded as dropped, unparsed or not applicable. Surfaced
 *  rather than filed away: code that discards an input has to say so. */
export function Disclosures({ items }: { items: Record<string, unknown>[] }) {
  if (!items?.length) return null;
  return (
    <Section eyebrow="Disclosed" title="What this run had to declare"
      sub="Every point at which the engine dropped, could not parse, or declined to use something. A run that discards an input silently is worse than one that fails.">
      <ul className="scn-disc">
        {items.map((d, i) => (
          <li key={i}>
            <b>{plain(d.kind ?? "note")}</b>{" "}
            {plain(d.note ?? "")}
            {d.fact != null ? ` (${plain(d.fact)})` : ""}
            {d.reason != null ? ` [${plain(d.reason)}]` : ""}
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** Who stood in for the human. A simulated counterparty is a measurement
 *  instrument and its settings belong on the record. */
export function UserProvenance({ p }: { p: Record<string, unknown> }) {
  const entries = Object.entries(p ?? {});
  if (entries.length === 0) return null;
  return (
    <>
      <h4>Who the counterparty was</h4>
      <div className="scn-meta">
        {entries.map(([k, v]) => (
          <div key={k}>
            <span className="scn-meta__k">{k.replace(/_/g, " ")}</span>
            <span className="scn-meta__v">
              {v == null ? "—"
                : Array.isArray(v) ? (v.length ? v.join(", ") : "none")
                : String(v)}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* the two empty states                                                       */
/* -------------------------------------------------------------------------- */

/** No stored run. This says how to make one and names what has to persist it —
 *  it does not draw a plausible example row, because a fabricated run on a
 *  verification console is the worst thing this screen could show.
 *
 *  The command printed here must be a command that POPULATES this screen.
 *  `Registry.save_scenario_run` is the write this list reads back, and it has
 *  TWO callers in the tree: `cli.py`, inside `agenttic scenario run`, and
 *  `persist_scenario_run` (`scenario/runner.py:1671`), which `harness_executor`
 *  calls for every scenario `agenttic cdv` executes. Re-measured against a
 *  scratch registry holding 3 stored runs, `agenttic cdv --agent demo-bot
 *  --rubric r-cdv-persist --mock --max-scenarios 4 --max-rounds 1` finished its
 *  4 scenarios and left `select count(*) from scenario_runs` at 7 — one row per
 *  scenario, filed under `demo-bot`.
 *
 *  This screen used to name cdv as an EXCLUSION: "an empty list after a cdv run
 *  is that command's scope, not a dropped write". That was measured when cdv
 *  persisted nothing, and it is now false in the most expensive direction a
 *  sentence on this page can be false. `persist_scenario_run` swallows every
 *  storage failure — it logs at WARNING and lets the batch finish — so the run
 *  completes, the scorecard prints, and the row is gone. That is EXACTLY the
 *  state the old sentence instructed the reader to accept as by-design: an
 *  absence explained away as a non-finding, in the one place a reader comes to
 *  find out whether the evidence exists.
 *
 *  So the two states are held apart instead of collapsed: nothing ran, or a run
 *  completed and its write was lost. This screen has no signal for either, so
 *  it names both and names the DISCRIMINATOR — the id `scenario run` prints on
 *  success, the `NOT STORED` line the batch path logs on failure — rather than
 *  picking the reassuring one on the reader's behalf. `--mock` stays off the
 *  printed command because `scenario run` has no such flag (it exits 2) —
 *  offline IS the default there, and `--model` is what opts into spending
 *  money. */
export function ScenarioEmpty() {
  return (
    <EmptyState icon="◎" title="No scenario run has been stored yet"
      hint={<>
        A scenario run is one realized ticket driven against an agent through the
        enforcement gateway, in a world that can be made to fail on cue.
        Run one offline — no API key, no model:
        {/* the command as an expression, not as JSX text: a <pre> preserves the
            source indentation, and a command a reader copies must be the command
            they can paste. */}
        <pre className="doc" style={{ marginTop: "var(--sp-3)", textAlign: "left" }}>
          {"uv run agenttic scenario run --intent refund --tool-condition timeout"}
        </pre>
        <code>agenttic scenario run</code> persists the run it drives, and{" "}
        <code>agenttic cdv</code> persists one row per scenario it executes —
        both through <code>Registry.save_scenario_run</code>, the write this list
        reads back. So an empty list after either command is not that
        command&rsquo;s scope. Either nothing ran, or a run completed and its
        write was lost: the batch path logs a lost write and lets the run finish,
        so a scorecard can print over an empty page. Tell them apart rather than
        assuming — <code>scenario run</code> prints{" "}
        <code>stored as run &lt;id&gt;</code> when the row is here, and a write
        that was lost leaves <code>scenario run NOT STORED</code> in the process
        log. The list is tenant-scoped, so a run stored under another workspace
        stays in that one.
      </>} />
  );
}

/** One shell word. The filter values come from a free-text field, and a command
 *  a reader is invited to PASTE must not change meaning because an id carried a
 *  space or a semicolon. Anything outside the id alphabet is single-quoted. */
function shellWord(v: string): string {
  return /^[A-Za-z0-9_.:@/-]+$/.test(v) ? v : `'${v.replace(/'/g, "'\\''")}'`;
}

/** How many runs the list asks the API for.
 *
 *  Named rather than inlined because two places have to agree about it: the
 *  request and the notice that says the answer was cut off. */
export const RUN_LIMIT = 100;

/** A truncated list that looks complete is a claim about how much evidence
 *  exists, made by a query that never asked.
 *
 *  `GET /api/scenario-runs` returns `count = len(runs)` — the size of the PAGE,
 *  not of the store — so nothing in the response distinguishes "there are
 *  exactly 100 runs" from "there are thousands and you are seeing the newest
 *  100". A reader counting rows to judge how much testing has happened would get
 *  the same number either way. Shown only when the page came back full, because
 *  a notice on a short page would be the opposite error. */
export function ListCap({ n, limit }: { n: number; limit: number }) {
  if (n < limit) return null;
  return (
    <p className="scn-why scn-cap">
      Showing the newest <b>{limit}</b> runs, which is all this page asks for.
      There may be older ones: the list is capped, and the count above is the
      size of this page rather than of the store. Narrow it with the filters, or
      read the full set with <code>agenttic scenario list --limit</code>.
    </p>
  );
}

/** The OTHER zero, and it is not the one above.
 *
 *  `rows.length === 0` under a filter is a MEASUREMENT — "nothing matched this
 *  query" — while every sentence in `ScenarioEmpty` is a claim about the STORE
 *  ("no scenario run has been stored yet", "the list is tenant-scoped, so a run
 *  stored under another workspace stays in that one"). A filtered zero supports
 *  none of them: it is a statement about one exact-match query and says nothing
 *  about how much evidence exists. Printing the empty-store sentence for it is
 *  an absence dressed as a result, in the one place a reader goes to find out
 *  how much evidence exists — the same defect the CLI names and fixed in
 *  `cli.py:scenario_list_cmd` ("a zero UNDER A FILTER is a measurement"), and
 *  the same distinction the endpoint protects at the other end by refusing to
 *  read an untouched field as a filter (`routes/scenarios.py:_filter`). The
 *  honesty was already in the transport and was being lost at the render.
 *
 *  It names the filter it is reporting on, because "nothing matched" is only a
 *  measurement if the reader can see WHAT was asked — and both fields match
 *  exactly, so a near-miss id is the likely cause and has to be visible. */
export function ScenarioNoMatch({ query, onClear }: {
  query: { agent_id: string; scenario_id: string }; onClear?: () => void;
}) {
  const named = [
    query.agent_id ? `agent id ${query.agent_id}` : "",
    query.scenario_id ? `scenario id ${query.scenario_id}` : "",
  ].filter(Boolean).join(" and ");
  // The same query for the terminal, so the claim is checkable rather than
  // asserted: pasting this prints the CLI's own sentence for a filtered zero.
  const cmd = [
    "uv run agenttic scenario list",
    query.agent_id ? `--agent ${shellWord(query.agent_id)}` : "",
    query.scenario_id ? `--scenario ${shellWord(query.scenario_id)}` : "",
  ].filter(Boolean).join(" ");
  return (
    <EmptyState icon="⌀" title={`No scenario run matches ${named}`}
      hint={<>
        Which is not the same claim as no run being stored. This is a result
        about the query; nothing here reports how much evidence this workspace
        holds. Drop the filter for that. Both fields match a whole id exactly
        and never a fragment of one, so a near-miss reads as zero. The terminal
        answers this same question the same way:
        <pre className="doc" style={{ marginTop: "var(--sp-3)", textAlign: "left" }}>
          {cmd}
        </pre>
      </>}
      action={onClear
        ? <button type="button" onClick={onClear}>drop the filter</button>
        : undefined} />
  );
}

/* -------------------------------------------------------------------------- */
/* the page                                                                   */
/* -------------------------------------------------------------------------- */

/** Why the faults cell can say "report unreadable" — a tooltip on the cell,
 *  because the cell has one line and the reason belongs to the run, not to the
 *  list. The full reason string the registry stored is on the detail view. */
const UNREADABLE_WHY =
  "A fault report was recorded for this run and could not be reconstructed on "
  + "read. Inspect the run for the lists it stored and the reason they would "
  + "not rebuild.";

/** How much of a run id the list shows before it has to cut. A run id is 32 hex
 *  characters; the column is one of nine. */
const RUN_ID_HEAD = 12;

/** The run id, and the fact that it has been CUT.
 *
 *  This id is the one value an operator has to carry off this screen: it is the
 *  argument to `agenttic scenario transcript <id>`, and the id `agenttic
 *  scenario run` prints when it stores a run. The cell used to render
 *  `slice(0, 12)` with no ellipsis, no title and nothing to copy — so a
 *  12-character prefix was drawn exactly as a whole id would be. That is the
 *  page's own defect family wearing a different hat: a partial value presented
 *  as a complete one, indistinguishable from the real thing until it comes back
 *  "no such run" from the CLI.
 *
 *  So the cut is drawn (`…`), the whole id is on the element as `title` and in
 *  the copy control's accessible name, and the button copies the id itself —
 *  never the prefix. A row with no id at all says so and offers nothing to
 *  copy, because copying "" would look like it worked. */
export function RunId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  const full = String(id ?? "");
  if (!full) return <span className="scn-why">(no id)</span>;

  const head = full.slice(0, RUN_ID_HEAD);
  const cut = full.length > head.length;
  const copy = () => {
    try {
      navigator.clipboard?.writeText(full);
      setCopied(true);
      // the label is state, and it goes back when the state does
      setTimeout(() => setCopied(false), 1400);
    } catch { /* clipboard unavailable — `title` still carries the whole id */ }
  };
  return (
    <span className="scn-id">
      <code className="scn-id__text" title={full}>
        {head}
        {cut && <span className="scn-id__cut" aria-hidden>…</span>}
      </code>
      <button type="button" className="scn-id__copy" onClick={copy}
              title={cut ? `copy the whole run id — ${full}` : `copy ${full}`}
              aria-label={`copy the whole run id ${full}`}>
        {copied ? "copied" : "copy"}
      </button>
    </span>
  );
}

/** One row of the run list.
 *
 *  The faults cell has FOUR states and they are four different claims. `no
 *  report` is `recorded: false` — nobody wrote down what was staged. `report
 *  unreadable` is `recorded: true, counts: null`: the report IS stored and could
 *  not be reconstructed on read (it names a tool this world lacks, say), which
 *  `sqlite_store._faults_view` keeps deliberately apart from the first and which
 *  {@link FaultLedger} shows in full one click away. This cell used to print
 *  both as `no report`, i.e. it denied a record that exists and contradicted the
 *  detail view of the very run it was summarising. `none staged` is a measured
 *  empty plan, and the counts are the four facts of a plan that ran. */
export function RunRow({ r, selected, onSelect }: {
  r: ScenarioRunRow; selected: boolean; onSelect: (id: string) => void;
}) {
  const c = r.faults?.counts;
  return (
    <tr className={selected ? "scn-selected" : ""}>
      {/* a row with no id has burned this console before ("can't access
          property 'slice'"), and one bad row must not take the page down. */}
      <td className="scn-runs__id"><RunId id={r.run_id} /></td>
      <td className="mono">{r.agent_id}</td>
      <td className="mono">{r.scenario_id}</td>
      <td>{formatCreated(r.created_at)}</td>
      <td>
        <span className={`scn-tag${r.conversational ? " scn-tag--conv" : ""}`}>
          {r.conversational ? "conversation" : "single-shot"}
        </span>
        {r.ended ? <span className="scn-why">{r.ended}</span> : null}
      </td>
      <td>{r.world_changed ? "changed" : <span className="scn-why">unchanged</span>}</td>
      <td className="num">{r.n_blocked}</td>
      <td>
        {r.faults?.recorded !== true
          ? <span className="scn-why">no report</span>
          : !c
            ? <span className="scn-unreadable" title={UNREADABLE_WHY}>
                report unreadable</span>
            : c.planned === 0
              ? <span className="scn-why">none staged</span>
              : <>{c.fired} fired{c.skipped ? ` · ${c.skipped} skipped` : ""}
                  {c.never_reached ? ` · ${c.never_reached} never reached` : ""}</>}
      </td>
      <td>
        <button onClick={() => onSelect(r.run_id)}>inspect</button>
      </td>
    </tr>
  );
}

export function ScenarioRunDetailView({ run, spans, traceProblem, tracePending }: {
  run: ScenarioRunDetail; spans: TraceSpan[] | null; traceProblem?: string;
  tracePending?: boolean;
}) {
  return (
    <>
      <Stimulus run={run} />

      <Section eyebrow="2 · What happened" title="The exchange"
        sub="What the agent was told, what it said back, and — for a conversation — which turns handed over a fact it had to ask for.">
        <Transcript run={run} />
        <Elicitation run={run} />
        <Interactions run={run} />
        <UserProvenance p={run.user_provenance ?? {}} />
      </Section>

      <Section eyebrow="3 · What the world did" title="Enforcement, faults, and the store"
        sub="The gateway ruled on every call the agent attempted; the injector staged failures on named calls; the store either moved or did not.">
        <ToolLedger spans={spans} blocked={run.blocked ?? []}
                    traceProblem={traceProblem} pending={tracePending} />
        <FaultLedger faults={run.faults} />
        <StateDiff diff={run.state_diff} />
      </Section>

      <ExhibitedCoverage coverage={run.coverage} />
      {/* the other half of the same coverage read, and the half nothing stored
          until now: what the point ASKED FOR that this run never produced. It
          answers its own presence question (`null` vs `[]`), which is why it is
          not passed through `measured`. */}
      {/* the point and the exhibited bins travel with the rows because an EMPTY
          divergence list is a result about the corners the read could speak for
          and about no others: without them this block closed over the whole
          point with the word "every". */}
      <Divergence rows={run.coverage?.divergence} point={run.point}
                  bins={run.coverage?.bins} />

      <Disclosures items={run.disclosures ?? []} />

      <Section title="The record itself"
        sub="Everything above is this document, rendered. Nothing on this screen is computed from anything else.">
        <div className="scn-meta">
          <div><span className="scn-meta__k">run</span>
               <span className="scn-meta__v">{run.run_id}</span></div>
          <div><span className="scn-meta__k">trace</span>
               <span className="scn-meta__v">{run.trace_id}</span></div>
          <div><span className="scn-meta__k">agent</span>
               <span className="scn-meta__v">{run.agent_id}</span></div>
          <div><span className="scn-meta__k">stored</span>
               <span className="scn-meta__v">{formatCreated(run.created_at)}</span></div>
        </div>
        <RawToggle value={run} label="the stored run, verbatim" />
      </Section>
    </>
  );
}

export function ScenariosPage() {
  const [rows, setRows] = useState<ScenarioRunRow[] | null>(null);
  const [listErr, setListErr] = useState("");
  const [agentId, setAgentId] = useState("");
  const [scenarioId, setScenarioId] = useState("");
  const [query, setQuery] = useState({ agent_id: "", scenario_id: "" });

  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<ScenarioRunDetail | null>(null);
  const [detailErr, setDetailErr] = useState("");
  const [spans, setSpans] = useState<TraceSpan[] | null>(null);
  const [traceProblem, setTraceProblem] = useState("");
  // In flight is its own state. Without it, the window between the run arriving
  // and its trace arriving renders as "the trace could not be read" — a finding
  // about a request that has not come back yet.
  const [tracePending, setTracePending] = useState(false);

  // `rows === null` is "still loading" and drives the skeleton. It is reset in
  // the submit handler rather than in the effect body, so this effect only
  // touches state from its async callbacks (react-hooks/set-state-in-effect).
  useEffect(() => {
    let live = true;
    api.listScenarioRuns({
      agent_id: query.agent_id || undefined,
      scenario_id: query.scenario_id || undefined,
      limit: RUN_LIMIT,
    })
      .then((r) => { if (live) setRows(r.runs ?? []); })
      .catch((e) => { if (live) { setListErr(String(e?.message || e)); setRows([]); } });
    return () => { live = false; };
  }, [query]);

  const applyFilter = (e: React.FormEvent) => {
    e.preventDefault();
    setRows(null);
    setListErr("");
    setQuery({ agent_id: agentId.trim(), scenario_id: scenarioId.trim() });
  };

  // "Drop the filter" has to drop it in both places or the screen lies about
  // what it is showing: the fields the reader can still see, and the query the
  // list was actually answered for.
  const clearFilter = () => {
    setAgentId("");
    setScenarioId("");
    setRows(null);
    setListErr("");
    setQuery({ agent_id: "", scenario_id: "" });
  };

  const inspect = (runId: string) => {
    setSelected(runId);
    setDetail(null);
    setDetailErr("");
    setSpans(null);
    setTraceProblem("");
    setTracePending(true);
    api.getScenarioRun(runId)
      .then((d) => {
        setDetail(d);
        // The calls and the gateway's verdict on each live on the trace, not on
        // the run record. A failure here is reported, never rendered as "the
        // agent called nothing".
        return api.getTrace(d.trace_id)
          .then((t: { spans?: TraceSpan[] }) => setSpans(t?.spans ?? []))
          .catch((e) => setTraceProblem(String(e?.message || e)));
      })
      .catch((e) => setDetailErr(String(e?.message || e)))
      .finally(() => setTracePending(false));
  };

  return (
    <div className="page">
      <div className="list-page scn-page">
        <PageHeader title="Scenario runs"
          subtitle="One realized ticket, driven against an agent through the enforcement gateway, in a world that can be made to fail on cue. What was asked, what happened, what the world did, and what it proved." />

        <form className="scn-filters" onSubmit={applyFilter}>
          <input value={agentId} onChange={(e) => setAgentId(e.target.value)}
                 placeholder="agent id (exact)" aria-label="filter by agent id" />
          <input value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}
                 placeholder="scenario id (exact)" aria-label="filter by scenario id" />
          <button type="submit">filter</button>
        </form>

        {listErr ? (
          <EmptyState icon="⚠" title="Could not load stored runs" hint={listErr} />
        ) : rows == null ? (
          <Skeleton rows={6} />
        ) : rows.length === 0 ? (
          /* Two different zeros. `query` — not `agentId`/`scenarioId` — is what
             decides: the fields hold what the reader is typing NEXT, and the
             empty list on screen is the answer to the query that was last
             submitted. See ScenarioNoMatch. */
          query.agent_id || query.scenario_id ? (
            <ScenarioNoMatch query={query} onClear={clearFilter} />
          ) : (
            <ScenarioEmpty />
          )
        ) : (
          <div className="table-wrap">
            <table className="data scn-runs">
              <thead>
                <tr><th>run</th><th>agent</th><th>scenario</th><th>stored</th>
                    <th>shape</th><th>world</th><th className="num">blocked</th>
                    <th>faults</th><th /></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <RunRow key={r.run_id} r={r} selected={r.run_id === selected}
                          onSelect={inspect} />
                ))}
              </tbody>
            </table>
            <ListCap n={rows.length} limit={RUN_LIMIT} />
          </div>
        )}

        {detailErr && (
          <EmptyState icon="⚠" title="Could not load that run" hint={detailErr} />
        )}
        {detail && (
          <ScenarioRunDetailView run={detail} spans={spans}
                                 traceProblem={traceProblem || undefined}
                                 tracePending={tracePending} />
        )}
      </div>
    </div>
  );
}
