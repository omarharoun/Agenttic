"""Secret loading (env + *_FILE) and log redaction."""

import logging

from ascore.secrets import (
    SecretRedactor, get_secret, hydrate_env_secrets, known_secret_values)


def test_get_secret_prefers_file(tmp_path, monkeypatch):
    f = tmp_path / "tok"
    f.write_text("file-secret\n")
    monkeypatch.setenv("ASCORE_API_TOKEN_FILE", str(f))
    monkeypatch.delenv("ASCORE_API_TOKEN", raising=False)
    assert get_secret("ASCORE_API_TOKEN") == "file-secret"


def test_get_secret_env_when_no_file(monkeypatch):
    monkeypatch.delenv("ASCORE_API_TOKEN_FILE", raising=False)
    monkeypatch.setenv("ASCORE_API_TOKEN", "env-secret")
    assert get_secret("ASCORE_API_TOKEN") == "env-secret"


def test_hydrate_copies_file_into_env(tmp_path, monkeypatch):
    f = tmp_path / "key"
    f.write_text("sk-ant-fromfile")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(f))
    hydrate_env_secrets()
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-fromfile"


def test_plain_env_wins_over_file(tmp_path, monkeypatch):
    f = tmp_path / "key"
    f.write_text("from-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(f))
    hydrate_env_secrets()
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "from-env"


def test_known_secret_values_from_config():
    cfg = {"auth": {"token": "supersecrettoken", "tokens": {"another-secret-1": "viewer"}}}
    vals = known_secret_values(cfg)
    assert "supersecrettoken" in vals and "another-secret-1" in vals


class TestRedaction:
    def test_redacts_message_and_extra_fields(self):
        red = SecretRedactor({"supersecrettoken"})
        rec = logging.LogRecord("ascore", logging.INFO, __file__, 1,
                                "auth with supersecrettoken", (), None)
        rec.extra_fields = {"note": "token=supersecrettoken used"}
        assert red.filter(rec) is True
        assert "supersecrettoken" not in rec.getMessage()
        assert "***" in rec.getMessage()
        assert "supersecrettoken" not in rec.extra_fields["note"]

    def test_no_secrets_is_noop(self):
        red = SecretRedactor(set())
        rec = logging.LogRecord("ascore", logging.INFO, __file__, 1,
                                "nothing secret", (), None)
        assert red.filter(rec) is True
        assert rec.getMessage() == "nothing secret"
