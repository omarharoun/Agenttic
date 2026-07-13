"""Deterministic offline mock LLM provider for demos / no-key certification runs.

This is NOT used in the test suite (tests inject their own fakes and mock every
LLM call). It exists so ``agenttic certify --mock`` and the certification demo can
run end-to-end with no network and no API key, producing a real, verifiable
dossier from benign, deterministic responses (Hard Rule 10: no novel harmful
content — the mock only ever emits benign template text).

It emulates the small slice of the Anthropic ``messages.create`` surface the
reference agent + judge use:

* an **agent** request (carries ``tools``) → a single benign ``end_turn`` text
  answer, with a fixed ``confidence`` in the text so calibration has a signal;
* a **judge** request (no tools; asks for a JSON verdict) → a valid
  ``{"score": 1, "rationale": "..."}`` object.
"""

from __future__ import annotations

from types import SimpleNamespace as _NS


def _usage(inp: int = 120, out: int = 40):
    return _NS(input_tokens=inp, output_tokens=out)


def _text_block(t: str):
    return _NS(type="text", text=t)


class MockAnthropicClient:
    """A deterministic stand-in for ``anthropic.Anthropic()``."""

    def __init__(self, agent_answer: str = "Based on policy: 30 days, full refund."):
        self.agent_answer = agent_answer
        self.messages = _NS(create=self._create)

    def _create(self, **kwargs):
        tools = kwargs.get("tools")
        if tools:
            # agent turn — answer directly, no tool call, benign template text
            return _NS(
                stop_reason="end_turn", usage=_usage(),
                content=[_text_block(self.agent_answer + " (confidence: 0.6)")],
            )
        # judge turn — return a valid strict-JSON verdict
        return _NS(
            stop_reason="end_turn", usage=_usage(80, 20),
            content=[_text_block('{"score": 1, "rationale": "benign mock verdict"}')],
        )
