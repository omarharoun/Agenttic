"""Billing tables. Two scopes, matching the rest of the codebase:

* **Tenant-scoped** (share the tenant's Registry engine, filter by ``tenant_id``):
  the credit ledger, the subscription, and invoices. On SQLite these live in the
  tenant's own DB file (``tenant_id`` stays ``"default"`` within the file); on
  Postgres they share one engine with row-level ``tenant_id`` isolation — exactly
  like :class:`ascore.registry.sqlite_store.Registry`.

* **GLOBAL** (live in the DEFAULT tenant's engine, like ``users`` / ``api_keys`` /
  ``certifications``): the external-customer → tenant map and the webhook-event
  idempotency log. Webhooks arrive UNAUTHENTICATED, so we need a
  tenant-independent place to (a) look up which tenant an external customer
  belongs to and (b) guarantee a replayed event can't double-apply.

Everything money is integer CENTS; every credit is one cent (see plans.py).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint

from ascore.registry.sqlite_store import DEFAULT_TENANT


class LedgerRow(SQLModel, table=True):
    """Append-only credit ledger. One row per grant or debit; the balance is
    ``sum(credits)`` for the tenant. ``credits`` is SIGNED — grants are positive,
    debits negative — so a plain SUM gives the balance and the table is never
    UPDATEd. ``dedup_key`` makes external grants idempotent (a webhook replay with
    the same key is a no-op); it is unique per tenant when set."""
    __tablename__ = "billing_ledger"
    __table_args__ = (UniqueConstraint("tenant_id", "dedup_key"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    entry_id: str = Field(index=True)
    kind: str = Field(index=True)          # grant | debit
    #: signed credits (== cents): grant > 0, debit < 0
    credits: int
    #: signup | topup | subscription | copilot | certification | scan | adjustment
    reason: str = Field(default="", index=True)
    model: str = ""                        # model that incurred a metered debit
    #: idempotency key for external grants (e.g. "stripe:evt_123"); NULL for
    #: internal debits (each debit is its own event). Unique-per-tenant when set.
    dedup_key: str | None = Field(default=None, index=True)
    meta: str = "{}"                       # small JSON blob (tokens, cost_usd, …)
    created_at: datetime


class SubscriptionRow(SQLModel, table=True):
    """The tenant's current plan + billing status. One row per tenant (upsert),
    like ``api_keys``. ``plan_id`` names a plan from ``billing.plans`` config;
    ``status`` is trialing | active | past_due | canceled; ``provider`` is the
    payment provider backing a paid plan (stripe | paypal | none)."""
    __tablename__ = "billing_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    plan_id: str = "free"
    status: str = "trialing"               # trialing | active | past_due | canceled
    provider: str = "none"                 # none | stripe | paypal
    external_id: str = ""                  # provider subscription id
    current_period_end: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InvoiceRow(SQLModel, table=True):
    """One issued invoice — immutable once written (status is the only field that
    changes, on void). Generated per charge (subscription payment or credit
    top-up). Amounts are integer CENTS; ``line_items`` is a JSON list of
    ``{description, quantity, unit_cents, amount_cents}``."""
    __tablename__ = "billing_invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "invoice_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    invoice_id: str = Field(index=True)
    number: str = Field(index=True)        # human invoice number, e.g. AGT-DEFAULT-000001
    provider: str = "none"                 # stripe | paypal | none (manual/topup)
    external_id: str = ""                  # provider invoice/session id
    status: str = "paid"                   # paid | open | void
    currency: str = "usd"
    subtotal_cents: int = 0
    tax_cents: int = 0                     # placeholder (0) — no tax engine yet
    total_cents: int = 0
    credits_granted: int = 0               # credits this charge added to the balance
    line_items: str = "[]"                 # JSON list of line items
    description: str = ""
    issued_at: datetime
    created_at: datetime


class BillingCustomerRow(SQLModel, table=True):
    """GLOBAL map: a payment provider's customer/subscription id → the Agenttic
    tenant it belongs to. Lets an UNAUTHENTICATED webhook resolve the tenant when
    the event doesn't carry our metadata. Lives in the DEFAULT engine (like
    ``users``). Keyed by (provider, external_id)."""
    __tablename__ = "billing_customers"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)
    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True)      # stripe | paypal
    external_id: str = Field(index=True)   # customer id AND/OR subscription id
    tenant: str = Field(index=True)
    created_at: datetime


class WebhookEventRow(SQLModel, table=True):
    """GLOBAL idempotency log for processed provider webhook events. A replayed
    event (same provider+event_id) is recognised and skipped, so credits are
    never applied twice. Lives in the DEFAULT engine."""
    __tablename__ = "billing_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id"),)
    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True)      # stripe | paypal
    event_id: str = Field(index=True)
    event_type: str = ""
    tenant: str = ""
    processed_at: datetime
