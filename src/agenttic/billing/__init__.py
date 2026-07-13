"""Billing — the platform-fee + free-credits + subscriptions layer.

Business model: a PLATFORM FEE with FREE CREDITS. Every tenant gets a one-time
free-credit grant on signup (config-driven) so they can try the tests and the
Copilot chat. Billable actions (Copilot chat, certification/scan runs) DEBIT
credits from an append-only ledger; the balance is the fold of that ledger.
When a tenant runs out and has no active entitlement, the existing 402 path
fires with an honest "out of credits — upgrade or add credits" message.

Credits are integer units and **1 credit == 1 US cent** — so the ledger, the
balance, top-ups, subscription allowances, and invoices are all integer-cents
money math (no floats crossing a money boundary). See :mod:`agenttic.billing.plans`
for the credit-unit config.

Layout:
* :mod:`agenttic.billing.models`   — SQLModel tables (tenant-scoped ledger /
  subscription / invoices; GLOBAL customer-map + webhook-idempotency).
* :mod:`agenttic.billing.plans`    — plan/tier + credit config from ``config.yaml``.
* :mod:`agenttic.billing.store`    — the tenant ``BillingStore`` + ``GlobalBillingStore``.
* :mod:`agenttic.billing.service`  — high-level ops (free-trial grant, metering,
  the 402 entitlement check).
* :mod:`agenttic.billing.credits_provider` — wires the real provider into the
  Copilot credits seam (:mod:`agenttic.copilot.credits`).
* :mod:`agenttic.billing.invoices` — invoice numbering + HTML render.
* :mod:`agenttic.billing.gateways` — Stripe + PayPal (keys from env; TEST/SANDBOX).
* :mod:`agenttic.billing.webhooks` — idempotent apply-event logic (provider-agnostic).
"""

from __future__ import annotations
