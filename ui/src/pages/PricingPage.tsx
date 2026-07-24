import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import { Button, Eyebrow, SectionHeading, FaqItem } from "../components/ds";
import { money } from "../billing";
import type { PricingCatalog } from "../api";
import "../landing/landing.css";

/* ============================================================================
   Public access page (/pricing).

   Rebuilt for the closed-source position: Agenttic is scoped and sold as a
   verification engagement, not licensed by the seat and not downloaded. There is
   deliberately NO public price list — a number here would be a number invented
   before the scope is known, and this product exists to refuse invented numbers.

   What the page DOES state plainly: what the engagement is, what comes back at
   the end of it, and what moves the price. The console's metered preview (the
   Copilot and hosted runs, which really do bill credits) is disclosed at the
   bottom rather than hidden — it is a real cost, just not the thing being sold.

   Built on the SAME `.lp` layout + ds components as the landing route, so the
   public surface is one design world (SPEC-11). Bundle-safe & prerenderable: it
   renders fully from static content and only hydrates the live credit figure
   from the public /api/pricing endpoint on mount.
   ========================================================================== */

/** Mirrors config.yaml `billing` defaults so the page prerenders a real figure.
 *  Live values from /api/pricing override this on mount. */
const DEFAULT_CATALOG: Pick<
  PricingCatalog, "currency" | "free_trial_credits" | "credit_cent_value"
> = { currency: "usd", free_trial_credits: 500, credit_cent_value: 1 };

/** The engagement, in the order it actually happens. */
const PHASES = [
  { n: "01", h: "Scope",
    p: "We work out what your agent is for, what it is allowed to touch, and what would count as a failure your business cares about. This is the part that decides whether the rest is worth anything." },
  { n: "02", h: "Fit",
    p: "A suite is built for that agent specifically, and then put through a discrimination gate: unless it can separate a known-good agent from a known-bad one, it is rejected and rebuilt. A test that everything passes measures nothing." },
  { n: "03", h: "Verify",
    p: "The agent is run against it under your keys, in your environment. Properties are watched across every run, coverage of the situation space is measured, and the parts of the system where a question is decidable are decided rather than sampled." },
  { n: "04", h: "Hand back",
    p: "You get the evidence, signed and scoped — including, in writing, the situations nothing has exercised yet. We would rather hand you a short list of real conclusions than a long list of green ticks." },
];

/** What exists at the end. Deliverables, not features. */
const DELIVERABLES = [
  { h: "A verification report",
    p: "Led by coverage closure and property results — what was exercised, what held, and what was never once put to the test. The pass rate is in there, labelled with the scope it actually describes." },
  // NB: worded to avoid the platform's banned-claim substrings (schema/
  // attestation.BANNED_CLAIMS). That guard is a blunt substring match and cannot
  // tell a claim from a denial of it — so shipped copy is worded around the
  // phrases rather than the guard being loosened.
  { h: "Signed evidence",
    p: "An evidence manifest bound to the exact agent configuration tested, with an expiry date and a revocation path. It attests to what was measured, under which conditions — never to how the agent will behave in a situation nobody tested." },
  { h: "A supply-chain record",
    p: "An agent bill of materials covering the models, prompts, tools and MCP servers behind the agent, with the component-level results for the ones we certified in their own right." },
  { h: "A sign-off pack",
    p: "The verification plan mapped to requirements, with untested requirements flagged as untested rather than quietly omitted. Written to be read by someone who has to sign their name under it." },
  { h: "The failures, reproducibly",
    p: "Every finding comes with the trace that produced it and the seed to produce it again. Nothing rests on you taking our word for it." },
  { h: "Re-verification on drift",
    p: "Agents change, models are updated underneath them, and evidence goes stale. Where the engagement covers it, drift triggers re-evaluation and can suspend a certificate automatically." },
];

/** Honest scope factors. No numbers — the numbers depend on these. */
const FACTORS = [
  { h: "How many agents", p: "One agent is an engagement. A fleet sharing tools and memory is a different one, and usually cheaper per agent." },
  { h: "How much of the space", p: "A targeted look at the behaviour that worries you costs less than driving coverage closure up across the whole situation space." },
  { h: "How deep the proof goes", p: "Deterministic checks and property monitoring come as standard. Exhaustive decision over the parts that admit it is more work, and worth it where it applies." },
  { h: "Where it has to run", p: "Your CI is straightforward. Your VPC is routine. Fully air-gapped takes more setup, and we support it." },
  { h: "The supply chain", p: "Certifying the tools, MCP servers and memory an agent depends on is separately scoped, because each is a subject in its own right." },
  { h: "How often", p: "A point-in-time verification differs from standing re-verification with drift monitoring and a live revocation list." },
];

export function PricingPage() {
  const [cat, setCat] = useState(DEFAULT_CATALOG);

  // Progressive enhancement: pull the live figure (config-driven) if reachable.
  useEffect(() => {
    let alive = true;
    fetch("/api/pricing")
      .then((r) => (r.ok ? r.json() : null))
      .then((c: PricingCatalog | null) => {
        if (alive && c && typeof c.free_trial_credits === "number") {
          setCat({
            currency: c.currency ?? DEFAULT_CATALOG.currency,
            free_trial_credits: c.free_trial_credits,
            credit_cent_value: c.credit_cent_value ?? DEFAULT_CATALOG.credit_cent_value,
          });
        }
      })
      .catch(() => { /* keep static defaults */ });
    return () => { alive = false; };
  }, []);

  const freeCredits = money(cat.free_trial_credits * cat.credit_cent_value, cat.currency);

  return (
    <div className="lp">
      <SiteNav />

      {/* ---- HERO ---- */}
      <header className="lp-hero">
        <div className="wrap">
          <Eyebrow>Access</Eyebrow>
          <h1>Priced by what has to be verified.</h1>
          <p className="lp-hero__lede">
            Agenttic is scoped and sold as a verification engagement — not licensed
            by the seat, not downloaded, not a subscription to a dashboard. There is
            no price list on this page, because a price set before the scope is
            known is a number nobody can stand behind.
          </p>
          <div className="lp-cta">
            <Button href="/#access">Request a briefing</Button>
            <Button variant="ghost" href="#factors">What sets the price</Button>
          </div>
          <div className="lp-hero__meta">
            Runs in your environment · your keys · nothing leaves it
          </div>
        </div>
      </header>

      {/* ---- THE ENGAGEMENT ---- */}
      <section id="engagement">
        <div className="wrap">
          <SectionHeading
            eyebrow="The engagement"
            title="Four steps, in this order."
            sub="The order matters. Most evaluation work starts at step three, against a suite nobody checked could tell a good agent from a bad one." />
          <div className="lp-grid lp-grid--2">
            {PHASES.map((s) => (
              <div className="lp-cell" key={s.n}>
                <code>{s.n}</code>
                <h3>{s.h}</h3>
                <p>{s.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- DELIVERABLES ---- */}
      <section id="deliverables">
        <div className="wrap">
          <SectionHeading
            eyebrow="What comes back"
            title="You are buying evidence, not access."
            sub="Everything below is a file you keep. If the engagement ended tomorrow, these would still be yours and would still verify." />
          <div className="lp-grid lp-grid--3">
            {DELIVERABLES.map((d) => (
              <div className="lp-cell" key={d.h}>
                <h3>{d.h}</h3><p>{d.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- WHAT SETS THE PRICE ---- */}
      <section id="factors">
        <div className="wrap">
          <SectionHeading
            eyebrow="What sets the price"
            title="Six things, and we will tell you which ones apply on the first call."
            sub="No seats, no per-run metering on the verification itself, no tier that quietly withholds the honest part of the report." />
          <div className="lp-grid lp-grid--3">
            {FACTORS.map((f) => (
              <div className="lp-cell" key={f.h}>
                <h3>{f.h}</h3><p>{f.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- ACCESS ---- */}
      <section id="access">
        <div className="wrap lp-price">
          <Eyebrow>Access</Eyebrow>
          <div className="lp-price__big">Sold as an engagement.</div>
          <p>
            We scope the agent, stand the verification up against it, and hand back
            evidence your risk function can actually read. Availability is limited
            while we work with a small number of teams.
          </p>
          <div className="lp-cta" style={{ justifyContent: "center" }}>
            <Button href="/#access">Request a briefing</Button>
            <Button variant="ghost" href="/methodology">Read the methodology</Button>
          </div>
        </div>
      </section>

      {/* ---- FAQ ---- */}
      <section id="faq">
        <div className="wrap lp-faq">
          <SectionHeading eyebrow="FAQ" title="What people ask about cost." />
          <FaqItem q="Why is there no price on the pricing page?" open>
            Because the work is not the same size twice. Verifying one internal
            agent against the behaviour that worries you, and driving coverage
            closure across a fleet that shares tools and memory, are different
            engagements with different costs. We would rather quote you after one
            conversation than anchor you to a number invented before it.
          </FaqItem>
          <FaqItem q="Is there a free tier?">
            Not of the verification itself. The console has a metered preview you
            can try — see below — but a free verification engagement would be a
            shallow one, and a shallow verification is the exact thing this product
            exists to argue against.
          </FaqItem>
          <FaqItem q="Do you charge per run, or per seat?">
            Neither. The engagement is scoped up front and priced against that
            scope. Running your suite more times does not cost you more from us;
            it costs you whatever your own model provider charges, under your own
            keys, because the runs happen in your environment.
          </FaqItem>
          <FaqItem q="What happens when our agent changes?">
            The evidence is bound to the exact configuration tested, so a changed
            agent invalidates it by construction rather than silently inheriting a
            clean result. Standing re-verification — drift monitoring, automatic
            suspension, a live revocation list — is a scope option, not a surprise
            line item.
          </FaqItem>
          <FaqItem q="Can we run it ourselves afterwards?">
            Yes. The point of the handover is that the suite, the evidence and the
            reproduction seeds are yours. What you are paying for is building the
            thing correctly and being told the truth about what it did not cover.
          </FaqItem>
        </div>
      </section>

      {/* ---- THE METERED PREVIEW (disclosed, not sold) ---- */}
      <section id="preview">
        <div className="wrap lp-prose">
          <Eyebrow>The console preview</Eyebrow>
          <SectionHeading title="One thing that is metered, stated plainly." />
          <p>
            Separate from any engagement, the console has a preview you can use with
            a workspace account. Its model-backed features — the Copilot, and scans
            we run rather than you — spend credits, because they spend real model
            budget. A credit is one cent of platform value, new workspaces start
            with {freeCredits} of them, and every debit is itemised in your ledger.
          </p>
          <p>
            Scanning your own endpoint from your own infrastructure costs nothing,
            and is not metered at all. If you already have a workspace, your exact
            balance and itemised spend are in{" "}
            <Link to="/app/billing">billing</Link>.
          </p>
          <div className="lp-note">
            The preview is a way to look at the product. It is not the verification
            engagement, and it does not produce signed evidence.
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
