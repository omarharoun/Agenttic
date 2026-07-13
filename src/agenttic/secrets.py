"""Secret loading + log redaction.

Secrets come from the environment, with a ``<NAME>_FILE`` convention so
file-mounted secrets (Docker/Kubernetes secrets, Vault agent sidecars, etc.)
work transparently: if ``ASCORE_API_TOKEN_FILE`` points at a file, its contents
are used. ``hydrate_env_secrets()`` (called at startup) copies any ``*_FILE``
secret into the plain env var so libraries that read the env directly (the
Anthropic SDK reads ``ANTHROPIC_API_KEY``) see it.

Rotation: provide overlapping tokens via ``auth.tokens`` (add new, deploy,
remove old) for zero-downtime rotation; the admin token rotates by updating
``ASCORE_API_TOKEN`` and restarting. Secrets are never logged — a
``SecretRedactor`` filter scrubs known secret values from every log record.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Secrets that may be supplied via env or <NAME>_FILE.
SECRET_ENV_NAMES = [
    "ANTHROPIC_API_KEY", "COPILOT_ANTHROPIC_KEY", "ASCORE_API_TOKEN",
    "FI_API_KEY", "FI_SECRET_KEY",
    "ASCORE_DB", "ASCORE_REDIS_URL",
    "ASCORE_SESSION_SECRET", "ASCORE_ADMIN_PASSWORD",
]


def get_secret(name: str) -> str:
    """Value of ``name`` — from ``<name>_FILE`` if set (and readable), else the
    plain env var. Returns "" when unset."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        p = Path(file_path)
        if p.is_file():
            return p.read_text().strip()
    return os.environ.get(name, "").strip()


def hydrate_env_secrets() -> None:
    """Copy any ``<NAME>_FILE`` secret into ``NAME`` so env-reading libraries
    pick it up. Existing plain env vars win (never overwritten)."""
    for name in SECRET_ENV_NAMES:
        if os.environ.get(name):
            continue
        file_path = os.environ.get(f"{name}_FILE")
        if file_path and Path(file_path).is_file():
            os.environ[name] = Path(file_path).read_text().strip()


def known_secret_values(cfg: dict) -> set[str]:
    """All secret strings worth redacting from logs (env + config tokens).
    Short values are excluded to avoid over-redacting incidental text."""
    values: set[str] = set()
    for name in ("ANTHROPIC_API_KEY", "COPILOT_ANTHROPIC_KEY", "ASCORE_API_TOKEN",
                 "FI_API_KEY", "FI_SECRET_KEY"):
        v = get_secret(name)
        if v:
            values.add(v)
    auth = cfg.get("auth", {}) or {}
    if auth.get("token"):
        values.add(str(auth["token"]))
    for tok in (auth.get("tokens", {}) or {}):
        values.add(str(tok))
    return {v for v in values if len(v) >= 6}


class SecretRedactor(logging.Filter):
    """Replaces known secret values with '***' in log messages + extra fields."""

    def __init__(self, secrets: set[str]):
        super().__init__()
        self.secrets = secrets

    def _scrub(self, text: str) -> str:
        for s in self.secrets:
            if s and s in text:
                text = text.replace(s, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            try:
                record.msg = self._scrub(str(record.getMessage()))
                record.args = ()
            except Exception:  # noqa: BLE001 — never let logging crash
                pass
            ef = getattr(record, "extra_fields", None)
            if isinstance(ef, dict):
                for k, v in list(ef.items()):
                    if isinstance(v, str):
                        ef[k] = self._scrub(v)
        return True
