import { useState } from "react";
import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import {
  Button, Eyebrow, SectionHeading, CodeBlock, StatTile, ComparisonTable,
  FaqItem, EscapementMark, ScorecardCard, ProvenanceBadge,
} from "../components/ds";
import {
  SHOW_SOCIAL_PROOF, ASSISTANTS, type TabKey, SAMPLE_METRICS, SAMPLE_ROWS,
  COMPARISON, CONFIDENCE, TOOLKIT, TRUST, FAQ,
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
  const [tab, setTab] = useState<TabKey>("install");
  return (
    <div className="lp-picker">
      <div className="lp-picker__q">What are you building with?</div>
      <div className="lp-assts" role="tablist" aria-label="assistant">
        {ASSISTANTS.map((a) => (
          <button key={a.id} className="lp-asst" role="tab"
                  aria-selected={a.id === asst.id}
                  onClick={() => setAsst(a)}>{a.name}</button>
        ))}
      </div>
      <div className="lp-tabs" role="tablist" aria-label="command">
        {(["install", "eval", "mcp"] as TabKey[]).map((t) => (
          <button key={t} className="lp-tab" role="tab" aria-selected={t === tab}
                  onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="lp-picker__cmd">
        <CodeBlock lines={asst.cmds[tab]} label={`${asst.name} ${tab} commands`} />
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
            <Eyebrow>Verifiable evals · Grounded verdicts</Eyebrow>
            <h1>The evaluation your AI agent can't game.</h1>
            <p className="lp-hero__lede">
              Open-source and on-device. One command scores any agent against a
              rubric fitted to what it actually does. Every score traces to a
              check you can audit.
            </p>
            <div className="lp-cta">
              <Button href="#setup">Get started</Button>
              <Button variant="ghost" href="/methodology">Read the methodology</Button>
            </div>
            <div className="lp-hero__meta">On-device · no telemetry · MIT</div>
          </div>
          <div className="lp-hero__art"><EscapementMark size={280} /></div>
        </div>
      </header>

      {/* ---- HOW IT WORKS ---- */}
      <section id="how">
        <div className="wrap">
          <SectionHeading eyebrow="How it works" title="Install → fit → prove."
            sub="Agenttic installs as a skill in the assistant you already use. Pick yours for the exact commands." />
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

      {/* ---- TOOLKIT ---- */}
      <section id="toolkit">
        <div className="wrap">
          <SectionHeading eyebrow="The toolkit" title="Built for the way you already work." />
          <div className="lp-grid lp-grid--3">
            {TOOLKIT.map((t) => (
              <div className="lp-cell" key={t.code}>
                <code>{t.code}</code><h3>{t.h}</h3><p>{t.p}</p>
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
              <h3>Score your agent in one command</h3>
              <ul>
                <li>Catch failures before your users do — including the ones that only show up 1 run in 8.</li>
                <li>Every score opens to its evidence; no black-box numbers.</li>
              </ul>
              <Button href="#setup">Install in one command</Button>
            </div>
            <div className="lp-door">
              <Eyebrow>For teams &amp; buyers</Eyebrow>
              <h3>The scorecard is a file your team can share</h3>
              <ul>
                <li>Evaluate a vendor's agent black-box before you deploy it.</li>
                <li>Reliability, policy compliance, and contamination — what procurement actually asks.</li>
              </ul>
              <Button variant="ghost" href="/pricing">See pricing</Button>
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
              <StatTile tag="GitHub stars" value="—" />
              <StatTile tag="PyPI downloads" value="—" />
            </div>
          </div>
        </section>
      )}

      {/* ---- PRICING ---- */}
      <section id="pricing">
        <div className="wrap lp-price">
          <Eyebrow>Pricing</Eyebrow>
          <div className="lp-price__big">$0. MIT. Free forever.</div>
          <p>Everything that scores an agent on your machine is open source: the
            harness, the checks, the judge mechanism (bring your own key), the
            reports, the MCP server. No limits, no account, no card.</p>
          <div className="lp-cta" style={{ justifyContent: "center" }}>
            <Button href="#setup">Install the CLI</Button>
            <Button variant="ghost" href="/pricing">Pricing details</Button>
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
          <Eyebrow>Start in one command</Eyebrow>
          <SectionHeading title="Try it on your own agent." />
          <div style={{ maxWidth: 520, margin: "0 auto var(--sp-6)" }}>
            <CodeBlock lines={[{ prompt: "$", text: "uv tool install agenttic" }]} />
          </div>
          <div className="lp-cta">
            <Button href="/scan">Get started</Button>
            <Button variant="ghost" href="/methodology">Read the docs</Button>
          </div>
        </div>
      </section>

      {/* ---- FOOTER ---- */}
      <footer>
        <div className="wrap" style={{ padding: "var(--sp-12) var(--sp-8)", color: "var(--muted)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: "var(--sp-4)", fontFamily: "var(--font-mono)", fontSize: "var(--t-label)", letterSpacing: "0.06em" }}>
            <span>© 2026 Agenttic · MIT · on-device</span>
            <span>
              <Link to="/methodology">Methodology</Link> · <Link to="/pricing">Pricing</Link> · <Link to="/status">Status</Link>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
