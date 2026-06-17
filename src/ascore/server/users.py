"""User account store — login accounts in the (shared/default) database.

Users are global (looked up by email); each carries a ``role`` and ``tenant_id``
that feed the existing RBAC + tenant scoping. On Postgres this is one shared
table; on SQLite it lives in the default-tenant DB (the auth surface is global).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, func, select

from ascore.registry.sqlite_store import UserRow
from ascore.server.passwords import hash_password, verify_password

ROLES = {"viewer", "operator", "admin"}


class DuplicateUserError(ValueError):
    pass


def _norm(email: str) -> str:
    return email.strip().lower()


class UserStore:
    def __init__(self, engine):
        self.engine = engine

    def count(self) -> int:
        with Session(self.engine) as s:
            return int(s.exec(select(func.count(UserRow.id))).one() or 0)

    def get_by_email(self, email: str) -> UserRow | None:
        with Session(self.engine) as s:
            return s.exec(select(UserRow).where(
                UserRow.email == _norm(email))).first()

    def create_user(self, email: str, password: str, *, role: str = "viewer",
                    tenant: str = "default") -> UserRow:
        if role not in ROLES:
            raise ValueError(f"invalid role {role!r}")
        if not password or len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        email = _norm(email)
        if "@" not in email:
            raise ValueError("invalid email")
        with Session(self.engine) as s:
            if s.exec(select(UserRow).where(UserRow.email == email)).first():
                raise DuplicateUserError(f"user {email} already exists")
            row = UserRow(email=email, password_hash=hash_password(password),
                          role=role, tenant_id=tenant,
                          created_at=datetime.now(timezone.utc))
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def set_password(self, email: str, password: str) -> bool:
        """Reset an existing user's password. Returns False if no such user."""
        if not password or len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        with Session(self.engine) as s:
            row = s.exec(select(UserRow).where(
                UserRow.email == _norm(email))).first()
            if row is None:
                return False
            row.password_hash = hash_password(password)
            s.add(row)
            s.commit()
            return True

    def authenticate(self, email: str, password: str) -> UserRow | None:
        user = self.get_by_email(email)
        if user and verify_password(password, user.password_hash):
            return user
        return None

    def ensure_admin(self, email: str, password: str) -> bool:
        """Create an admin if that email doesn't exist yet. Returns True if
        created. Used for env-driven first-admin bootstrap."""
        if self.get_by_email(email):
            return False
        self.create_user(email, password, role="admin", tenant="default")
        return True
