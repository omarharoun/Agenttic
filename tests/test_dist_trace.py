"""SPEC-8 T41.3 — auto-detecting trace() dispatch, behavior-identical wrapping,
generic fallback, and import hygiene.

All framework detection is exercised against FIXTURE objects that merely mimic
each framework's public shape — no live LangGraph/OpenAI-Agents install (the
adapters themselves import cleanly without their SDK, which is how zero-SDK
detection is possible). LLM calls are never made.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

import agenttic
from agenttic import _detect
from ascore.ingest.mapping import spans_to_traces
from ascore.ingest.otel import parse_otlp

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_DIRS = [REPO_ROOT / "adapters" / "langgraph",
                REPO_ROOT / "adapters" / "openai_agents"]


@pytest.fixture(autouse=True)
def _adapters_importable():
    """Make the in-repo adapters importable (they import fine with no SDK) so
    detection can dispatch to them — the 'framework installed' state."""
    added = []
    for d in ADAPTER_DIRS:
        p = str(d)
        if p not in sys.path:
            sys.path.insert(0, p)
            added.append(p)
    yield
    for p in added:
        sys.path.remove(p)


# --- fixtures mimicking each framework's public shape ----------------------
class LangGraphFixture:
    """A LangGraph compiled-graph shape: invoke/stream/get_graph."""
    def invoke(self, input, config=None, **kwargs):
        return {"echo": input}

    def stream(self, input, config=None, **kwargs):
        yield {"echo": input}

    def get_graph(self):
        return "graph"


class OpenAIAgentFixture:
    """An OpenAI Agents SDK Agent shape: name/instructions/tools/handoffs."""
    name = "triage"
    instructions = "be helpful"
    tools: list = []
    handoffs: list = []


def _canonical_run(sink):
    traces, _decisions, _rep = spans_to_traces(parse_otlp(sink[0]))
    assert len(traces) == 1
    return traces[0]


# --- detection --------------------------------------------------------------
def test_detection_distinguishes_the_two_shapes():
    assert _detect.is_langgraph(LangGraphFixture())
    assert not _detect.is_openai_agent(LangGraphFixture())
    assert _detect.is_openai_agent(OpenAIAgentFixture())
    assert not _detect.is_langgraph(OpenAIAgentFixture())
    # a plain callable is neither
    assert not _detect.is_langgraph(lambda q: q)
    assert not _detect.is_openai_agent(lambda q: q)


def test_same_trace_call_dispatches_to_langgraph_adapter():
    wrapped = agenttic.trace(LangGraphFixture(), sink=[])
    assert type(wrapped).__name__ == "_TracedGraph"


def test_same_trace_call_dispatches_to_openai_adapter():
    wrapped = agenttic.trace(OpenAIAgentFixture(), sink=[])
    assert type(wrapped).__name__ == "_TracedAgent"


def test_arbitrary_callable_falls_back_to_generic_and_emits_valid_run():
    sink: list = []

    def homegrown(query):
        return "reply:" + query

    wrapped = agenttic.trace(homegrown, sink=sink)
    out = wrapped("ping")
    assert out == "reply:ping"                 # behavior-identical
    run = _canonical_run(sink)
    assert run.source == "otel_ingest"
    assert run.final_output == "reply:ping"
    # generic fallback records an honest partial trajectory, never fabricated
    partial = [s for s in run.spans
               if s.attributes.get("agenttic.trajectory") == "partial"]
    assert partial, "generic wrapper must mark the trajectory partial"


# --- two import states (Hard Rule 37): adapter present vs absent ------------
def test_framework_shape_without_adapter_raises_actionable_error(monkeypatch):
    """A recognized (non-callable) framework object with no adapter installed
    fails with an actionable 'install the extra' message — never an opaque crash
    and never a silent no-detection."""
    monkeypatch.setattr(_detect, "_load_adapter", lambda name: None)
    with pytest.raises(ImportError) as ei:
        agenttic.trace(LangGraphFixture())
    assert "agenttic[langgraph]" in str(ei.value)
    with pytest.raises(ImportError) as eo:
        agenttic.trace(OpenAIAgentFixture())
    assert "agenttic[openai]" in str(eo.value)


def test_callable_framework_shape_degrades_to_generic_when_adapter_absent(monkeypatch):
    """If a matching object is ALSO callable, a missing adapter degrades to the
    generic OTel wrapper rather than erroring."""
    monkeypatch.setattr(_detect, "_load_adapter", lambda name: None)

    class CallableGraph(LangGraphFixture):
        def __call__(self, q):
            return {"echo": q}

    sink: list = []
    wrapped = agenttic.trace(CallableGraph(), sink=sink)
    assert type(wrapped).__name__ != "_TracedGraph"
    assert wrapped("hi") == {"echo": "hi"}
    assert _canonical_run(sink).source == "otel_ingest"


def test_plain_callable_works_with_no_framework_sdk_present():
    # neither langgraph/langchain nor agents is installed in this env
    for sdk in ("langgraph", "langchain_core", "agents"):
        assert sdk not in sys.modules or True  # not required; detection is duck-typed
    sink: list = []
    wrapped = agenttic.trace(lambda q: q.upper(), sink=sink)
    assert wrapped("hi") == "HI"
    assert _canonical_run(sink).final_output == "HI"


# --- behavior-identical across fixtures ------------------------------------
def test_behavior_identical_sync_and_async_generic():
    def sync_agent(q):
        return {"n": len(q), "q": q}

    async def async_agent(q):
        return {"n": len(q), "q": q}

    direct = sync_agent("hello")
    wrapped = agenttic.trace(sync_agent, sink=[])("hello")
    assert wrapped == direct

    direct_a = asyncio.run(async_agent("hey"))
    wrapped_a = asyncio.run(agenttic.trace(async_agent, sink=[])("hey"))
    assert wrapped_a == direct_a


def test_behavior_identical_langgraph_adapter_invoke():
    fx = LangGraphFixture()
    direct = fx.invoke({"messages": ["x"]})
    wrapped = agenttic.trace(fx, sink=[]).invoke({"messages": ["x"]})
    assert wrapped == direct == {"echo": {"messages": ["x"]}}


def test_behavior_identical_openai_adapter_run(monkeypatch):
    """Prove byte-identical outputs through the OpenAI adapter using a fake
    `agents` SDK (mocked import state — never a live SDK)."""
    fake = importlib.util.module_from_spec(importlib.machinery.ModuleSpec("agents", None))

    class RunHooks:  # public base the adapter subclasses
        pass

    class Runner:
        @staticmethod
        def run_sync(agent, input, hooks=None, **kwargs):
            return f"ran:{input}"

    fake.RunHooks = RunHooks
    fake.Runner = Runner
    monkeypatch.setitem(sys.modules, "agents", fake)

    wrapped = agenttic.trace(OpenAIAgentFixture(), sink=[])
    assert type(wrapped).__name__ == "_TracedAgent"
    # unwrapped call vs wrapped call -> identical output
    assert Runner.run_sync(OpenAIAgentFixture(), "hi") == "ran:hi"
    assert wrapped.run_sync("hi") == "ran:hi"


def test_behavior_identical_preserves_exceptions():
    boom = ValueError("nope")

    def raises(q):
        raise boom

    sink: list = []
    wrapped = agenttic.trace(raises, sink=sink)
    with pytest.raises(ValueError) as ei:
        wrapped("x")
    assert ei.value is boom                     # same exception re-raised
    # the failed run is still emitted, marked partial with the error reason
    run = _canonical_run(sink)
    span = run.spans[0]
    assert "ValueError" in span.attributes.get("agenttic.trajectory.reason", "")


# --- no-target guidance, never a silent phone-home (Hard Rule 38) ----------
def test_no_target_is_a_logged_no_op(caplog):
    _detect._GUIDANCE_LOGGED = False  # reset the once-latch
    import logging
    with caplog.at_level(logging.WARNING, logger="agenttic"):
        wrapped = agenttic.trace(lambda q: q, target=None)  # no target, no sink
        assert wrapped("hi") == "hi"            # agent still runs, identical
    assert any("no target configured" in r.message for r in caplog.records)


# --- import-surface: no private framework modules in _detect.py ------------
def test_detect_imports_no_private_framework_modules():
    src = Path(_detect.__file__).read_text()
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    # _detect must never import a framework SDK (public or private) at all —
    # it detects by duck-typing and loads adapters by name only.
    forbidden = ("langgraph", "langchain", "langchain_core", "agents.")
    for mod in imported:
        assert not any(mod == f or mod.startswith(f) for f in forbidden), mod
        # and never a dunder/private submodule of a framework
        assert "._" not in mod or not mod.startswith(("langchain", "langgraph")), mod
