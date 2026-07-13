"""Persistence for the "Connect your agent" config — one saved connection per
tenant, mirroring the BYO-key pattern (``server/keys.py``).

The auth header **value** is a secret: encrypted at rest with Fernet
(``server/crypto.py``), never returned by the API, never logged — only a masked
``…last4`` is surfaced. The endpoint URL is SSRF-validated at save time (reused
``security.validate_blackbox_url``, ``allow_unresolved=True`` so a brand-new but
not-yet-warm DNS name can still be saved; the request-time check in the adapter
re-validates before any traffic). ``consent`` records that the user confirmed
authorization to test the agent — the scan route blocks without it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from agenttic.connect import ConnectionConfig, Mapping
from agenttic.registry.sqlite_store import AgentConnectionRow
from agenttic.security import validate_blackbox_url
from agenttic.server.crypto import decrypt, encrypt

_DEFAULT = "default"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mask(name: str, last4: str) -> str:
    return f"{name}: ••••{last4}" if last4 else ""


class ConnectionStore:
    """CRUD for a tenant's agent connection. Bound to the tenant registry engine
    (like :class:`agenttic.server.keys.KeyStore`)."""

    def __init__(self, engine, cfg: dict):
        self.engine = engine
        self.cfg = cfg

    # -- internal -----------------------------------------------------------
    def _row(self, s: Session, tenant: str) -> AgentConnectionRow | None:
        return s.exec(select(AgentConnectionRow).where(
            AgentConnectionRow.tenant_id == tenant,
            AgentConnectionRow.name == _DEFAULT)).first()

    # -- writes -------------------------------------------------------------
    def save(self, tenant: str, *, endpoint_url: str, agent_name: str = "",
             preset: str = "generic", request_field: str = "",
             response_path: str = "", model: str = "",
             auth_header_name: str = "", auth_header_value: str = "",
             consent: bool = False) -> dict:
        """Create/update the tenant's connection. Validates the URL for SSRF at
        save time (raises :class:`agenttic.security.UnsafeURLError`). The mapping is
        normalised through :class:`Mapping` so a preset fills its defaults. If
        ``auth_header_value`` is blank on an update, the existing encrypted secret
        is preserved (lets the user edit the URL/mapping without re-typing it)."""
        endpoint_url = endpoint_url.strip()
        if not endpoint_url:
            raise ValueError("Add the HTTPS endpoint URL for your agent.")
        # SSRF gate at SAVE time (scheme + private/metadata ranges).
        validate_blackbox_url(endpoint_url, cfg=self.cfg, allow_unresolved=True)

        m = Mapping.resolve(preset, request_field=request_field,
                            response_path=response_path, model=model)
        auth_header_name = auth_header_name.strip()
        auth_header_value = auth_header_value.strip()

        with Session(self.engine) as s:
            row = self._row(s, tenant)
            now = _now()
            # secret handling: new value → encrypt; blank on update → keep old.
            if auth_header_value:
                ciphertext = encrypt(self.cfg, auth_header_value)
                last4 = auth_header_value[-4:]
            elif row is not None and auth_header_name == row.auth_header_name:
                ciphertext, last4 = row.auth_ciphertext, row.auth_last4
            else:
                ciphertext, last4 = "", ""
            if row is None:
                row = AgentConnectionRow(
                    tenant_id=tenant, name=_DEFAULT, created_at=now, updated_at=now)
            row.agent_name = agent_name.strip() or row.agent_name or "your-agent"
            row.endpoint_url = endpoint_url
            row.preset = m.preset
            row.request_field = m.request_field
            row.response_path = m.response_path
            row.model = m.model
            row.auth_header_name = auth_header_name
            row.auth_ciphertext = ciphertext
            row.auth_last4 = last4
            row.updated_at = now
            if consent and not row.consent:
                row.consent_at = now
            row.consent = bool(consent)
            s.add(row)
            s.commit()
        return self.status(tenant)

    def set_consent(self, tenant: str, consent: bool) -> dict:
        with Session(self.engine) as s:
            row = self._row(s, tenant)
            if row is None:
                raise ValueError("Connect your agent before confirming authorization.")
            if consent and not row.consent:
                row.consent_at = _now()
            row.consent = bool(consent)
            row.updated_at = _now()
            s.add(row)
            s.commit()
        return self.status(tenant)

    def delete(self, tenant: str) -> bool:
        with Session(self.engine) as s:
            row = self._row(s, tenant)
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    # -- reads --------------------------------------------------------------
    def get(self, tenant: str) -> ConnectionConfig | None:
        """The decrypted config for server-side use (building an adapter). NEVER
        return this to a client — use :meth:`status` for the masked view."""
        with Session(self.engine) as s:
            row = self._row(s, tenant)
            if row is None:
                return None
            auth_value = decrypt(self.cfg, row.auth_ciphertext) if row.auth_ciphertext else ""
            return ConnectionConfig(
                endpoint_url=row.endpoint_url, agent_name=row.agent_name,
                preset=row.preset, request_field=row.request_field,
                response_path=row.response_path, model=row.model,
                auth_header_name=row.auth_header_name, auth_header_value=auth_value or "",
                consent=row.consent, consent_at=row.consent_at,
                updated_at=row.updated_at)

    def status(self, tenant: str) -> dict:
        """The safe, API-returnable view — mapping + masked auth, NEVER the
        secret or its ciphertext."""
        with Session(self.engine) as s:
            row = self._row(s, tenant)
        if row is None:
            return {"connected": False}
        return {
            "connected": True,
            "agent_name": row.agent_name,
            "endpoint_url": row.endpoint_url,
            "preset": row.preset,
            "request_field": row.request_field,
            "response_path": row.response_path,
            "model": row.model,
            "auth_header_name": row.auth_header_name,
            "auth_set": bool(row.auth_ciphertext),
            "auth_masked": _mask(row.auth_header_name, row.auth_last4),
            "consent": row.consent,
            "consent_at": row.consent_at.isoformat() if row.consent_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
