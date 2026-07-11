import { useCallback, useEffect, useState } from "react";
import {
  api,
  type BillingOverview,
  type BillingProviderConfig,
  type Invoice,
  type LedgerEntry,
  type PricingCatalog,
} from "../api";
import { money, sharePct } from "../billing";
import { EmptyState, PageHeader, Spinner } from "../components/ui";

/* ============================================================================
   In-app Billing & subscription management (/app/billing).

   Current plan, credit balance + usage, plan upgrade/downgrade, credit top-ups,
   and invoice history + download. All data is tenant-scoped by the API; this
   page only ever shows the signed-in workspace's own billing. Payment actions
   redirect to Stripe/PayPal — the buttons only appear for a configured provider.
   Chronometer design: cards, mono numerals, gilt accent.
   ========================================================================== */

const REASON_LABELS: Record<string, string> = {
  copilot: "Copilot chat",
  certification: "Certification",
  scan: "Agent scans",
  adjustment: "Adjustments",
};

export function BillingPage() {
  const [overview, setOverview] = useState<BillingOverview | null>(null);
  const [catalog, setCatalog] = useState<PricingCatalog | null>(null);
  const [providers, setProviders] = useState<BillingProviderConfig | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.billingOverview(),
      api.billingPlans(),
      api.billingProviderConfig().catch(() => null),
      api.billingInvoices().then((r) => r.invoices).catch(() => []),
      api.billingLedger(20).then((r) => r.entries).catch(() => []),
    ])
      .then(([ov, cat, prov, inv, led]) => {
        setOverview(ov);
        setCatalog(cat);
        setProviders(prov);
        setInvoices(inv);
        setLedger(led);
        setErr(null);
      })
      .catch((e: any) => setErr(e?.message || "Failed to load billing"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const checkout = async (
    provider: "stripe" | "paypal",
    body: { kind: "subscription" | "topup"; plan_id?: string; topup_id?: string },
  ) => {
    setBusy(`${provider}:${body.plan_id || body.topup_id}`);
    setErr(null);
    try {
      const fn = provider === "stripe" ? api.checkoutStripe : api.checkoutPaypal;
      const { url } = await fn(body);
      window.location.href = url;   // redirect to the hosted checkout
    } catch (e: any) {
      setErr(e?.message || `Couldn't start ${provider} checkout`);
      setBusy(null);
    }
  };

  if (loading) {
    return <div className="page"><div className="list-page">
      <PageHeader title="Billing" subtitle="Plan, credits & invoices" />
      <Spinner /></div></div>;
  }

  const stripeOn = !!providers?.stripe.configured;
  const paypalOn = !!providers?.paypal.configured;
  const anyProvider = stripeOn || paypalOn;
  const cur = overview?.currency || "usd";
  const usage = overview?.usage_by_reason || {};
  const usageTotal = Object.values(usage).reduce((a, b) => a + b, 0);

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Billing"
          subtitle="Your plan, credit balance, and invoices"
          actions={<button className="btn-ghost" onClick={load}>Refresh</button>}
        />

        {err && <div className="bill-alert">{err}</div>}

        {/* --- balance + plan summary --- */}
        <div className="bill-top">
          <section className="card bill-balance">
            <div className="card-body">
              <div className="bill-eyebrow">Credit balance</div>
              <div className="bill-balance-amt mono">{overview?.balance_display}</div>
              <div className="bill-balance-sub">
                {overview?.balance_credits?.toLocaleString()} credits
                {overview && !overview.billing_enabled && " · billing disabled (free preview)"}
              </div>
              {anyProvider ? (
                <div className="bill-topups">
                  {catalog?.topups.map((t) => (
                    <button key={t.id} className="btn-ghost sm"
                            disabled={!!busy}
                            onClick={() => checkout(stripeOn ? "stripe" : "paypal",
                              { kind: "topup", topup_id: t.id })}>
                      + {money(t.price_cents, cur)}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="bill-note">Add a payment provider to buy credits
                  (Stripe / PayPal keys not set on this server).</div>
              )}
            </div>
          </section>

          <section className="card bill-plan">
            <div className="card-body">
              <div className="bill-eyebrow">Current plan</div>
              <div className="bill-plan-name">{overview?.plan.name}</div>
              <div className="bill-plan-meta">
                <span className={`badge-status s-${overview?.status}`}>{overview?.status}</span>
                {overview?.plan.price_cents ? (
                  <span className="mono">{money(overview.plan.price_cents, cur)}/{overview.plan.interval}</span>
                ) : <span className="muted">no charge</span>}
              </div>
              {overview?.current_period_end && (
                <div className="bill-note">Renews {overview.current_period_end.slice(0, 10)}</div>
              )}
              {overview?.plan.included_credits ? (
                <div className="bill-note">{overview.plan.included_credits.toLocaleString()} credits
                  included per {overview.plan.interval}</div>
              ) : null}
            </div>
          </section>

          <section className="card bill-usage">
            <div className="card-body">
              <div className="bill-eyebrow">Usage this account</div>
              {usageTotal === 0 ? (
                <div className="bill-note">No credits spent yet — try the Copilot or scan an agent.</div>
              ) : (
                <ul className="bill-usage-list">
                  {Object.entries(usage).sort((a, b) => b[1] - a[1]).map(([reason, credits]) => (
                    <li key={reason}>
                      <span className="u-bar" style={{ width: `${sharePct(credits, usageTotal)}%` }} />
                      <span className="u-label">{REASON_LABELS[reason] || reason}</span>
                      <span className="u-val mono">{money(credits, cur)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>

        {/* --- plans --- */}
        <h2 className="bill-h2">Plans</h2>
        <div className="bill-plans">
          {catalog?.plans.map((p) => {
            const current = p.id === overview?.plan.id;
            const purchasable = p.price_cents > 0 && anyProvider;
            return (
              <div key={p.id} className={`plan${p.highlight ? " hot" : ""}${current ? " current" : ""}`}>
                <div className="plan-head">
                  <span className="plan-name">{p.name}</span>
                  {p.highlight && <span className="plan-tag">Popular</span>}
                </div>
                <div className="plan-price">
                  <span className="amt mono">{money(p.price_cents, cur)}</span>
                  <span className="per">/{p.interval}</span>
                </div>
                <div className="plan-credits">{p.included_credits.toLocaleString()} credits
                  {p.interval === "month" ? " / mo" : ""}</div>
                <ul className="plan-features">
                  {(p.features || []).map((f) => <li key={f}>{f}</li>)}
                </ul>
                <div className="plan-cta">
                  {current ? (
                    <button className="btn-ghost" disabled>Current plan</button>
                  ) : purchasable ? (
                    <>
                      {stripeOn && (
                        <button className="btn-primary" disabled={!!busy}
                                onClick={() => checkout("stripe", { kind: "subscription", plan_id: p.id })}>
                          {busy === `stripe:${p.id}` ? "Redirecting…" : "Upgrade — card"}
                        </button>
                      )}
                      {paypalOn && (
                        <button className="btn-ghost" disabled={!!busy}
                                onClick={() => checkout("paypal", { kind: "subscription", plan_id: p.id })}>
                          PayPal
                        </button>
                      )}
                    </>
                  ) : (
                    <button className="btn-ghost" disabled>
                      {p.price_cents === 0 ? "Free" : "Unavailable"}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* --- invoices --- */}
        <h2 className="bill-h2">Invoices</h2>
        {invoices.length === 0 ? (
          <EmptyState icon="🧾" title="No invoices yet"
                      hint="Invoices appear here after a subscription payment or a credit top-up." />
        ) : (
          <table className="data bill-invoices">
            <thead><tr>
              <th>Number</th><th>Date</th><th>Description</th>
              <th className="num">Amount</th><th>Status</th><th></th>
            </tr></thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.invoice_id}>
                  <td className="mono">{inv.number}</td>
                  <td>{inv.issued_at.slice(0, 10)}</td>
                  <td>{inv.description}</td>
                  <td className="num mono">{money(inv.total_cents, inv.currency)}</td>
                  <td><span className={`badge-status s-${inv.status}`}>{inv.status}</span></td>
                  <td className="num">
                    <a className="link" href={api.invoiceDownloadUrl(inv.invoice_id)}
                       target="_blank" rel="noreferrer">Download</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* --- recent ledger --- */}
        {ledger.length > 0 && (
          <>
            <h2 className="bill-h2">Recent credit activity</h2>
            <table className="data bill-ledger">
              <thead><tr>
                <th>When</th><th>Type</th><th>Detail</th><th className="num">Credits</th>
              </tr></thead>
              <tbody>
                {ledger.map((e) => (
                  <tr key={e.entry_id}>
                    <td>{e.created_at.slice(0, 16).replace("T", " ")}</td>
                    <td>{e.kind === "grant" ? "Grant" : "Debit"}</td>
                    <td>{REASON_LABELS[e.reason] || e.reason}{e.model ? ` · ${e.model}` : ""}</td>
                    <td className={`num mono ${e.credits >= 0 ? "pos" : "neg"}`}>
                      {e.credits >= 0 ? "+" : ""}{e.credits.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
