import { useState } from "react";
import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import {
  Button, Eyebrow, SectionHeading, CodeBlock, StatTile, ComparisonTable,
  FaqItem, EscapementMark, ScorecardCard, ProvenanceBadge,
} from "../components/ds";
import {
  SHOW_SOCIAL_PROOF, ASSISTANTS, type TabKey, SAMPLE_METRICS, SAMPLE_ROWS,
  COMPARISON, CONFIDENCE, COVERAGE_CLAIMS, TRUST, FAQ,
} from "../landing/data";
import "../landing/landing.css";

/* ============================================================================
   The public landing route (SPEC-11 Step 52). Rebuilt from the shared design
   tokens + the ds component library — no bespoke markup, no second style world.
   The see-it scorecard is the SAME <ScorecardCard> the console renders. All
   social proof is gated behind SHOW_SOCIAL_PROOF (OFF until real, Hard Rule 49),
   so with the flag off the page ships clean with those sections simply absent.
   Public route: SiteNav only, no authenticated data or console chrome.
   ========================================================================== */

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

export function LandingPage() {
  return (
    <div className="lp">
      <SiteNav />

      {/* ---- HERO ---- */}
      <header className="lp-hero">
        <div className="wrap lp-hero__grid">
          <div>
            <Eyebrow>Agent verification</Eyebrow>
            <h1>Everyone tells you what passed. We tell you what was never tested.</h1>
            <p className="lp-hero__lede">
              A pass rate is a report on the cases somebody thought to write. We
              measure the space your agent was actually put through, hold it to its
              properties on every run, and prove what can be proven — then lead with
              what is still untested.
            </p>
            <div className="lp-cta">
              <Button href="#access">Request a briefing</Button>
              <Button variant="ghost" href="#cover">See what we cover</Button>
            </div>
            <div className="lp-hero__meta">
              Runs in your environment · your keys · nothing leaves it
            </div>
          </div>
          <div className="lp-hero__art"><EscapementMark size={280} /></div>
        </div>
      </header>

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
            sub="Each row is one criterion; the badge is how it was scored. This is the same component the console renders with your real data." />
          <ScorecardCard bar="scorecard.html · support-triage · sample data"
                         metrics={SAMPLE_METRICS} rows={SAMPLE_ROWS} />
        </div>
      </section>

      {/* ---- WHY A RUBRIC / SIDE BY SIDE ---- */}
      <section id="why">
        <div className="wrap">
          <SectionHeading eyebrow="Why a rubric, not a benchmark"
            title="Every score traces to how it was measured."
            sub="Leaderboards hand every agent the same test and one number. Real agents do different jobs, and a number you can't open is a number you can't trust." />
          <ComparisonTable columns={COMPARISON.columns} rows={COMPARISON.rows} />
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
            sub="Every hosted eval platform asks you to ship your agent, prompts, and traces to someone else's cloud first. Agenttic doesn't, because it can't: there is no server in the loop." />
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
          <div className="lp-price__big">Sold as an engagement.</div>
          <p>We scope the agent, stand the verification up against it, and hand back
            evidence your risk function can actually read. Availability is limited
            while we work with a small number of teams.</p>
          <div className="lp-cta" style={{ justifyContent: "center" }}>
            <Button href="#access">Request a briefing</Button>
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
