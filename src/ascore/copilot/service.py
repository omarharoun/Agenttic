"""Copilot chat service: build the request, call Claude Sonnet 4.6 server-side,
stream tokens back, and apply the output guards.

The model is called with **Agenttic's own** Anthropic key (server-side), NOT a
tenant's BYO key — the Copilot is a platform-provided assistant. The key is read
from the environment (``COPILOT_ANTHROPIC_KEY`` preferred, else
``ANTHROPIC_API_KEY``); it is NEVER hardcoded. If it isn't configured the service
raises :class:`CopilotNotConfigured`, which the route turns into a clear 503 —
this is the one deploy-time gap (like the passport signing key).

Guards applied here (defense in depth; the route adds rate-limit + credits):
- User content is capped in length and count (context/cost ceiling) and passed as
  ordinary chat turns — the system prompt already instructs the model to treat
  all conversation content as untrusted data, not instructions.
- Streamed output is scrubbed with :func:`ascore.assistant.guard.redact_secrets`
  so no key/secret pattern can be echoed back.
- ``max_tokens`` is capped so a single answer can't run away.

Streaming is a **sync generator** yielding ``(event, data)`` tuples; Starlette
runs it in a threadpool, so the blocking SDK call never stalls the event loop.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

from ascore.assistant.guard import redact_secrets
from ascore.copilot.skill import build_system_prompt

DEFAULT_MODEL = "claude-sonnet-4-6"

# Context / cost ceilings (overridable via the ``copilot`` config block).
MAX_USER_CHARS = 6000          # per user message; longer is truncated
MAX_HISTORY_MESSAGES = 20      # trailing turns kept (user+assistant)
MAX_OUTPUT_TOKENS = 1024       # hard cap on a single answer
# STOPGAP spend caps (in-memory daily message counters; see credits.py). These
# defaults apply even when the deployed config omits a ``copilot`` block, so the
# free-preview Copilot is bounded out of the box. 0/absent disables a cap.
DAILY_CAP_PER_USER = 50        # messages / tenant / UTC day
DAILY_CAP_GLOBAL = 300         # messages / server / UTC day


class CopilotNotConfigured(RuntimeError):
    """Raised when no server-side Anthropic key is configured for the Copilot."""


@dataclass
class CopilotConfig:
    model: str = DEFAULT_MODEL
    max_user_chars: int = MAX_USER_CHARS
    max_history_messages: int = MAX_HISTORY_MESSAGES
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    daily_cap_per_user: int = DAILY_CAP_PER_USER
    daily_cap_global: int = DAILY_CAP_GLOBAL

    @classmethod
    def from_cfg(cls, cfg: dict | None) -> "CopilotConfig":
        c = (cfg or {}).get("copilot", {}) or {}
        models = (cfg or {}).get("models", {}) or {}
        return cls(
            model=str(c.get("model") or models.get("agent_default") or DEFAULT_MODEL),
            max_user_chars=int(c.get("max_user_chars", MAX_USER_CHARS)),
            max_history_messages=int(c.get("max_history_messages", MAX_HISTORY_MESSAGES)),
            max_output_tokens=int(c.get("max_output_tokens", MAX_OUTPUT_TOKENS)),
            daily_cap_per_user=int(c.get("daily_message_cap_per_user", DAILY_CAP_PER_USER)),
            daily_cap_global=int(c.get("daily_message_cap_global", DAILY_CAP_GLOBAL)),
        )


def server_side_key() -> str:
    """The Copilot's own Anthropic key, from the environment. Empty if unset."""
    return (os.environ.get("COPILOT_ANTHROPIC_KEY")
            or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def is_configured(injected: dict | None = None) -> bool:
    """True if the Copilot can run — either a test/dev client is injected or a
    server-side key is present in the environment."""
    if injected and (injected.get("copilot") or injected.get("anthropic")):
        return True
    return bool(server_side_key())


def resolve_client(injected: dict | None = None):
    """Return an Anthropic-style client for the Copilot.

    Prefers an injected client (tests/dev wire ``clients['copilot']`` or
    ``clients['anthropic']``). Otherwise builds a real client from the
    server-side key. Raises :class:`CopilotNotConfigured` if neither exists —
    never falls back to a tenant key."""
    if injected:
        client = injected.get("copilot") or injected.get("anthropic")
        if client is not None:
            return client
    key = server_side_key()
    if not key:
        raise CopilotNotConfigured(
            "The Copilot assistant isn't configured on this server "
            "(no COPILOT_ANTHROPIC_KEY / ANTHROPIC_API_KEY set).")
    import anthropic
    return anthropic.Anthropic(api_key=key)


def sanitize_messages(messages: list[dict], cfg: CopilotConfig) -> list[dict]:
    """Normalize the client-supplied conversation into Anthropic message dicts.

    Keeps only ``user``/``assistant`` roles, coerces content to text, truncates
    over-long messages, drops empties, keeps the trailing ``max_history_messages``
    turns, and ensures the exchange ends on a user turn. This is the context cap
    and the point where the transcript is normalized to untrusted text."""
    norm: list[dict] = []
    for m in messages or []:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue
        if len(content) > cfg.max_user_chars:
            content = content[:cfg.max_user_chars] + "\n[…truncated]"
        norm.append({"role": role, "content": content})
    norm = norm[-cfg.max_history_messages:]
    # Anthropic requires the first message to be a user turn and the last to be
    # user (we're asking for a completion). Trim a leading assistant turn and
    # refuse if there's no user turn to answer.
    while norm and norm[0]["role"] == "assistant":
        norm.pop(0)
    while norm and norm[-1]["role"] == "assistant":
        norm.pop()
    return norm


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class CopilotService:
    """Stateless per-request service. Build with the resolved client + config."""

    def __init__(self, client, cfg: CopilotConfig | None = None, *,
                 system_prompt: str | None = None,
                 extra_secrets: set[str] | None = None):
        self.client = client
        self.cfg = cfg or CopilotConfig()
        self.system_prompt = system_prompt or build_system_prompt()
        self.extra_secrets = extra_secrets or set()

    def _redact(self, text: str) -> str:
        return redact_secrets(text, extra=self.extra_secrets)

    def stream(self, messages: list[dict]) -> Iterator[tuple[str, object]]:
        """Yield ``("token", str)`` chunks as the answer streams, then
        ``("usage", Usage)`` once, then ``("done", None)``. On an upstream error
        yields ``("error", str)`` (never the raw exception/internal detail).

        Secret patterns are scrubbed from every chunk before it leaves the
        server. Chunking can split a secret across boundaries, so we also hold
        back a small tail to catch a pattern straddling two deltas."""
        convo = sanitize_messages(messages, self.cfg)
        if not convo:
            yield ("error", "no user message to answer")
            return
        usage = Usage()
        pending = ""  # buffer to catch secrets split across chunk boundaries
        try:
            with self.client.messages.stream(
                model=self.cfg.model,
                max_tokens=self.cfg.max_output_tokens,
                system=self.system_prompt,
                messages=convo,
            ) as stream:
                for delta in stream.text_stream:
                    if not delta:
                        continue
                    pending += delta
                    # emit everything except a trailing window that a secret
                    # pattern could still be growing into
                    if len(pending) > 48:
                        emit, pending = pending[:-48], pending[-48:]
                        yield ("token", self._redact(emit))
                if pending:
                    yield ("token", self._redact(pending))
                try:
                    final = stream.get_final_message()
                    u = getattr(final, "usage", None)
                    if u is not None:
                        usage.input_tokens = int(getattr(u, "input_tokens", 0) or 0)
                        usage.output_tokens = int(getattr(u, "output_tokens", 0) or 0)
                except Exception:  # noqa: BLE001 — usage is best-effort
                    pass
            yield ("usage", usage)
            yield ("done", None)
        except CopilotNotConfigured:
            raise
        except Exception:  # noqa: BLE001 — never leak upstream internals
            yield ("error", "The Copilot ran into a problem reaching the model. "
                            "Please try again in a moment.")
