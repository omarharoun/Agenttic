"""Per-tenant Anthropic API keys: encrypted storage + run-client wiring.

Each tenant supplies its OWN Anthropic key. It is encrypted at rest (see
:mod:`ascore.server.crypto`), never logged, and never returned by the API — only
a masked ``sk-ant-…last4`` is surfaced. Every Anthropic call made for a tenant's
run is built from that tenant's key; the platform/global key is never used for a
tenant run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from ascore.registry.sqlite_store import ApiKeyRow
from ascore.server.crypto import decrypt, encrypt


def _now() -> datetime:
    return datetime.now(timezone.utc)


def mask(last4: str) -> str:
    return f"sk-ant-…{last4}"


class KeyStore:
    def __init__(self, engine, cfg: dict):
        self.engine = engine
        self.cfg = cfg

    def set_key(self, tenant: str, plaintext: str, provider: str = "anthropic") -> None:
        plaintext = plaintext.strip()
        if len(plaintext) < 8:
            raise ValueError("that doesn't look like a valid API key")
        ct = encrypt(self.cfg, plaintext)
        last4 = plaintext[-4:]
        with Session(self.engine) as s:
            row = s.exec(select(ApiKeyRow).where(
                ApiKeyRow.tenant_id == tenant,
                ApiKeyRow.provider == provider)).first()
            if row is None:
                s.add(ApiKeyRow(tenant_id=tenant, provider=provider, ciphertext=ct,
                                last4=last4, created_at=_now(), updated_at=_now()))
            else:
                row.ciphertext = ct
                row.last4 = last4
                row.updated_at = _now()
                s.add(row)
            s.commit()

    def get_key(self, tenant: str, provider: str = "anthropic") -> str | None:
        """Decrypted plaintext for server-side use only (building a client)."""
        with Session(self.engine) as s:
            row = s.exec(select(ApiKeyRow).where(
                ApiKeyRow.tenant_id == tenant,
                ApiKeyRow.provider == provider)).first()
        return decrypt(self.cfg, row.ciphertext) if row else None

    def status(self, tenant: str, provider: str = "anthropic") -> dict:
        """Safe, API-returnable status — masked only, never the key."""
        with Session(self.engine) as s:
            row = s.exec(select(ApiKeyRow).where(
                ApiKeyRow.tenant_id == tenant,
                ApiKeyRow.provider == provider)).first()
        if row is None:
            return {"set": False, "masked": None, "updated_at": None}
        return {"set": True, "masked": mask(row.last4),
                "updated_at": row.updated_at.isoformat()}

    def delete(self, tenant: str, provider: str = "anthropic") -> bool:
        with Session(self.engine) as s:
            row = s.exec(select(ApiKeyRow).where(
                ApiKeyRow.tenant_id == tenant,
                ApiKeyRow.provider == provider)).first()
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


def validate_anthropic_key(key: str) -> tuple[bool, str]:
    """Cheap liveness check: list models (no token spend). Returns (ok, msg).
    Monkeypatched in tests to avoid network."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        client.models.list(limit=1)
        return (True, "")
    except Exception as exc:  # noqa: BLE001 — surface a friendly message
        name = type(exc).__name__
        if "Authentication" in name or "401" in str(exc):
            return (False, "Anthropic rejected this key (authentication failed)")
        return (False, f"could not validate key: {name}")


def build_tenant_clients(key: str) -> dict:
    """One Anthropic client (built from the tenant key) wired to every Anthropic
    call site in a run — agent, judge, generator. Shared client is thread-safe."""
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    return {"agent": client, "judge": client, "generator": client, "anthropic": client}
