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
    def test_console_mode_when_unconfigured(self):
        # no SMTP host -> logs instead of sending, returns False, never raises
        m = Mailer({"email": {"from": "noreply@agenttic.io"}})
        assert m.settings.configured is False
        assert m.send("a@b.com", "hi", "body") is False

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "relay.example.com")
        monkeypatch.setenv("SMTP_PORT", "2525")
        monkeypatch.setenv("SMTP_FROM", "noreply@agenttic.io")
        m = Mailer({"email": {"smtp": {"host": "ignored", "port": 587}}})
        assert m.settings.host == "relay.example.com"
        assert m.settings.port == 2525 and m.settings.configured is True
