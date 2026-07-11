import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { HexMark } from "../components/Icons";
import { money } from "../billing";
import type { PricingCatalog } from "../api";

/* ============================================================================
   Public pricing page (/pricing) — plans + the free-credits offer.

   Bundle-safe & prerenderable: it renders from a STATIC default catalog (so it
   emits real HTML at build time and works with no JS), then hydrates from the
   public /api/pricing endpoint on mount if the server's config differs. No app
   imports — it stays in the small public bundle, matching the Chronometer
   landing design (the .agx token system in theme.css).
   ========================================================================== */

/** Static fallback catalog — mirrors config.yaml `billing` defaults so the page
 *  prerenders fully. Live values from /api/pricing override this on mount. */
const DEFAULT_CATALOG: PricingCatalog = {
  currency: "usd",
  free_trial_credits: 500,
  credit_cent_value: 1,
  plans: [
    { id: "free", name: "Free trial", price_cents: 0, interval: "once", included_credits: 500,
      features: ["$5.00 in free credits", "Copilot chat + agent tools", "Scan & certify your agent", "Community support"] },
    { id: "starter", name: "Starter", price_cents: 2900, interval: "month", included_credits: 5000,
      features: ["$50 in monthly credits", "Everything in Free", "Custom invoices", "Email support"] },
    { id: "pro", name: "Pro", price_cents: 9900, interval: "month", included_credits: 20000, highlight: true,
      features: ["$200 in monthly credits", "Everything in Starter", "Priority scans & certification", "Priority support"] },
  ],
  topups: [
    { id: "topup_10", name: "$10 credit top-up", price_cents: 1000, credits: 1000 },
    { id: "topup_50", name: "$50 credit top-up", price_cents: 5000, credits: 5000 },
    { id: "topup_100", name: "$100 credit top-up", price_cents: 10000, credits: 10000 },
  ],
};

export function PricingPage() {
  const [catalog, setCatalog] = useState<PricingCatalog>(DEFAULT_CATALOG);

  // Progressive enhancement: pull the live catalog (config-driven) if reachable.
  useEffect(() => {
    let alive = true;
    fetch("/api/pricing")
      .then((r) => (r.ok ? r.json() : null))
      .then((c: PricingCatalog | null) => { if (alive && c?.plans?.length) setCatalog(c); })
      .catch(() => { /* keep static defaults */ });
    return () => { alive = false; };
  }, []);

  const freeCredits = money(catalog.free_trial_credits * catalog.credit_cent_value, catalog.currency);

  return (
    <div className="agx pricing">
      <nav>
        <div className="wrap">
          <Link to="/" className="brand"><HexMark /> Agenttic</Link>
          <div className="nl">
            <Link to="/methodology">Methodology</Link>
            <Link to="/pricing">Pricing</Link>
            <Link to="/login">Log in</Link>
            <Link className="cta" to="/signup">Start free</Link>
          </div>
        </div>
      </nav>

      {/* ===================== HERO ===================== */}
      <header className="hero pricing-hero">
        <div className="wrap">
          <div className="eyebrow">Pricing</div>
          <h1>Start free. Pay for what you meter.</h1>
          <p className="lede">
            Every workspace gets <b>{freeCredits} in free credits</b> to try the
            tests and the Copilot — no card required. After that, a simple platform
            fee: subscribe for monthly credits, or top up pay-as-you-go. You're
            only charged for the model budget your runs actually spend.
          </p>
          <div className="pricing-freebadge">
            <span className="fb-amt mono">{freeCredits}</span>
            <span className="fb-label">free credits on signup</span>
          </div>
        </div>
      </header>

      {/* ===================== PLANS ===================== */}
      <section className="blk" id="plans">
        <div className="wrap">
          <div className="plans-grid">
            {catalog.plans.map((p) => (
              <div key={p.id} className={`pcard${p.highlight ? " hot" : ""}`}>
                {p.highlight && <div className="pcard-flag">Most popular</div>}
                <div className="pcard-name">{p.name}</div>
                <div className="pcard-price">
                  <span className="amt mono">{money(p.price_cents, catalog.currency, true)}</span>
                  <span className="per">{p.price_cents === 0 ? "" : `/${p.interval}`}</span>
                </div>
                <div className="pcard-credits">
                  {p.included_credits.toLocaleString()} credits
                  {p.interval === "month" ? " every month" : " to start"}
                </div>
                <ul className="pcard-features">
                  {(p.features || []).map((f) => <li key={f}>{f}</li>)}
                </ul>
                <Link className={`btn ${p.highlight ? "btn-g" : "btn-o"} pcard-cta`}
                      to="/signup">
                  {p.price_cents === 0 ? "Start free" : `Choose ${p.name}`}
                </Link>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== TOP-UPS ===================== */}
      <section className="blk" id="topups">
        <div className="wrap">
          <div className="kick">Pay as you go</div>
          <h2>Or just top up credits.</h2>
          <p className="sub">
            No subscription needed. Buy a bundle of credits and spend them across
            the Copilot, scans, and certification. 1 credit = 1&#162; of platform
            value; you always see the exact spend in your ledger.
          </p>
          <div className="topup-row">
            {catalog.topups.map((t) => (
              <div className="topup" key={t.id}>
                <span className="topup-amt mono">{money(t.price_cents, catalog.currency, true)}</span>
                <span className="topup-credits">{t.credits.toLocaleString()} credits</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== FAQ / trust ===================== */}
      <section className="blk" id="pricing-faq">
        <div className="wrap">
          <div className="kick">How billing works</div>
          <h2>Honest, metered, transparent.</h2>
          <div className="faq-grid">
            <div className="faq"><h3>What's a credit?</h3>
              <p>A credit is 1&#162; of platform value. Billable actions — Copilot
                chat, agent scans, certification runs — debit credits based on the
                model budget they spend, with the platform fee included.</p></div>
            <div className="faq"><h3>What's free?</h3>
              <p>Every new workspace gets {freeCredits} in credits automatically.
                Scanning your own agent endpoint costs nothing — it runs on your
                infrastructure. You only spend credits on metered model work.</p></div>
            <div className="faq"><h3>How do I pay?</h3>
              <p>Card via Stripe or your PayPal account. Subscribe for monthly
                credits or top up any time. Every charge generates a numbered
                invoice you can download.</p></div>
            <div className="faq"><h3>Can I cancel?</h3>
              <p>Any time. You keep the credits you've already been granted, and
                you drop back to the free plan — no lock-in.</p></div>
          </div>
          <div className="cta-row" style={{ justifyContent: "center", marginTop: "40px" }}>
            <Link className="btn btn-g" to="/signup">Start with {freeCredits} free</Link>
            <Link className="btn btn-o" to="/methodology">Read the methodology</Link>
          </div>
        </div>
      </section>

      <footer>
        <div className="wrap">
          <div className="legal">
            Prices in {catalog.currency.toUpperCase()}. A credit is a unit of
            platform value (1 credit = 1&#162;). Metered actions are billed on the
            model budget they consume plus the platform fee; scanning your own
            endpoint is free. See your workspace ledger for exact, itemized spend.
          </div>
        </div>
      </footer>
    </div>
  );
}
