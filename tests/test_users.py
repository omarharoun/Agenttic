"""User accounts: password hashing, session tokens, UserStore, migration v3."""

import time

import pytest

from agenttic.registry.sqlite_store import Registry, UserRow
from agenttic.server.passwords import hash_password, verify_password
from agenttic.server.sessions import session_secret, sign_session, verify_session
from agenttic.server.users import DuplicateUserError, UserStore


class TestPasswords:
    def test_hash_verify(self):
        h = hash_password("correct horse battery staple")
        assert h != "correct horse battery staple"
        assert verify_password("correct horse battery staple", h)
        assert not verify_password("wrong", h)

    def test_long_password_not_truncated(self):
        # >72 bytes: bcrypt would truncate; the sha256 pre-hash prevents it
        a = "x" * 100 + "A"
        b = "x" * 100 + "B"
        h = hash_password(a)
        assert verify_password(a, h) and not verify_password(b, h)


class TestSessions:
    def test_round_trip(self):
        t = sign_session({"uid": 1, "role": "admin", "tenant": "default"},
                         "secret", 3600)
        body = verify_session(t, "secret")
        assert body["uid"] == 1 and body["role"] == "admin"

    def test_tampered_or_wrong_secret_rejected(self):
        t = sign_session({"uid": 1}, "secret", 3600)
        assert verify_session(t, "other-secret") is None
        assert verify_session(t[:-2] + "xy", "secret") is None

    def test_expired_rejected(self):
        t = sign_session({"uid": 1}, "secret", -1)  # already expired
        assert verify_session(t, "secret") is None

    def test_secret_falls_back_to_api_token(self, monkeypatch):
        monkeypatch.delenv("AGENTTIC_SESSION_SECRET", raising=False)
        s = session_secret({"auth": {"token": "admintoken"}})
        assert "admintoken" in s


class TestUserStore:
    def test_create_authenticate(self, tmp_path):
        store = UserStore(Registry(tmp_path / "u.db").engine)
        u = store.create_user("Alice@Example.com ", "hunter2hunter", role="operator",
                              tenant="acme")
        assert u.email == "alice@example.com"  # normalized
        assert u.role == "operator" and u.tenant_id == "acme"
        assert store.authenticate("alice@example.com", "hunter2hunter").id == u.id
        assert store.authenticate("alice@example.com", "nope") is None

    def test_duplicate_rejected(self, tmp_path):
        store = UserStore(Registry(tmp_path / "u.db").engine)
        store.create_user("a@b.com", "password1")
        with pytest.raises(DuplicateUserError):
            store.create_user("A@B.com", "password2")

    def test_validation(self, tmp_path):
        store = UserStore(Registry(tmp_path / "u.db").engine)
        with pytest.raises(ValueError):
            store.create_user("a@b.com", "short")       # <8 chars
        with pytest.raises(ValueError):
            store.create_user("noemail", "password1")   # no @
        with pytest.raises(ValueError):
            store.create_user("a@b.com", "password1", role="superuser")

    def test_ensure_admin_idempotent(self, tmp_path):
        store = UserStore(Registry(tmp_path / "u.db").engine)
        assert store.ensure_admin("admin@x.com", "password1") is True
        assert store.ensure_admin("admin@x.com", "password1") is False
        assert store.get_by_email("admin@x.com").role == "admin"
        assert store.count() == 1

    def test_set_password_resets_and_validates(self, tmp_path):
        store = UserStore(Registry(tmp_path / "u.db").engine)
        store.create_user("a@b.com", "password1", role="admin")
        assert store.authenticate("a@b.com", "password1")
        assert store.set_password("a@b.com", "newpassword2") is True
        assert store.authenticate("a@b.com", "newpassword2")
        assert store.authenticate("a@b.com", "password1") is None  # old revoked
        assert store.set_password("nobody@b.com", "whatever9") is False
        with pytest.raises(ValueError):
            store.set_password("a@b.com", "short")


def test_migration_v3_creates_users_table(tmp_path):
    reg = Registry(tmp_path / "m.db")
    # fresh DB: users table exists from the schema
    from sqlalchemy import inspect
    assert "users" in inspect(reg.engine).get_table_names()
    # and it's usable
    UserStore(reg.engine).create_user("x@y.com", "password1")
