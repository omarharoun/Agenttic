"""Generate a self-contained billing preview (no server needed).

Emits one standalone HTML that embeds the REAL compiled Chronometer CSS and
reproduces, with sample data, the three billing surfaces:
  1. the public pricing section (plans + free-credits offer + top-ups),
  2. the in-app billing & subscription dashboard,
  3. a sample custom invoice (rendered by the real invoice renderer).

Everything is inlined so the file opens anywhere. Sample data only — no secrets.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agenttic.billing.invoices import render_invoice_html  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "billing-preview"
OUT.mkdir(parents=True, exist_ok=True)

DIST = REPO / "ui" / "dist" / "assets"
css = "\n".join(p.read_text() for p in sorted(DIST.glob("*.css")))

# ---- sample data ---------------------------------------------------------- #
PLANS = [
    {"id": "free", "name": "Free trial", "price": "$0", "per": "", "credits": "500 credits to start",
     "features": ["$5.00 in free credits", "Copilot chat + agent tools",
                  "Scan & certify your agent", "Community support"], "hot": False, "cur": False},
    {"id": "starter", "name": "Starter", "price": "$29", "per": "/month", "credits": "5,000 credits every month",
     "features": ["$50 in monthly credits", "Everything in Free", "Custom invoices",
                  "Email support"], "hot": False, "cur": True},
    {"id": "pro", "name": "Pro", "price": "$99", "per": "/month", "credits": "20,000 credits every month",
     "features": ["$200 in monthly credits", "Everything in Starter",
                  "Priority scans & certification", "Priority support"], "hot": True, "cur": False},
]
TOPUPS = [("$10", "1,000 credits"), ("$50", "5,000 credits"), ("$100", "10,000 credits")]
USAGE = [("Copilot chat", 71, "$0.71"), ("Certification", 42, "$0.42"), ("Agent scans", 18, "$0.18")]
INVOICES = [
    ("AGT-ACME-000002", "2026-07-08", "Starter subscription", "$29.00", "paid"),
    ("AGT-ACME-000001", "2026-06-21", "$10 credit top-up", "$10.00", "paid"),
]
LEDGER = [
    ("2026-07-11 09:42", "Debit", "Copilot chat · claude-sonnet-4-6", "-2", "neg"),
    ("2026-07-11 09:15", "Debit", "Certification", "-63", "neg"),
    ("2026-07-08 11:03", "Grant", "Subscription", "+5,000", "pos"),
    ("2026-06-21 16:20", "Grant", "Top-up", "+1,000", "pos"),
    ("2026-06-20 10:00", "Grant", "Signup", "+500", "pos"),
]


def plan_card(p: dict) -> str:
    feats = "".join(f"<li>{f}</li>" for f in p["features"])
    tag = '<span class="plan-tag">Popular</span>' if p["hot"] else ""
    cls = "plan" + (" hot" if p["hot"] else "") + (" current" if p["cur"] else "")
    if p["cur"]:
        cta = '<button class="btn-ghost" disabled>Current plan</button>'
    elif p["price"] == "$0":
        cta = '<button class="btn-ghost" disabled>Free</button>'
    else:
        cta = ('<button class="btn-primary">Upgrade — card</button>'
               '<button class="btn-ghost">PayPal</button>')
    return f"""<div class="{cls}">
      <div class="plan-head"><span class="plan-name">{p['name']}</span>{tag}</div>
      <div class="plan-price"><span class="amt mono">{p['price']}</span><span class="per">{p['per']}</span></div>
      <div class="plan-credits">{p['credits']}</div>
      <ul class="plan-features">{feats}</ul>
      <div class="plan-cta">{cta}</div>
    </div>"""


def pcard(p: dict) -> str:
    feats = "".join(f"<li>{f}</li>" for f in p["features"])
    flag = '<div class="pcard-flag">Most popular</div>' if p["hot"] else ""
    btn = "btn-g" if p["hot"] else "btn-o"
    label = "Start free" if p["price"] == "$0" else f"Choose {p['name']}"
    return f"""<div class="pcard{' hot' if p['hot'] else ''}">
      {flag}
      <div class="pcard-name">{p['name']}</div>
      <div class="pcard-price"><span class="amt mono">{p['price']}</span><span class="per">{p['per']}</span></div>
      <div class="pcard-credits">{p['credits']}</div>
      <ul class="pcard-features">{feats}</ul>
      <a class="btn {btn} pcard-cta" href="#">{label}</a>
    </div>"""


usage_total = sum(u[1] for u in USAGE)
usage_rows = "".join(
    f'<li><span class="u-bar" style="width:{round(c / usage_total * 100)}%"></span>'
    f'<span class="u-label">{lbl}</span><span class="u-val mono">{disp}</span></li>'
    for lbl, c, disp in USAGE)
inv_rows = "".join(
    f'<tr><td class="mono">{n}</td><td>{d}</td><td>{desc}</td>'
    f'<td class="num mono">{amt}</td><td><span class="badge-status s-{st}">{st}</span></td>'
    f'<td class="num"><a class="link" href="sample-invoice.html" target="_blank">Download</a></td></tr>'
    for n, d, desc, amt, st in INVOICES)
ledger_rows = "".join(
    f'<tr><td>{w}</td><td>{k}</td><td>{det}</td><td class="num mono {cls}">{v}</td></tr>'
    for w, k, det, v, cls in LEDGER)
topups = "".join(f'<div class="topup"><span class="topup-amt mono">{a}</span>'
                 f'<span class="topup-credits">{c}</span></div>' for a, c in TOPUPS)
bill_topups = "".join(f'<button class="btn-ghost sm">+ {a}</button>' for a, _ in TOPUPS)

html = f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agenttic Billing — preview</title>
<style>{css}</style>
<style>
  body {{ background: var(--bg); color: var(--text); margin: 0; font-family: var(--font-ui); }}
  .pv-band {{ max-width: 1160px; margin: 0 auto; padding: 8px 30px 24px; }}
  .pv-tag {{ display:inline-block; margin: 28px 0 4px; font: 600 11px/1 var(--font-ui);
    text-transform: uppercase; letter-spacing: .14em; color: var(--accent);
    border: 1px solid var(--accent-line); background: var(--accent-soft);
    border-radius: 999px; padding: 6px 12px; }}
  .pv-h {{ font: 500 22px/1.2 var(--font-serif); margin: 10px 0 2px; color: var(--text); }}
  .pv-sub {{ color: var(--muted); font-size: 13px; margin: 0 0 8px; }}
  .pv-divider {{ border: 0; border-top: 1px solid var(--border); margin: 8px 0 0; }}
  /* emulate the console frame for the dashboard block */
  .pv-console {{ background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
    overflow: hidden; margin-top: 12px; }}
  .pv-console .list-page {{ max-height: none; }}
</style></head>
<body>

<div class="pv-band">
  <span class="pv-tag">Agenttic · Billing system preview (sample data)</span>
  <div class="pv-h">1 · Public pricing page — <code>/pricing</code></div>
  <div class="pv-sub">Plans + free-credits offer + pay-as-you-go top-ups. Prerendered, public-bundle-safe.</div>
</div>

<div class="agx pricing">
  <header class="hero pricing-hero"><div class="wrap">
    <div class="eyebrow">Pricing</div>
    <h1>Start free. Pay for what you meter.</h1>
    <p class="lede">Every workspace gets <b>$5.00 in free credits</b> to try the tests and the
      Copilot — no card required. After that, a simple platform fee: subscribe for monthly
      credits, or top up pay-as-you-go. You're only charged for the model budget your runs
      actually spend.</p>
    <div class="pricing-freebadge"><span class="fb-amt mono">$5.00</span>
      <span class="fb-label">free credits on signup</span></div>
  </div></header>
  <section class="blk"><div class="wrap">
    <div class="plans-grid">{''.join(pcard(p) for p in PLANS)}</div>
  </div></section>
  <section class="blk"><div class="wrap">
    <div class="kick">Pay as you go</div>
    <h2>Or just top up credits.</h2>
    <p class="sub">No subscription needed. 1 credit = 1&#162; of platform value; you always
      see the exact spend in your ledger.</p>
    <div class="topup-row">{topups}</div>
  </div></section>
</div>

<div class="pv-band">
  <hr class="pv-divider">
  <div class="pv-h">2 · In-app billing &amp; subscription dashboard — <code>/app/billing</code></div>
  <div class="pv-sub">Balance, plan, usage, upgrade/top-ups (Stripe/PayPal), invoice history. Tenant-scoped.</div>
</div>

<div class="pv-band">
 <div class="pv-console"><div class="app-shell" style="display:block"><div class="app-body">
  <div class="page"><div class="list-page">
    <div class="list-head"><div><h1 style="font:500 26px/1.15 var(--font-serif);margin:0">Billing</h1>
      <p style="color:var(--muted);margin:4px 0 0">Your plan, credit balance, and invoices</p></div>
      <div><button class="btn-ghost">Refresh</button></div></div>

    <div class="bill-top">
      <section class="card bill-balance"><div class="card-body">
        <div class="bill-eyebrow">Credit balance</div>
        <div class="bill-balance-amt mono">$54.35</div>
        <div class="bill-balance-sub">5,435 credits</div>
        <div class="bill-topups">{bill_topups}</div>
      </div></section>
      <section class="card bill-plan"><div class="card-body">
        <div class="bill-eyebrow">Current plan</div>
        <div class="bill-plan-name">Starter</div>
        <div class="bill-plan-meta"><span class="badge-status s-active">active</span>
          <span class="mono">$29.00/month</span></div>
        <div class="bill-note">Renews 2026-08-08</div>
        <div class="bill-note">5,000 credits included per month</div>
      </div></section>
      <section class="card bill-usage"><div class="card-body">
        <div class="bill-eyebrow">Usage this account</div>
        <ul class="bill-usage-list">{usage_rows}</ul>
      </div></section>
    </div>

    <h2 class="bill-h2">Plans</h2>
    <div class="bill-plans">{''.join(plan_card(p) for p in PLANS)}</div>

    <h2 class="bill-h2">Invoices</h2>
    <table class="data bill-invoices"><thead><tr><th>Number</th><th>Date</th><th>Description</th>
      <th class="num">Amount</th><th>Status</th><th></th></tr></thead>
      <tbody>{inv_rows}</tbody></table>

    <h2 class="bill-h2">Recent credit activity</h2>
    <table class="data bill-ledger"><thead><tr><th>When</th><th>Type</th><th>Detail</th>
      <th class="num">Credits</th></tr></thead><tbody>{ledger_rows}</tbody></table>
  </div></div>
 </div></div></div>
</div>

<div class="pv-band">
  <hr class="pv-divider">
  <div class="pv-h">3 · Sample custom invoice — <code>/api/billing/invoices/&lt;id&gt;/download</code></div>
  <div class="pv-sub">Numbered, itemized, integer-cent totals, printable → PDF. Rendered by the real invoice renderer.</div>
  <div style="margin-top:12px"><a class="btn-primary" href="sample-invoice.html" target="_blank"
     style="display:inline-block;text-decoration:none">Open the sample invoice →</a></div>
</div>

</body></html>"""

(OUT / "billing-preview.html").write_text(html)

# the standalone sample invoice (real renderer)
sample_invoice = {
    "number": "AGT-ACME-000002", "status": "paid", "currency": "usd",
    "issued_at": "2026-07-08T11:03:00", "subtotal_cents": 2900, "tax_cents": 0,
    "total_cents": 2900, "credits_granted": 5000,
    "line_items": [{"description": "Agenttic Starter subscription — 5,000 monthly credits",
                    "quantity": 1, "unit_cents": 2900, "amount_cents": 2900}],
}
(OUT / "sample-invoice.html").write_text(
    render_invoice_html(sample_invoice, tenant="acme"))

print("wrote:", OUT / "billing-preview.html")
print("wrote:", OUT / "sample-invoice.html")
