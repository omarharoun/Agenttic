"""Payment gateways — Stripe + PayPal. Both read their keys ONLY from the
environment (never hardcoded, never from config), run in TEST/SANDBOX mode by
default, and degrade gracefully to ``configured=False`` when keys are absent so
the app boots and the rest of billing (free credits, the ledger, invoices) works
without any payment provider wired.
"""

from __future__ import annotations
