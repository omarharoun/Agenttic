# Billing

Agenttic's billing system: a **platform fee with free credits**. Every workspace
gets a one-time free-credit grant so users can try the tests and the Copilot
chat; after that, billable actions spend credits, which users top up
pay-as-you-go or replenish with a monthly subscription. Payments run through
**Stripe** (card) and **PayPal**; charges produce **custom, numbered invoices**.

This document covers the model, the plans (config), the credits ledger, Stripe +
PayPal setup (and which env keys must be set to go live), invoices, and how it
replaces the Copilot stub gate.

---

## The model

- **Credits are integer units, and 1 credit = 1 US cent.** The ledger, the
  balance, top-ups, subscription allowances, and invoices are therefore all
  integer-cents money math — no floats cross a money boundary. The single
  conversion point is `billing.credit_cent_value` (default `1`).
- **Free credits on signup.** A new tenant is granted `billing.free_trial_credits`
  (default 500 = $5.00) the first time it is touched — on signup, on first
  Copilot check, or on first billing dashboard view. The grant is **idempotent**
  (keyed `free-trial:<tenant>`) so it happens exactly once.
- **Metering.** Billable actions debit credits equal to the model budget they
  spent, converted to credits with the platform markup
  (`billing.markup_multiplier`, default 1.5×) and floored at
  `billing.min_action_credits` (default 1). Metered actions today:
  - **Copilot chat** — debited per turn from token usage (priced via the
    `pricing` config), and per executed write/cost tool.
  - **Certification runs** — debited from the run's `cost_usd` (cached runs cost
    nothing, so nothing is debited).
  - **Demo scans** — debited from the scan's `cost_usd`. Scanning your *own*
    endpoint runs on your infra and costs `$0`, so it is never debited or gated.
- **Out of credits → 402.** When a tenant has no credits and no active
  entitlement, the pre-flight gate raises the existing **HTTP 402** with an
  honest message: *"You're out of credits — upgrade your plan or add a credit
  top-up…"*. This is enforced for the Copilot chat, demo scans, and certification
  starts.
- **Tenant isolation.** The ledger, subscription, and invoices are tenant-scoped
  exactly like the Registry (SQLite: DB-per-tenant; Postgres: row-level
  `tenant_id`). A user only ever sees and acts on their own billing.

When `billing.enabled` is `false`, the whole system is a permissive free preview:
usage is not debited and nothing is ever refused.

---

## Plans (config)

Plans, top-ups, and credit parameters live in `config.yaml` under `billing`
(Hard Rule 7 — no hardcoded prices/plans in code). Defaults:

```yaml
billing:
  enabled: true
  currency: usd
  credit_cent_value: 1          # 1 credit == 1 cent
  free_trial_credits: 500       # one-time signup grant ($5.00)
  markup_multiplier: 1.5        # platform fee over metered model cost
  min_action_credits: 1         # a metered action always costs at least 1 credit
  plans:
    free:    { name: Free trial, price_cents: 0,    interval: once,  included_credits: 500 }
    starter: { name: Starter,    price_cents: 2900, interval: month, included_credits: 5000 }
    pro:     { name: Pro,        price_cents: 9900, interval: month, included_credits: 20000, highlight: true }
  topups:
    - { id: topup_10,  name: "$10 credit top-up",  price_cents: 1000,  credits: 1000 }
    - { id: topup_50,  name: "$50 credit top-up",  price_cents: 5000,  credits: 5000 }
    - { id: topup_100, name: "$100 credit top-up", price_cents: 10000, credits: 10000 }
```

Each plan may carry a `features` list (shown on the pricing page) and, for PayPal
subscriptions, a `paypal_plan_id` (the pre-created PayPal plan id). A tenant has
exactly one subscription row: `plan_id` + `status`
(`trialing | active | past_due | canceled`) + `provider` + balance.

---

## The credits ledger

`billing_ledger` is **append-only**. One row per grant or debit; `credits` is
signed (grants positive, debits negative). The **balance is `SUM(credits)`** — it
is never stored, always computed, so it can't drift.

| column | meaning |
|---|---|
| `kind` | `grant` \| `debit` |
| `credits` | signed magnitude (== cents) |
| `reason` | `signup` \| `topup` \| `subscription` \| `copilot` \| `certification` \| `scan` \| `adjustment` |
| `model` | model that incurred a metered debit |
| `dedup_key` | idempotency key for external grants (`stripe:<event_id>` / `free-trial:<tenant>`); unique per tenant when set |
| `meta` | small JSON (tokens, cost_usd, ref) |

Idempotency: a grant with a `dedup_key` that already exists is a **no-op**, so a
webhook replay can never double-credit.

Other tenant-scoped tables: `billing_subscriptions` (one per tenant, upsert) and
`billing_invoices`. Two **global** tables live in the default engine (like
`users`): `billing_customers` (external customer/subscription id → tenant, for
resolving unauthenticated webhooks) and `billing_webhook_events` (processed-event
idempotency log). All tables are created on first use via
`SQLModel.metadata.create_all` — no migration step.

---

## Payments — Stripe + PayPal

Both providers read their keys **only from the environment** (never config, never
code), run in **TEST/SANDBOX** by default, and degrade to `configured: false`
when keys are absent (checkout returns a clear `503`, while free credits, the
ledger, and invoices keep working).

### Stripe (TEST mode)

- Checkout Sessions for **subscriptions** and one-off **credit top-ups**
  (`POST /api/billing/checkout/stripe`). The tenant is stamped into the session
  `metadata` and `client_reference_id` for webhook resolution.
- Webhook `POST /api/billing/webhooks/stripe` — **signature-verified** via
  `stripe.Webhook.construct_event`, then applied idempotently. Handled events:
  - `checkout.session.completed` — activates the subscription (grants included
    credits + invoice) or applies a top-up (grants credits + invoice).
  - `invoice.paid` — recurring renewal: grants another period of credits + invoice.
  - `customer.subscription.updated` / `customer.subscription.deleted` — updates
    plan/status (deletion → back to free/canceled).

**Env keys to go live (Stripe):**

| var | secret? | purpose |
|---|---|---|
| `STRIPE_SECRET_KEY` | **yes** — server only | secret API key — use `sk_test_…` to stay in TEST mode |
| `STRIPE_WEBHOOK_SECRET` | **yes** — server only | webhook signing secret (`whsec_…`), required to verify webhooks |
| `STRIPE_PUBLISHABLE_KEY` | no — exposed to the UI | publishable key (`pk_…`); surfaced via the public `/api/pricing` + `/api/billing/config` so the client can init Stripe.js. Can't move money. |

**Products, prices & webhook (provisioning).** `scripts/stripe_provision.py`
creates the products + recurring prices (Starter/Pro) and one-off top-up prices,
and the webhook endpoint — idempotently, reading `STRIPE_SECRET_KEY` from the env
and never printing it. The resulting **price IDs are NOT secret** and live in
`config.yaml` under each plan/top-up as `stripe_price_id`; checkout uses them
directly (`line_items: [{price: <id>, quantity: 1}]`), falling back to inline
`price_data` when a price id isn't configured. Re-run in live mode and swap the
IDs to go live. The webhook command writes the returned `whsec_…` to `.env`
(gitignored) — never to stdout or git.

```bash
# provision (test mode); price IDs printed are safe to commit, whsec is not
python scripts/stripe_provision.py prices
python scripts/stripe_provision.py webhook https://agenttic.io/api/billing/webhooks/stripe
```

### PayPal (SANDBOX mode)

- Orders (top-ups) and subscriptions via the PayPal REST API
  (`POST /api/billing/checkout/paypal`). The tenant is carried in `custom_id`.
- Webhook `POST /api/billing/webhooks/paypal` — verified via PayPal's
  verify-signature endpoint (requires `PAYPAL_WEBHOOK_ID`), then applied
  idempotently. Handled events: `PAYMENT.CAPTURE.COMPLETED` /
  `CHECKOUT.ORDER.APPROVED` (top-up), `BILLING.SUBSCRIPTION.ACTIVATED`,
  `BILLING.SUBSCRIPTION.CANCELLED` / `.EXPIRED`.

**Env keys to go live (PayPal):**

| var | purpose |
|---|---|
| `PAYPAL_CLIENT_ID` | REST app client id |
| `PAYPAL_SECRET` | REST app secret |
| `PAYPAL_WEBHOOK_ID` | webhook id — required to verify webhook signatures |
| `PAYPAL_ENV` | `sandbox` (default) or `live` |

> To move from test/sandbox to production you must (1) set the live provider keys
> above, (2) register the webhook endpoints with each provider and set the
> resulting signing secret / webhook id, and (3) for PayPal subscriptions, create
> the plans in PayPal and add each `paypal_plan_id` to the matching plan in config.

---

## Invoices

Every charge generates an immutable, numbered invoice
(`AGT-<TENANT>-000001`, sequential per tenant) with line items, integer-cent
amounts, a tax placeholder (0 — no tax engine yet), a total, and the credits
granted. Endpoints (tenant-scoped):

- `GET /api/billing/invoices` — history
- `GET /api/billing/invoices/{id}` — detail (JSON)
- `GET /api/billing/invoices/{id}/download` — a standalone, printable **HTML**
  document (browser → PDF), rendered by `ascore.billing.invoices`.

---

## How it replaces the Copilot stub gate

`ascore.copilot.credits` defines a `CreditsProvider` seam with a permissive stub
(`check` always allows; `record` only logs). Billing supplies the real
implementation — `ascore.billing.credits_provider.BillingCreditsProvider` — and
installs it at app startup:

- On startup the app calls `install_if_default(workspaces, cfg)`, which replaces
  the process-wide provider **only if it is still the default stub** (so a test
  that has swapped in its own provider is never clobbered) and **only when
  billing is enabled**. The previous provider is restored on shutdown.
- `check_credits(tenant)` → grants the free trial on first sight, then allows iff
  the balance is positive; otherwise returns `allowed=False`, which the Copilot
  endpoint turns into the existing **402**. It **fails open** on an internal
  billing error so a billing hiccup can't lock a paying user out.
- `record_usage(...)` / `record_action(...)` → debit real credits from the ledger
  (token cost for a chat turn; known/estimated cost for an executed write action).

The in-memory stopgap daily message cap (`credits.check_daily_cap`) remains as a
secondary belt-and-suspenders bound; the credit gate is now the primary control.

---

## UI

- **Public pricing** — `/pricing` (prerendered, public-bundle-safe; hydrates from
  the unauthenticated `GET /api/pricing`). Plans + the free-credits offer +
  top-ups + a billing FAQ, in the Chronometer design. Linked from the landing
  nav/footer.
- **In-app billing** — `/app/billing`: credit balance, current plan, usage
  breakdown, plan upgrade/downgrade, credit top-ups (Stripe/PayPal buttons appear
  only for a configured provider), and invoice history with download. All data is
  tenant-scoped by the API.

---

## Code map

| path | responsibility |
|---|---|
| `src/agenttic/billing/models.py` | SQLModel tables (tenant + global) |
| `src/agenttic/billing/plans.py` | plan/credit config reads + USD↔credits math |
| `src/agenttic/billing/store.py` | `BillingStore` (tenant) + `GlobalBillingStore` |
| `src/agenttic/billing/service.py` | free-trial grant, metering, the 402 gate, dashboard payload |
| `src/agenttic/billing/credits_provider.py` | wires the real provider into the Copilot seam |
| `src/agenttic/billing/invoices.py` | invoice HTML render |
| `src/agenttic/billing/gateways/stripe_gateway.py` | Stripe checkout + webhook verify |
| `src/agenttic/billing/gateways/paypal_gateway.py` | PayPal orders/subs + webhook verify |
| `src/agenttic/billing/webhooks.py` | idempotent apply-event logic (both providers) |
| `src/agenttic/server/routes/billing.py` | protected dashboard + public pricing + webhooks |
| `ui/src/pages/PricingPage.tsx` | public pricing page |
| `ui/src/pages/BillingPage.tsx` | in-app billing dashboard |
| `tests/test_billing.py` | 19 tests (credits, debits, 402, idempotent webhooks, invoices, lifecycle) |
