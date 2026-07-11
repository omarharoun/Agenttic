"""Custom invoice rendering — a self-contained, printable HTML document per
charge. This is the "custom billing / sending invoices" the product asks for: a
numbered invoice with line items, integer-cent amounts, a tax placeholder, and a
total, that the user can view and download (and print → PDF from the browser).

Kept dependency-free on purpose: an invoice is just an ``InvoiceRow`` rendered to
HTML with inline styles (so it survives download / email with no external CSS).
The visual language matches the Chronometer design (serif headings, mono
numerals, gilt accent), but nothing here imports the frontend.
"""

from __future__ import annotations

from html import escape


def _money(cents: int, currency: str = "usd") -> str:
    sym = {"usd": "$", "eur": "€", "gbp": "£"}.get((currency or "usd").lower(), "$")
    return f"{sym}{int(cents) / 100:,.2f}"


def render_invoice_html(invoice: dict, *, tenant: str, seller: dict | None = None,
                        currency: str = "usd") -> str:
    """Render one invoice dict (from ``BillingStore``) to a standalone HTML page."""
    seller = seller or {
        "name": "Agenttic",
        "tagline": "Agent safety certification",
        "email": "billing@agenttic.io",
    }
    cur = invoice.get("currency", currency)
    rows = []
    for li in invoice.get("line_items", []):
        rows.append(
            "<tr>"
            f"<td class='desc'>{escape(str(li.get('description', '')))}</td>"
            f"<td class='num'>{int(li.get('quantity', 1))}</td>"
            f"<td class='num'>{_money(li.get('unit_cents', 0), cur)}</td>"
            f"<td class='num'>{_money(li.get('amount_cents', 0), cur)}</td>"
            "</tr>")
    line_rows = "\n".join(rows) or (
        "<tr><td class='desc' colspan='4'>No line items</td></tr>")

    status = escape(str(invoice.get("status", "paid"))).upper()
    number = escape(str(invoice.get("number", "")))
    issued = escape(str(invoice.get("issued_at", ""))[:10])
    subtotal = _money(invoice.get("subtotal_cents", 0), cur)
    tax = _money(invoice.get("tax_cents", 0), cur)
    total = _money(invoice.get("total_cents", 0), cur)
    credits = int(invoice.get("credits_granted", 0))
    credits_line = (f"<div class='credits'>+{credits:,} credits added to your "
                    "balance</div>" if credits else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Invoice {number}</title>
<style>
  :root {{ --ink:#16181B; --muted:#6A7077; --line:#E2E1DC; --gold:#8A6D14; --panel:#F8F8F5; }}
  * {{ box-sizing:border-box; }}
  body {{ font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; color:var(--ink);
          background:#ECEDEA; margin:0; padding:40px; }}
  .sheet {{ max-width:720px; margin:0 auto; background:#fff; border:1px solid var(--line);
            border-radius:10px; padding:44px 48px; box-shadow:0 2px 20px rgba(0,0,0,.06); }}
  .top {{ display:flex; justify-content:space-between; align-items:flex-start;
          border-bottom:2px solid var(--ink); padding-bottom:20px; margin-bottom:28px; }}
  .brand {{ font:400 26px/1 Georgia,'Times New Roman',serif; letter-spacing:.01em; }}
  .brand .hex {{ color:var(--gold); }}
  .brand .tag {{ display:block; font:400 12px/1.4 sans-serif; color:var(--muted); margin-top:6px; }}
  .doc {{ text-align:right; }}
  .doc h1 {{ font:400 22px/1 Georgia,serif; letter-spacing:.16em; text-transform:uppercase;
             margin:0 0 8px; }}
  .doc .no {{ font:600 13px/1.4 'SF Mono',ui-monospace,monospace; }}
  .doc .date {{ color:var(--muted); font-size:12px; }}
  .status {{ display:inline-block; margin-top:8px; font:600 11px/1 sans-serif;
             letter-spacing:.08em; padding:5px 10px; border-radius:999px;
             background:rgba(138,109,20,.12); color:var(--gold); border:1px solid rgba(138,109,20,.3); }}
  .meta {{ display:flex; justify-content:space-between; gap:24px; margin-bottom:28px; }}
  .meta .lbl {{ font:600 10px/1 sans-serif; letter-spacing:.1em; text-transform:uppercase;
                color:var(--muted); margin-bottom:6px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:22px; }}
  thead th {{ font:600 10px/1 sans-serif; letter-spacing:.1em; text-transform:uppercase;
              color:var(--muted); text-align:left; padding:0 0 10px; border-bottom:1px solid var(--line); }}
  thead th.num, td.num {{ text-align:right; font-family:'SF Mono',ui-monospace,monospace; }}
  tbody td {{ padding:12px 0; border-bottom:1px solid var(--line); vertical-align:top; }}
  .totals {{ width:280px; margin-left:auto; }}
  .totals .row {{ display:flex; justify-content:space-between; padding:7px 0; }}
  .totals .row.grand {{ border-top:2px solid var(--ink); margin-top:6px; padding-top:12px;
                        font:600 16px/1 sans-serif; }}
  .totals .num {{ font-family:'SF Mono',ui-monospace,monospace; }}
  .credits {{ margin-top:16px; padding:12px 14px; background:var(--panel); border-radius:8px;
              border:1px solid var(--line); color:var(--gold); font-weight:600; }}
  .foot {{ margin-top:34px; padding-top:18px; border-top:1px solid var(--line);
           color:var(--muted); font-size:12px; }}
  @media print {{ body {{ background:#fff; padding:0; }} .sheet {{ border:0; box-shadow:none; }} }}
</style></head>
<body><div class="sheet">
  <div class="top">
    <div class="brand"><span class="hex">&#x2B21;</span> {escape(seller['name'])}
      <span class="tag">{escape(seller.get('tagline', ''))}</span></div>
    <div class="doc">
      <h1>Invoice</h1>
      <div class="no">{number}</div>
      <div class="date">Issued {issued}</div>
      <div class="status">{status}</div>
    </div>
  </div>
  <div class="meta">
    <div><div class="lbl">Billed to</div><div>Workspace <b>{escape(tenant)}</b></div></div>
    <div style="text-align:right"><div class="lbl">From</div>
      <div>{escape(seller['name'])}<br>{escape(seller.get('email', ''))}</div></div>
  </div>
  <table>
    <thead><tr><th>Description</th><th class="num">Qty</th>
      <th class="num">Unit</th><th class="num">Amount</th></tr></thead>
    <tbody>{line_rows}</tbody>
  </table>
  <div class="totals">
    <div class="row"><span>Subtotal</span><span class="num">{subtotal}</span></div>
    <div class="row"><span>Tax</span><span class="num">{tax}</span></div>
    <div class="row grand"><span>Total</span><span class="num">{total}</span></div>
  </div>
  {credits_line}
  <div class="foot">Thank you for using Agenttic. Questions? {escape(seller.get('email', ''))}
    · This invoice was generated by Agenttic's custom billing system.</div>
</div></body></html>"""
