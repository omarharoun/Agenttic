import { useState } from "react";
import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import {
  Button, Eyebrow, SectionHeading, CodeBlock, StatTile,
  FaqItem, ScorecardCard, ProvenanceBadge,
  CoverageWheel, CoverageWheelLegend, RefusalNotice,
} from "../components/ds";
import {
  SHOW_SOCIAL_PROOF, ASSISTANTS, type TabKey, SAMPLE_METRICS, SAMPLE_ROWS,
  CONFIDENCE, COVERAGE_CLAIMS, TRUST, FAQ, REFUSAL_REASONS, ON_TOP,
  STAT_BAND, REFUSAL_TRANSCRIPT, REFUSAL_CONDITIONS, LIMITS, PROOF_STATES,
  VERIFY_TRANSCRIPT, VERIFY_STATES,
} from "../landing/data";
import "../landing/landing.css";

/* ============================================================================
   The public landing route (SPEC-11 Step 52). Rebuilt from the shared design
   tokens + the ds component library — no bespoke markup, no second style world.
   The see-it block mirrors the console's run view: the coverage wheel first,
   then the SAME <ScorecardCard>. All
   social proof is gated behind SHOW_SOCIAL_PROOF (OFF until real, Hard Rule 49),
   so with the flag off the page ships clean with those sections simply absent.
   Public route: SiteNav only, no authenticated data or console chrome.
   ========================================================================== */

/** Command OUTPUT, not a command. CodeBlock offers a copy button because its
 *  lines are meant to be run; a refusal transcript is meant to be read, and a
 *  copy affordance on it would invite pasting a message back into a shell. */
function Transcript({ lines, label, meta }: {
  lines: { prompt?: string; text: string; tone?: "fail" | "dim" | "ok" }[];
  label: string; meta?: string;
}) {
  return (
    <figure className="lp-tx" role="group" aria-label={label}>
      <figcaption className="lp-tx__bar">
        <span>{label}</span>
        {meta && <span className="lp-tx__sample">{meta}</span>}
      </figcaption>
      <pre className="lp-tx__body">
        {lines.map((l, i) => (
          <div key={i} className={l.tone ? `lp-tx__l is-${l.tone}` : "lp-tx__l"}>
            {l.prompt && <span className="lp-tx__p">{l.prompt} </span>}
            {l.text || "\u00a0"}
          </div>
        ))}
      </pre>
    </figure>
  );
}

function HowItWorks() {
  const [asst, setAsst] = useState(ASSISTANTS[0]);
  const [tab, setTab] = useState<TabKey>("run");
  return (
    <div className="lp-picker">
      <div className="lp-picker__q">Where does it need to run?</div>
      <div className="lp-assts" role="tablist" aria-label="deployment surface">
        {ASSISTANTS.map((a) => (
          <button key={a.id} className="lp-asst" role="tab"
                  aria-selected={a.id === asst.id}
                  onClick={() => setAsst(a)}>{a.name}</button>
        ))}
      </div>
      <div className="lp-tabs" role="tablist" aria-label="command">
        {(["run", "integrate", "isolate"] as TabKey[]).map((t) => (
          <button key={t} className="lp-tab" role="tab" aria-selected={t === tab}
                  onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="lp-picker__cmd">
        <CodeBlock lines={asst.cmds[tab]} label={`${asst.name} — ${tab}`} />
      </div>
    </div>
  );
}

/** A real captured run, not a mock. Four of the eight dimensions carry a measured
 *  figure; the other four draw hatched, because an unmeasured dimension cannot
 *  fail and would otherwise appear in no report at all.
 *
 *  `session_shape` was `0.333` here. It cannot be, and never could have been:
 *  the coverpoint is declared not measurable — nothing in a run emits a human
 *  turn — so no run emits a figure for it. A number the product cannot produce,
 *  on the page whose whole length argues that a number needs a denominator, is
 *  the defect this page is about.
 *
 *  Two things this capture does not show, named rather than filled in. There is
 *  no `agent_steps` figure: that coverpoint did not exist when the run was taken,
 *  and a value for it here would be a guess. And `closure` below is what this run
 *  reported under baseline **v2**, which averaged `session_shape` into the mean;
 *  under v3 the same traces report a different figure. The traces are not in the
 *  tree, so it cannot be recomputed here — recapturing the whole wheel against a
 *  v3 run is owed work, and inventing two numbers is not the alternative to it. */
const LANDING_WHEEL = [
  { id: "trajectory", value: 0.111 },
  { id: "tool_condition", value: 0.167 },
  { id: "action_risk", value: 0.5 },
  { id: "data_condition", value: 0.2 },
  { id: "session_shape", value: null },
  { id: "intent", value: null },
  { id: "emotional_register", value: null },
  { id: "policy_vector", value: null },
];

export function LandingPage() {
  return (
    <div className="lp">
      <SiteNav />

      {/* ---- HERO ---- */}
      <header className="lp-hero">
        <div className="wrap lp-hero__grid">
          <div>
            <Eyebrow>Agent verification</Eyebrow>
            <h1>A certificate is not a participation award.</h1>
            {/* The lede used to read "coverage closure, assertions, a bounded
                formal check ... the sign-off is negative the signing call
                raises" — five internal terms in two sentences, one of them
                ("raises") a Python word, in the first paragraph anybody reads.
                The jargon rule below this file only ever checked the
                `#why-refused` slice, so the hero was never looked at. */}
            <p className="lp-hero__lede">
              Your tests tell you how the agent did on the situations you
              thought of. We tell you which ones it was never put in — and when
              too much was never tried, we refuse to certify it and write
              nothing at all.
            </p>
            <div className="lp-cta">
              <Button href="#access">See what was never tried</Button>
              <Button variant="ghost" href="#check-it">Check a certificate yourself</Button>
            </div>
            <div className="lp-hero__meta">
              Sits on the traces you already produce · runs on your machines
            </div>
          </div>
          {/* The hero is a certificate we did NOT issue. Every competitor sells a
              number that goes up; the one thing none of them can do is tell the
              customer paying them that the evidence is not good enough yet. */}
          <div className="lp-hero__art">
            <RefusalNotice
              subject="support-agent · version 4a91c2 · checked 26 July"
              reasons={REFUSAL_REASONS}
              footnote={"This is what our answer looks like when the evidence does "
                + "not hold up. It is the same wording the tool prints, not a mockup."}
            />
          </div>
        </div>
      </header>

      {/* ---- THREE COUNTABLE FACTS, then the tool declining ---- */}
      <section id="facts" className="lp-band-sec">
        <div className="wrap">
          <div className="lp-band">
            {STAT_BAND.map((s) => (
              <div className="lp-band__i" key={s.lab}>
                <div className="lp-band__f">{s.fig}</div>
                <div className="lp-band__l">{s.lab}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- THE REFUSAL, AS PRINTED ----
          Nobody in this category shows a negative outcome from their own
          product. It is simultaneously the demo, the proof and the difference,
          which is why it sits first rather than in a footer. */}
      <section id="refusal">
        <div className="wrap lp-refusal">
          <SectionHeading eyebrow="What it prints when it will not certify"
            title="The only command that issues a certificate, declining to." />
          <Transcript lines={REFUSAL_TRANSCRIPT} label="agenttic attest"
                      meta="support-triage · sample data" />
          <div className="lp-refusal__notes">
            <p>
              It names what was missing, not a grade. It exits with an error, so
              a build that runs it fails too. And no file is written — the
              certificate is only written once the signing step has succeeded,
              and here it never does.
            </p>
            <p>
              The reasons are the checks themselves, one for one. It lists only
              what actually blocked it and nothing else, so you are never sent to
              go and fix the wrong thing.
            </p>
          </div>
        </div>
      </section>

      {/* ---- THERE IS NO force= ---- */}
      <section id="no-force">
        <div className="wrap">
          {/* The `force=` title stays: it is the page's signature claim and it
              is explained in the next breath by the sub. The EYEBROW was a bare
              function signature, which told a reader nothing they could use. */}
          <SectionHeading eyebrow="The signing step takes no override"
            title={<>There is no <code>force=</code>.</>}
            sub={"It takes no override parameter. Not a disabled one, not an "
              + "environment variable, not an internal flag we hold and you do "
              + "not. A gate with an override is documentation, not a gate."} />
          <ol className="lp-conds">
            {REFUSAL_CONDITIONS.map((c) => (
              <li key={c.h}><b>{c.h}.</b> {c.p}</li>
            ))}
          </ol>
          <p className="lp-conds__foot">
            The hosted certificate route is a second path, and it does not call
            this function. It re-checks the sign-off itself and raises its own
            error in its own words. Two paths, two checks, no override on either.
            We would rather write that than write “every path” and have you grep it.
          </p>
        </div>
      </section>

      {/* ---- WHY IT WAS REFUSED (the wheel, in plain words) ---- */}
      <section id="why-refused">
        <div className="wrap lp-whyref">
          <figure className="lp-whyref__art">
            <CoverageWheel dims={LANDING_WHEEL} closure={0.222} target={0.95}
              size={300} hubLabel="tried"
              label={"A wheel of eight kinds of situation. Four were tried a little,"
                + " between 11 and 50 percent. The other four are not measured"
                + " at all."} />
            <CoverageWheelLegend />
          </figure>
          <div className="lp-whyref__body">
            <Eyebrow>Why we said no</Eyebrow>
            <h2>Because most of it was never tried.</h2>
            {/* THIS PARAGRAPH IS NOW NARROWER THAN THE PRODUCT — a copy decision
                is owed, and this note is the record of why.

                It once named a service being made to time out and a customer
                pushing back. Those were removed as over-claims, correctly: fault
                injection and a simulated user did not exist, and the tense was
                narrowed to what the engine could really read off a recording — a
                tool "comes back" an error, which we OBSERVE and did not cause.

                Both exist now. `scenario/faults.py` stages a timeout on a named
                call and records whether it fired, was skipped, or was never
                reached; `scenario/user.py` drives a customer that holds a fact
                back until the agent asks. So the passive tense here is no longer
                a limit — it is a description of one of two paths, and the page
                still describes only that one.

                Left as-is deliberately rather than widened in passing: restoring
                a marketing claim is a positioning decision, not a correctness
                fix, and this file's history is what happens when copy moves
                ahead of the engine. The engineering note is corrected because a
                false note is the same defect one level up; the copy waits for a
                decision. The closing sentence is pinned by landing.test.tsx. */}
            <p>
              Think of the situations your agent can end up in — the record it
              was handed is incomplete, or two records contradict each other, or
              a tool it called comes back an error, or the thing it does next
              cannot be undone. That whole range is the circle.
            </p>
            <p>
              The filled part is what your tests actually put it through. The gap
              out to the edge is what nobody ever tried. The striped slices are
              kinds of situation nothing even asks about, so they can never fail
              and would never show up in any report.
            </p>
            <p className="lp-whyref__kicker">
              A high score on a small circle is not evidence. It is a smaller
              question, answered well.
            </p>
          </div>
        </div>
      </section>

      {/* ---- ON TOP OF WHAT YOU ALREADY RUN ---- */}
      <section id="ontop">
        <div className="wrap">
          <SectionHeading eyebrow="Where we fit"
            title="We do not replace your testing. We build on it."
            sub="You already run something to check your agent. Keep it. We read what it records and answer the one question it cannot." />
          <div className="lp-grid lp-grid--3">
            {ON_TOP.map((t) => (
              <div className="lp-cell" key={t.h}>
                <h3>{t.h}</h3>
                <p>{t.p}</p>
              </div>
            ))}
          </div>
          <p className="lp-ontop__names">
            Works alongside <b>LangSmith</b>, <b>deepeval</b>, <b>Future AGI</b>,
            <b> Braintrust</b>, <b>Langfuse</b>, public benchmarks, and home-grown
            scripts.
          </p>
        </div>
      </section>

      {/* ---- INSTALL ---- */}
      <section id="install">
        <div className="wrap">
          <SectionHeading eyebrow="Install"
            title="Two lines, and it starts watching."
            sub="Nothing to sign up for and no key needed. One command wires it in, and it can be taken back out just as easily." />

          <div className="lp-install">
            <div className="lp-install__col">
              <h3>One command sets both up</h3>
              <p>It finds your assistant’s settings, adds itself, and keeps a
                copy of the old file beside it. Run it twice and nothing
                happens — and <code>agenttic hook uninstall</code> takes it back
                out without touching anything else.</p>
              <CodeBlock label="set up" lines={[
                { prompt: "$", text: "uv tool install agenttic" },
                { prompt: "$", text: "agenttic hook install" },
                { prompt: "$", text: "agenttic hook verify",
                  comment: "after working as normal" },
              ]} />
            </div>

            <div className="lp-install__col">
              <h3>What those two things do</h3>
              <p><b>It watches.</b> Every file your assistant changes and every
                command it runs is noted, on your machine, so we can tell you
                afterwards what was never tried.</p>
              <p><b>It answers.</b> Your assistant can ask us about a command
                <em> before</em> running it — “is this something I cannot undo?” —
                and get a straight answer instead of finding out afterwards.</p>
              <CodeBlock label="or add it by hand" lines={[
                { text: '"agenttic": {' },
                { text: '  "command": "agenttic",' },
                { text: '  "args": ["mcp"]' },
                { text: "}" },
              ]} />
            </div>
          </div>

          <p className="lp-install__note">
            Everything stays on your machine. The command it never recognises is
            reported as unknown rather than assumed safe.
          </p>
        </div>
      </section>

      {/* ---- HOW IT WORKS ---- */}
      <section id="how">
        <div className="wrap">
          <SectionHeading eyebrow="Where it runs" title="It comes to your environment."
            sub="Your agent, prompts and traces stay where they already are. Pick the surface that matches your constraints." />
          <HowItWorks />
        </div>
      </section>

      {/* ---- PAYOFF ---- */}
      <section id="payoff">
        <div className="wrap">
          <SectionHeading eyebrow="The payoff" title="The result is a verdict, not a number."
            sub="Each criterion is tagged with how it was measured, so you can check it instead of trusting it." />
          <div className="lp-verdict">
            <ProvenanceBadge scorer="code" />
            <ProvenanceBadge scorer="judge" calibrated alpha={0.87} />
            <ProvenanceBadge scorer="judge" calibrated={false} />
          </div>
        </div>
      </section>

      {/* ---- SEE IT (the SAME ScorecardCard as the console) ---- */}
      <section id="see">
        <div className="wrap">
          <SectionHeading eyebrow="See it" title="Your agent's whole run, on one screen."
            sub="The wheel first, then the criteria. This is the order the console renders — coverage before any rate, because a rate with no denominator is an unscoped claim." />
          <div className="lp-see">
            <figure className="lp-see__wheel">
              <CoverageWheel dims={LANDING_WHEEL} closure={0.222} target={0.95}
                             size={280} hubLabel="tried" />
              <CoverageWheelLegend />
            </figure>
            <div className="lp-see__card">
              <ScorecardCard bar="scorecard.html · support-triage · sample data"
                             metrics={SAMPLE_METRICS} rows={SAMPLE_ROWS} />
            </div>
          </div>
        </div>
      </section>

      {/* ---- CHECK ONE YOURSELF ----
          Replaces the old side-by-side table. Three of that table's seven rows
          made categorical claims about competitors' products, which one
          counterexample breaks — on a page arguing it does not make unbounded
          claims. A procedure a stranger can run is stronger than a comparison
          they have to take our word for. */}
      <section id="check-it">
        <div className="wrap">
          <SectionHeading eyebrow="Third-party verification"
            title="Check one without asking us."
            sub={"Verification recomputes every hash from the stored evidence, "
              + "checks the signature against the published key, the binding to "
              + "the deployed config hash, the expiry, and the revocation list."} />
          <Transcript lines={VERIFY_TRANSCRIPT} label="agenttic verify"
                      meta="sample data" />
          <dl className="lp-states">
            {VERIFY_STATES.map((s) => (
              <div className="lp-states__r" key={s.k}>
                <dt>{s.k}</dt><dd>{s.v}</dd>
              </div>
            ))}
          </dl>
          <p className="lp-conds__foot">
            For assurance-tier certificates the public keys are published, as raw
            keys and as JWKS — you do not need to tell us you looked. A locally
            self-attested certificate is signed with a key generated on your own
            machine, so checking one means the holder needs that public key from
            you. We would rather write that sentence than let “anyone can verify
            it” stand for both tiers.
          </p>
        </div>
      </section>

      {/* ---- WHAT THIS CANNOT PROVE ---- */}
      <section id="limits">
        <div className="wrap">
          <SectionHeading eyebrow="Stated plainly" title="What this cannot prove." />
          <div className="lp-limits">
            {LIMITS.map((l) => (
              <div className="lp-limits__i" key={l.h}>
                <h3>{l.h}</h3>
                <p>{l.p}</p>
              </div>
            ))}
          </div>
          <div className="lp-proof" aria-label="formal result states">
            {PROOF_STATES.map((s) => <span key={s}>{s}</span>)}
          </div>
        </div>
      </section>

      {/* ---- CONFIDENCE ---- */}
      <section id="confidence">
        <div className="wrap">
          <SectionHeading eyebrow="Confidence" title="Every score says how it knows."
            sub="Tell what was checked deterministically from what a model judged — and whether that judge has been calibrated against humans." />
          <div className="lp-conf">
            {CONFIDENCE.map((c) => (
              <div className="lp-conf__item" key={c.name}>
                <ProvenanceBadge scorer={c.scorer} calibrated={c.calibrated} alpha={c.alpha} />
                <p><b>{c.name}</b> — {c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- WHAT WE COVER THAT OTHERS DON'T ---- */}
      <section id="cover">
        <div className="wrap">
          <SectionHeading eyebrow="What we cover"
            title="The questions other evaluations structurally cannot answer."
            sub="Not a scoring website. Six things a pass rate cannot express, and we lead with all of them." />
          {/* The first claim is the whole thesis, so it gets the picture rather
              than another paragraph asserting it. */}
          <div className="lp-cover-lead">
            <CoverageWheel dims={LANDING_WHEEL} closure={0.222} target={0.95}
                           size={210} compact hubLabel="tried" />
            <p className="lp-cover-lead__p">
              Eight dimensions of one real run. Filled is what the suite reached;
              everything out to the rim is untested, and the hatched sectors are
              dimensions nothing even asks about. <strong>An unmeasured dimension
              can never fail</strong> — which is why it has to be drawn.
            </p>
          </div>
          <div className="lp-grid lp-grid--3">
            {COVERAGE_CLAIMS.map((t) => (
              <div className="lp-cell" key={t.h}>
                <h3>{t.h}</h3><p>{t.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- DOORS ---- */}
      <section id="doors">
        <div className="wrap">
          <SectionHeading eyebrow="Pick your door" title="Developers run it. The whole team reads it." />
          <div className="lp-doors">
            <div className="lp-door">
              <Eyebrow>For developers</Eyebrow>
              <h3>Find the failures your suite never reaches</h3>
              <ul>
                <li>Catch failures before your users do — including the ones that only show up 1 run in 8.</li>
                <li>Every score opens to its evidence; no black-box numbers.</li>
              </ul>
              <Button href="#cover">See what we cover</Button>
            </div>
            <div className="lp-door">
              <Eyebrow>For teams &amp; buyers</Eyebrow>
              <h3>The evidence is a file your team can share</h3>
              <ul>
                <li>Evaluate a vendor's agent black-box before you deploy it.</li>
                <li>Reliability, policy compliance, and contamination — what procurement actually asks.</li>
              </ul>
              <Button variant="ghost" href="/pricing">How access works</Button>
            </div>
          </div>
        </div>
      </section>

      {/* ---- TRUST ---- */}
      <section id="trust">
        <div className="wrap">
          <SectionHeading eyebrow="Trust" title="Your agent and data never leave your machine."
            sub="Every hosted eval platform asks you to ship your agent, prompts, and traces to someone else's cloud first. Agenttic doesn't: there is no server in the evaluation loop. The harness, the checks and the trace capture run on your hardware — the hosted console only ever holds what you choose to publish, such as a certificate you want a third party to verify." />
          <div className="lp-grid lp-grid--2">
            {TRUST.map((t) => (
              <div className="lp-cell" key={t.h}><h3>{t.h}</h3><p>{t.p}</p></div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- SOCIAL PROOF (gated OFF until real — Hard Rule 49) ---- */}
      {SHOW_SOCIAL_PROOF && (
        <section id="proof">
          <div className="wrap">
            <SectionHeading eyebrow="In the wild" title="In their words." />
            <div className="lp-stats">
              {/* bound to a real source before this flag is turned on */}
              <StatTile tag="Agents verified" value="—" />
              <StatTile tag="Engagements" value="—" />
            </div>
          </div>
        </section>
      )}

      {/* ---- ACCESS ---- */}
      <section id="access">
        <div className="wrap lp-price">
          <Eyebrow>Access</Eyebrow>
          <div className="lp-price__big">Start with the audit.</div>
          <p>Point us at traces you already have — from your own harness, or from
            LangSmith, deepeval, Future AGI, raw OpenTelemetry, whatever you run.
            We hand back the situations your agent has never been put in, the
            properties that were never once exercised, and anything actually broken
            in that traffic. Fixed fee, one week, and it costs us no model calls
            because none of it is judged.</p>
          <p className="lp-price__then">
            Verification engagements follow from there: scope, fit a suite, verify,
            hand back signed evidence. We would rather show you the gap first than
            ask you to buy the fix on faith.
          </p>
          <div className="lp-cta" style={{ justifyContent: "center" }}>
            <Button href="#access">Book a coverage audit</Button>
            <Button variant="ghost" href="/methodology">Read the methodology</Button>
          </div>
        </div>
      </section>

      {/* ---- FAQ ---- */}
      <section id="faq">
        <div className="wrap lp-faq">
          <SectionHeading eyebrow="FAQ" title="The questions we get first." />
          {FAQ.map((f, i) => (
            <FaqItem key={f.q} q={f.q} open={i === 0}>{f.a}</FaqItem>
          ))}
        </div>
      </section>

      {/* ---- CLOSING ---- */}
      <section id="setup">
        <div className="wrap lp-closing">
          <Eyebrow>Start the conversation</Eyebrow>
          <SectionHeading title="Find out what your agent was never tested for." />
          <p style={{ color: "var(--muted)", maxWidth: "52ch", margin: "0 auto var(--sp-6)" }}>
            Bring one agent you already believe is ready. We will show you the part
            of its behaviour nothing has looked at yet.
          </p>
          <div className="lp-cta">
            <Button href="#access">Request a briefing</Button>
            <Button variant="ghost" href="/methodology">Read the methodology</Button>
          </div>
        </div>
      </section>

      {/* ---- FOOTER ---- */}
      <footer>
        <div className="wrap" style={{ padding: "var(--sp-12) var(--sp-8)", color: "var(--muted)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: "var(--sp-4)", fontFamily: "var(--font-mono)", fontSize: "var(--t-label)", letterSpacing: "0.06em" }}>
            <span>© 2026 Agenttic · runs in your environment</span>
            <span>
              <Link to="/methodology">Methodology</Link> · <Link to="/status">Status</Link>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
