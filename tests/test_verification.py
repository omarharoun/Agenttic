"""Email verification: token issue / consume / expiry / reuse, and the mailer
console fallback."""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ascore.registry.sqlite_store import EmailTokenRow, Registry
from ascore.server.mailer import Mailer
from ascore.server.users import UserStore
from ascore.server.verification import VerificationStore


def _setup(tmp_path):
    reg = Registry(tmp_path / "v.db")
    UserStore(reg.engine).create_user("u@x.com", "password123", role="admin",
                                      verified=False)
    return reg


class TestVerificationTokens:
    def test_issue_then_consume_marks_verified(self, tmp_path):
        reg = _setup(tmp_path)
        vs = VerificationStore(reg.engine)
        token = vs.issue("u@x.com")
        status, email = vs.consume(token)
        assert status == "ok" and email == "u@x.com"
        assert UserStore(reg.engine).get_by_email("u@x.com").verified is True

    def test_unknown_token_invalid(self, tmp_path):
        reg = _setup(tmp_path)
        assert VerificationStore(reg.engine).consume("nope")[0] == "invalid"

    def test_reuse_rejected(self, tmp_path):
        reg = _setup(tmp_path)
        vs = VerificationStore(reg.engine)
        token = vs.issue("u@x.com")
        assert vs.consume(token)[0] == "ok"
        assert vs.consume(token)[0] == "used"   # single-use

    def test_expired_rejected(self, tmp_path):
        reg = _setup(tmp_path)
        vs = VerificationStore(reg.engine)
        token = vs.issue("u@x.com")
        # force the row to be expired
        with Session(reg.engine) as s:
            row = s.exec(select(EmailTokenRow).where(
                EmailTokenRow.token == token)).first()
            row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            s.add(row); s.commit()
        assert vs.consume(token)[0] == "expired"
        assert UserStore(reg.engine).get_by_email("u@x.com").verified is False

    def test_resend_supersedes_prior_token(self, tmp_path):
        reg = _setup(tmp_path)
        vs = VerificationStore(reg.engine)
        first = vs.issue("u@x.com")
        second = vs.issue("u@x.com")            # resend invalidates the first
        assert vs.consume(first)[0] == "used"
        assert vs.consume(second)[0] == "ok"


class TestMailerFallback:
    def test_console_mode_when_unconfigured(self, monkeypatch):
        # nothing configured -> provider resolves to console, logs, returns False
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("SMTP_HOST", raising=False)
        m = Mailer({"email": {"from": "noreply@agenttic.io"}})
        assert m.settings.provider == "console"
        assert m.send("a@b.com", "hi", "body") is False

    def test_smtp_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "relay.example.com")
        monkeypatch.setenv("SMTP_PORT", "2525")
        monkeypatch.setenv("SMTP_FROM", "noreply@agenttic.io")
        m = Mailer({"email": {"provider": "smtp", "smtp": {"host": "ignored", "port": 587}}})
        assert m.settings.host == "relay.example.com"
        assert m.settings.port == 2525 and m.settings.provider == "smtp"


class TestResendProvider:
    def test_auto_selects_resend_when_key_present(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_123")
        assert Mailer({"email": {"provider": "auto"}}).settings.provider == "resend"

    def test_resend_posts_to_https_api(self, monkeypatch):
        import ascore.server.mailer as mod
        monkeypatch.setenv("RESEND_API_KEY", "re_test_123")
        captured = {}

        def fake_post(url, headers, payload, timeout=15.0):
            captured.update(url=url, headers=headers, payload=payload)
            return 200, '{"id":"abc"}'

        monkeypatch.setattr(mod, "_http_post_json", fake_post)
        m = Mailer({"email": {"provider": "resend", "from": "noreply@agenttic.io"}})
        assert m.send("u@x.com", "Hi", "body", "<b>body</b>") is True
        assert captured["url"] == mod.RESEND_ENDPOINT
        assert captured["headers"]["Authorization"] == "Bearer re_test_123"
        assert captured["payload"]["from"] == "noreply@agenttic.io"
        assert captured["payload"]["to"] == ["u@x.com"]
        assert captured["payload"]["subject"] == "Hi"

    def test_resend_failure_returns_false_no_raise(self, monkeypatch):
        import ascore.server.mailer as mod
        monkeypatch.setenv("RESEND_API_KEY", "re_test_123")
        monkeypatch.setattr(mod, "_http_post_json",
                            lambda *a, **k: (422, '{"error":"bad domain"}'))
        m = Mailer({"email": {"provider": "resend"}})
        assert m.send("u@x.com", "Hi", "body") is False
