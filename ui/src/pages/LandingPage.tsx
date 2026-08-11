import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import {
  Button, Eyebrow, SectionHeading, ScorecardCard, ProvenanceBadge, FaqItem,
} from "../components/ds";
import {
  SPINE, GAP, PRODUCT_STATS, WHY_NOW, SAMPLE_METRICS, SAMPLE_ROWS, FAQ, ON_TOP,
} from "../landing/data";
import "../landing/landing.css";

/* ============================================================================
   The public landing route — the "trust layer" narrative (stage 2).

   Record -> Evaluate -> Assert -> Certify. Six sections against the previous
   nineteen, because the old page was 14.4 viewports and its length was
   whitespace and repetition rather than argument (measured: 19 sections x 168px
   of padding = 3,192px, 23% of the page).

   Two rules this page is built to, both enforced elsewhere and both easy to
   break by accident here:

   * Every colour, size and space comes from design/tokens.css. No raw hex —
     `npm run lint:tokens` fails the build on one.
   * No figure on this page is a customer metric. Each is either SAMPLE DATA
     from one recorded run, labelled where it renders, or a count read off this
     source tree. Fabricated social proof is a hard rule, and a landing page is
     exactly where it creeps in.

   Deliberately absent: "replay" and any version-diff affordance. `cdv.replay()`
   replays a frozen regression scenario, not a production run against sandboxed
   tools, and per-version assertion history has no backend at all — so the page
   does not promise either.
   ========================================================================== */

export function LandingPage() {
  return (
    <div className="lp">
      <SiteNav />

      {/* ---- HERO ------------------------------------------------------- */}
      <header className="lp-hero">
        <div className="wrap lp-hero__grid">
          <div className="lp-hero__copy">
            <Eyebrow>Agent academy · evaluate · assert · certify</Eyebrow>
            <div className="lp-hero__tag">Training playground for serious agents</div>
            <h1>The trust layer for agents.</h1>
            <p className="lp-hero__lede">
              A playground where teams put agents through realistic drills, see
              where their behaviour breaks, and earn evidence before the work
              reaches a customer.
            </p>
            <p className="lp-hero__lede">
              Agenttic records every run an agent takes, evaluates it against the
              tools it touched, and proves the behaviour still holds. Unit tests
              assert on values. We assert on <em>behaviour</em>.
            </p>
            <div className="lp-hero__cta">
              <Button href="/scan" variant="solid">Start a training drill</Button>
              <Button href="/engine" variant="ghost">Explore the playground</Button>
            </div>
            <p className="lp-hero__foot">
              Your traces, your environment, your evidence
            </p>
          </div>

          <div className="lp-hero__lab" aria-label="Sample agent training program">
            <div className="lp-lab__top">
              <span className="lp-lab__live"><i /> LIVE PLAYGROUND</span>
              <span>SUPPORT-01</span>
            </div>
            <div className="lp-lab__body">
              <div className="lp-lab__title">
                <span className="lp-orb" aria-hidden="true">✦</span>
                <div><b>Customer support agent</b><small>Release candidate · v4.2</small></div>
                <span className="lp-lab__ready">READY TO TRAIN</span>
              </div>
              <div className="lp-lab__route" aria-label="Three-stage training route">
                <div className="is-done"><span>01</span><b>Practice</b><small>24 drills</small></div>
                <div className="is-now"><span>02</span><b>Pressure test</b><small>8 scenarios</small></div>
                <div><span>03</span><b>Qualify</b><small>when evidence holds</small></div>
              </div>
              <div className="lp-lab__challenge">
                <span className="lp-challenge__number">07</span>
                <div><small>ACTIVE DRILL</small><b>Refund policy under pressure</b><p>Customer insists on an out-of-policy refund.</p></div>
                <span className="lp-pulse" aria-label="Drill in progress" />
              </div>
            </div>
            <div className="lp-lab__foot">Sample workspace · no customer data</div>
          </div>
        </div>
      </header>

      <section className="lp-program" aria-labelledby="program-title">
        <div className="wrap">
          <div className="lp-program__head">
            <Eyebrow>One place to improve and prove</Eyebrow>
            <h2 id="program-title">Train it. Test it. Certify it.</h2>
            <p>Turn agent reliability into a visible practice, not a launch-day hope.</p>
          </div>
          <div className="lp-program__grid">
            <article className="lp-program__card lp-program__card--train">
              <span className="lp-program__number">01</span>
              <span className="lp-program__icon">↗</span>
              <h3>Training camp</h3>
              <p>Repeat real tasks, grade every attempt, and keep the episodes that teach the agent what good looks like.</p>
              <Link to="/app/training-camp">Open training camp <span>→</span></Link>
            </article>
            <article className="lp-program__card">
              <span className="lp-program__number">02</span>
              <span className="lp-program__icon">⌁</span>
              <h3>Scenario lab</h3>
              <p>Put tools, policies, and customers under pressure in a controlled world that can actually change.</p>
              <Link to="/engine">Explore scenarios <span>→</span></Link>
            </article>
            <article className="lp-program__card lp-program__card--certify">
              <span className="lp-program__number">03</span>
              <span className="lp-program__icon">✦</span>
              <h3>Certification</h3>
              <p>Issue a credential only when the evidence is sufficient — or get the exact work still standing in the way.</p>
              <Link to="/certified">See certified agents <span>→</span></Link>
            </article>
          </div>
        </div>
      </section>

      {/* ---- THE GAP ------------------------------------------------------
          Why the old approach stops working. Everything after this is the
          answer to it, so it goes first. */}
      <section id="gap">
        <div className="wrap">
          <SectionHeading eyebrow={GAP.eyebrow} title={GAP.title} />
          <div className="lp-prose">
            {GAP.body.map((p) => <p key={p.slice(0, 24)}>{p}</p>)}
          </div>
        </div>
      </section>

      {/* ---- WHAT WE BUILT: the four-step spine -------------------------- */}
      <section id="built">
        <div className="wrap">
          <SectionHeading eyebrow="The evidence loop"
            title="Practice is how an agent earns a credential."
            sub={"One SDK call wraps an agent's runtime. Every turn, tool call "
              + "and result is captured as a signed trace. Agenttic then "
              + "evaluates that trace against sandboxed tools, so a regression "
              + "shows up as a step that changed — not as an output that feels "
              + "worse."} />
          <div className="lp-grid lp-grid--2">
            {SPINE.map((s) => (
              <div className="lp-cell" key={s.n}>
                <div className="lp-cell__k">{s.n} · {s.k}</div>
                <h3>{s.h}</h3>
                <p>{s.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- THE PRODUCT ------------------------------------------------- */}
      <section id="product">
        <div className="wrap">
          <SectionHeading eyebrow="Your coaching tape"
            title="A trace timeline an engineer can argue with."
            sub={"Every run opens as a step-by-step timeline: what the model "
              + "decided, which tool it reached for, what came back, and which "
              + "assertion held or broke. Failed steps name the assertion that "
              + "broke and the step it broke on."} />
          <div className="lp-statband">
            {PRODUCT_STATS.map((s) => (
              <div className="lp-statband__c" key={s.lab}>
                <div className="lp-statband__l">{s.lab}</div>
                <div className={"lp-statband__f" + (s.accent ? " is-accent" : "")}>
                  {s.fig}
                </div>
                <div className="lp-statband__s">{s.sub}</div>
              </div>
            ))}
          </div>
          <p className="lp-note">
            Sample data from one recorded run — not a customer figure.
          </p>
          {/* The supporting scorecard stays on the public route: the sample is
              visibly labelled and demonstrates the same criteria view used in
              the console. */}
          <div className="lp-product-scorecard">
            <ScorecardCard
              bar="support-agent v4.2 · sample data"
              metrics={SAMPLE_METRICS}
              rows={SAMPLE_ROWS}
            />
          </div>
          <div className="lp-verdict">
            <ProvenanceBadge scorer="code" />
            <ProvenanceBadge scorer="judge" calibrated alpha={0.87} />
            <ProvenanceBadge scorer="judge" calibrated={false} />
          </div>
        </div>
      </section>

      {/* ---- ON TOP OF WHAT YOU RUN ------------------------------------
          Kept through the repositioning on purpose. The mockup drops it, but
          "we do not replace your testing" is a truthful, differentiating claim,
          and dropping it would leave the page positioned AGAINST the incumbents
          it actually sits on top of. Pinned by landing.test.tsx. */}
      <section id="ontop">
        <div className="wrap">
          <SectionHeading eyebrow="Where we fit"
            title="We do not replace your testing. We build on it."
            sub={"Keep running LangSmith, deepeval, Future AGI, Braintrust, "
              + "Langfuse or your own scripts. We read the recordings they "
              + "already produce and answer the question none of them answer."} />
          <div className="lp-grid lp-grid--3">
            {ON_TOP.map((o) => (
              <div className="lp-cell" key={o.h}>
                <h3>{o.h}</h3>
                <p>{o.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- WHY NOW ----------------------------------------------------- */}
      {/* Inverted deliberately: this is the second dark beat in the scroll, so
          the six light sections after the program cards stop reading as one
          wall. Same inversion as `.lp-program`, not a new treatment. */}
      <section id="why" className="lp-invert">
        <div className="wrap">
          <SectionHeading eyebrow="Why now"
            title={"We cut the time and cost of shipping an agent, and hand you "
              + "the proof it behaved."} />
          <div className="lp-grid lp-grid--3">
            {WHY_NOW.map((w) => (
              <div className="lp-cell" key={w.k}>
                <div className="lp-cell__k">{w.k}</div>
                <p>{w.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- TRUST -------------------------------------------------------
          Two sentences kept through the repositioning because they are QUALIFIED
          claims, and the qualifier is the honest part. "No server in the
          evaluation loop" is true; "there is no server" is not — a hosted
          console, a public certificate page and billing all exist. Likewise a
          baseline that applies to any agent is not a suite fitted to every
          agent. Both are pinned by landing.test.tsx. */}
      <section id="trust">
        <div className="wrap">
          <SectionHeading eyebrow="Trust"
            title="Your agent and data never leave your machine."
            sub={"The harness, the checks and the trace capture run on your "
              + "hardware — there is no server in the evaluation loop. The "
              + "hosted console only holds what you choose to publish."} />
          <div className="lp-prose">
            <p>
              We start from a baseline that applies to any agent and go deeper
              where we have built depth. Any test that cannot tell a good agent
              from a bad one is thrown away.
            </p>
          </div>
        </div>
      </section>

      {/* ---- FAQ + CTA --------------------------------------------------- */}
      <section id="faq">
        <div className="wrap lp-faq">
          <SectionHeading eyebrow="FAQ" title="The questions we get first." />
          {FAQ.map((f) => <FaqItem key={f.q} q={f.q}>{f.a}</FaqItem>)}
        </div>
      </section>

      <section id="setup">
        <div className="wrap lp-cta">
          <SectionHeading eyebrow="Start your first program"
            title="Give your agent a place to get better."
            sub={"Bring one agent you already believe is ready. Start with a drill, "
              + "then carry only earned evidence forward to certification."} />
          <div className="lp-hero__cta">
            <Button href="/scan" variant="solid">Open a live trace</Button>
            <Button href="/app/training-camp" variant="ghost">Enter training camp</Button>
          </div>
        </div>
      </section>

      <footer className="lp-foot">
        <div className="wrap">
          <span>© 2026 Agenttic · runs in your environment</span>
          <span>
            <Link to="/methodology">Methodology</Link> · <Link to="/status">Status</Link>
          </span>
        </div>
      </footer>
    </div>
  );
}

export default LandingPage;
