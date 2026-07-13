"""Persistence for billing. Two stores mirroring the two table scopes:

* :class:`BillingStore` — bound to one tenant's engine; the ledger, subscription,
  and invoices. Balance is the fold of the append-only ledger.
* :class:`GlobalBillingStore` — bound to the DEFAULT engine; the external-customer
  → tenant map and the webhook-idempotency log (both UNAUTHENTICATED-reachable).

Both call ``SQLModel.metadata.create_all`` on construction (idempotent), like
:class:`agenttic.server.store.UIStore`, so the tables exist the first time billing
is touched — no migration step required.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, SQLModel, select

from agenttic.billing.models import (
    BillingCustomerRow,
    InvoiceRow,
    LedgerRow,
    SubscriptionRow,
    WebhookEventRow,
)
from agenttic.registry.sqlite_store import DEFAULT_TENANT


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _eid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class BillingStore:
    """Tenant-scoped billing persistence. Bound to (engine, tenant); every read
    and write is filtered/stamped by ``tenant_id`` — a tenant can never see or
    touch another tenant's ledger, subscription, or invoices."""

    def __init__(self, engine, tenant: str = DEFAULT_TENANT):
        self.engine = engine
        self.tenant = tenant
        SQLModel.metadata.create_all(engine)

    # -- ledger / balance ---------------------------------------------------

    def balance(self) -> int:
        """Current credit balance = SUM of the append-only ledger. Never < the
        stored rows (debits are negative, grants positive)."""
        with Session(self.engine) as s:
            total = s.exec(select(func.coalesce(func.sum(LedgerRow.credits), 0))
                           .where(LedgerRow.tenant_id == self.tenant)).one()
        return int(total or 0)

    def has_any_ledger(self) -> bool:
        """True once the tenant has any ledger row — i.e. the free trial has been
        granted. Used to grant the trial exactly once."""
        with Session(self.engine) as s:
            row = s.exec(select(LedgerRow.id)
                         .where(LedgerRow.tenant_id == self.tenant).limit(1)).first()
        return row is not None

    def _dedup_exists(self, s: Session, dedup_key: str) -> bool:
        return s.exec(select(LedgerRow.id).where(
            LedgerRow.tenant_id == self.tenant,
            LedgerRow.dedup_key == dedup_key)).first() is not None

    def grant(self, credits: int, reason: str, *, dedup_key: str | None = None,
              meta: dict | None = None) -> bool:
        """Append a positive grant. Idempotent when ``dedup_key`` is given: a
        second grant with the same key is a NO-OP (returns False), so a webhook
        replay can't double-credit. Returns True if a new row was written."""
        if credits <= 0:
            return False
        with Session(self.engine) as s:
            if dedup_key and self._dedup_exists(s, dedup_key):
                return False
            s.add(LedgerRow(
                tenant_id=self.tenant, entry_id=_eid("grant"), kind="grant",
                credits=int(credits), reason=reason, dedup_key=dedup_key,
                meta=json.dumps(meta or {}), created_at=_now()))
            s.commit()
        return True

    def debit(self, credits: int, reason: str, *, model: str = "",
              meta: dict | None = None) -> int:
        """Append a debit (stored as a negative amount). ``credits`` is the
        positive magnitude to deduct; values ≤0 are ignored. Returns the actual
        magnitude debited. We never block on balance here — the pre-flight
        entitlement check (service.ensure_credits / the credits provider) decides
        whether the action runs; metering always records the true spend so the
        balance is honest even if it dips slightly negative on the final turn."""
        credits = int(credits or 0)
        if credits <= 0:
            return 0
        with Session(self.engine) as s:
            s.add(LedgerRow(
                tenant_id=self.tenant, entry_id=_eid("debit"), kind="debit",
                credits=-credits, reason=reason, model=model,
                meta=json.dumps(meta or {}), created_at=_now()))
            s.commit()
        return credits

    def ledger(self, limit: int = 100) -> list[dict]:
        """Recent ledger entries, newest first."""
        with Session(self.engine) as s:
            rows = s.exec(select(LedgerRow)
                          .where(LedgerRow.tenant_id == self.tenant)
                          .order_by(LedgerRow.id.desc()).limit(limit)).all()
        return [{
            "entry_id": r.entry_id, "kind": r.kind, "credits": r.credits,
            "reason": r.reason, "model": r.model,
            "meta": json.loads(r.meta or "{}"),
            "created_at": r.created_at.isoformat(),
        } for r in rows]

    def usage_by_reason(self) -> dict[str, int]:
        """Total credits DEBITED, grouped by reason (positive magnitudes). For the
        in-app usage breakdown (copilot vs certification vs scan)."""
        with Session(self.engine) as s:
            rows = s.exec(select(LedgerRow.reason,
                                 func.coalesce(func.sum(LedgerRow.credits), 0))
                          .where(LedgerRow.tenant_id == self.tenant,
                                 LedgerRow.kind == "debit")
                          .group_by(LedgerRow.reason)).all()
        return {reason: int(-total) for reason, total in rows}

    # -- subscription -------------------------------------------------------

    def get_subscription(self) -> SubscriptionRow | None:
        with Session(self.engine) as s:
            return s.exec(select(SubscriptionRow)
                          .where(SubscriptionRow.tenant_id == self.tenant)).first()

    def upsert_subscription(self, *, plan_id: str, status: str, provider: str = "none",
                            external_id: str = "",
                            current_period_end: datetime | None = None) -> SubscriptionRow:
        with Session(self.engine) as s:
            row = s.exec(select(SubscriptionRow)
                         .where(SubscriptionRow.tenant_id == self.tenant)).first()
            now = _now()
            if row is None:
                row = SubscriptionRow(tenant_id=self.tenant, created_at=now,
                                      updated_at=now)
            row.plan_id = plan_id
            row.status = status
            row.provider = provider
            if external_id:
                row.external_id = external_id
            if current_period_end is not None:
                row.current_period_end = current_period_end
            row.updated_at = now
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    # -- invoices -----------------------------------------------------------

    def next_invoice_number(self, s: Session) -> str:
        """Sequential, human-readable invoice number, scoped to the tenant."""
        n = s.exec(select(func.count(InvoiceRow.id))
                   .where(InvoiceRow.tenant_id == self.tenant)).one()
        seq = int(n or 0) + 1
        slug = (self.tenant or "default").upper().replace("-", "")[:12]
        return f"AGT-{slug}-{seq:06d}"

    def create_invoice(self, *, description: str, line_items: list[dict],
                       provider: str = "none", external_id: str = "",
                       status: str = "paid", currency: str = "usd",
                       tax_cents: int = 0, credits_granted: int = 0,
                       dedup_key: str | None = None) -> dict:
        """Insert an invoice from line items. ``line_items`` is a list of
        ``{description, quantity, unit_cents}``; ``amount_cents`` and the subtotal
        are computed. Idempotent when ``dedup_key`` matches an existing
        ``external_id`` (a webhook replay returns the existing invoice)."""
        with Session(self.engine) as s:
            if dedup_key:
                existing = s.exec(select(InvoiceRow).where(
                    InvoiceRow.tenant_id == self.tenant,
                    InvoiceRow.external_id == dedup_key)).first()
                if existing is not None:
                    return self._invoice_dict(existing)
            items = []
            subtotal = 0
            for li in line_items:
                qty = int(li.get("quantity", 1))
                unit = int(li.get("unit_cents", 0))
                amount = qty * unit
                subtotal += amount
                items.append({"description": li.get("description", ""),
                              "quantity": qty, "unit_cents": unit,
                              "amount_cents": amount})
            total = subtotal + int(tax_cents or 0)
            now = _now()
            row = InvoiceRow(
                tenant_id=self.tenant, invoice_id=_eid("inv"),
                number=self.next_invoice_number(s), provider=provider,
                external_id=external_id or (dedup_key or ""), status=status,
                currency=currency, subtotal_cents=subtotal, tax_cents=int(tax_cents or 0),
                total_cents=total, credits_granted=int(credits_granted or 0),
                line_items=json.dumps(items), description=description,
                issued_at=now, created_at=now)
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._invoice_dict(row)

    def list_invoices(self, limit: int = 100) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(InvoiceRow)
                          .where(InvoiceRow.tenant_id == self.tenant)
                          .order_by(InvoiceRow.id.desc()).limit(limit)).all()
            return [self._invoice_dict(r) for r in rows]

    def get_invoice(self, invoice_id: str) -> dict | None:
        with Session(self.engine) as s:
            row = s.exec(select(InvoiceRow).where(
                InvoiceRow.tenant_id == self.tenant,
                InvoiceRow.invoice_id == invoice_id)).first()
            return self._invoice_dict(row) if row else None

    @staticmethod
    def _invoice_dict(r: InvoiceRow) -> dict:
        return {
            "invoice_id": r.invoice_id, "number": r.number, "provider": r.provider,
            "external_id": r.external_id, "status": r.status, "currency": r.currency,
            "subtotal_cents": r.subtotal_cents, "tax_cents": r.tax_cents,
            "total_cents": r.total_cents, "credits_granted": r.credits_granted,
            "line_items": json.loads(r.line_items or "[]"),
            "description": r.description, "issued_at": r.issued_at.isoformat(),
        }


class GlobalBillingStore:
    """GLOBAL billing persistence (DEFAULT engine): the customer→tenant map and
    the webhook idempotency log. Not tenant-scoped by construction — it exists to
    resolve/guard UNAUTHENTICATED webhooks."""

    def __init__(self, engine):
        self.engine = engine
        SQLModel.metadata.create_all(engine)

    def map_customer(self, provider: str, external_id: str, tenant: str) -> None:
        """Record (or refresh) that ``external_id`` on ``provider`` belongs to
        ``tenant``. Upsert by (provider, external_id)."""
        if not external_id:
            return
        with Session(self.engine) as s:
            row = s.exec(select(BillingCustomerRow).where(
                BillingCustomerRow.provider == provider,
                BillingCustomerRow.external_id == external_id)).first()
            if row is None:
                s.add(BillingCustomerRow(provider=provider, external_id=external_id,
                                         tenant=tenant, created_at=_now()))
            else:
                row.tenant = tenant
                s.add(row)
            s.commit()

    def tenant_for_customer(self, provider: str, external_id: str) -> str | None:
        if not external_id:
            return None
        with Session(self.engine) as s:
            row = s.exec(select(BillingCustomerRow).where(
                BillingCustomerRow.provider == provider,
                BillingCustomerRow.external_id == external_id)).first()
        return row.tenant if row else None

    def already_processed(self, provider: str, event_id: str) -> bool:
        with Session(self.engine) as s:
            row = s.exec(select(WebhookEventRow.id).where(
                WebhookEventRow.provider == provider,
                WebhookEventRow.event_id == event_id)).first()
        return row is not None

    def mark_processed(self, provider: str, event_id: str, event_type: str,
                       tenant: str) -> bool:
        """Record an event as processed. Returns False if it was already recorded
        (idempotency race), True on first insert."""
        with Session(self.engine) as s:
            if s.exec(select(WebhookEventRow.id).where(
                    WebhookEventRow.provider == provider,
                    WebhookEventRow.event_id == event_id)).first() is not None:
                return False
            s.add(WebhookEventRow(provider=provider, event_id=event_id,
                                  event_type=event_type, tenant=tenant,
                                  processed_at=_now()))
            s.commit()
        return True
