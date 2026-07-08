"""Framework adapter contracts (SPEC-7 Step 36, T36.4).

Pins the four acceptance criteria without requiring LangGraph / the OpenAI
Agents SDK to be installed: behavior-identical wrapping, public-API-only import
surface, enforce=True failing loud without a compiled policy, and the
trace(agent) → spans → ingested Traces round-trip. Where the real SDK is absent
we drive the adapter through fakes that mimic the framework's *public* contract.
"""
from __future__ import annotations

import ast
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters/langgraph"))
sys.path.insert(0, str(ROOT / "adapters/openai_agents"))

import agenttic_langgraph as al          # noqa: E402
import agenttic_openai_agents as ao      # noqa: E402
from ascore.enforce.adapter_guard import EnforceConfigError  # noqa: E402
from ascore.ingest import ingest_otlp_payload  # noqa: E402
from ascore.registry.sqlite_store import Registry  # noqa: E402


# --- fakes that mimic each framework's PUBLIC contract ---------------------

class FakeGraph:
    """Mimics a compiled LangGraph graph: drives the public callback contract."""

    def __init__(self, output):
        self.output = output

    def invoke(self, input, config=None, **kw):
        for h in (config or {}).get("callbacks", []):
            h.on_tool_start({"name": "get_weather"}, '{"city":"SF"}')
            h.on_tool_end('{"temp_f":68}')

            class R:
                generations = [[type("G", (), {"text": "done"})()]]
                llm_output = {"model_name": "m",
                              "token_usage": {"prompt_tokens": 3, "completion_tokens": 2}}
            h.on_llm_end(R())
        return dict(self.output)


def _install_fake_agents_module():
    """Inject a fake `agents` SDK exposing the public Runner.run_sync + RunHooks
    driving contract, so _TracedAgent.run_sync can be exercised offline."""
    mod = types.ModuleType("agents")

    class Tool:
        name = "lookup"

    class Runner:
        @staticmethod
        def run_sync(agent, input, hooks=None, **kw):
            if hooks is not None:
                import asyncio
                asyncio.run(hooks.on_tool_start(None, agent, Tool()))
                asyncio.run(hooks.on_tool_end(None, agent, Tool(), '{"ok":1}'))

                class Resp:
                    output_text = "answer"

                    class usage:
                        input_tokens = 4
                        output_tokens = 1
                asyncio.run(hooks.on_llm_end(None, agent, Resp()))
            return {"final": "answer", "echo": input}

    mod.Runner = Runner
    sys.modules["agents"] = mod
    return mod


# --- 1) behavior-identical --------------------------------------------------

def test_langgraph_wrapper_is_behavior_identical():
    graph = FakeGraph({"answer": 42})
    wrapped = al.trace(graph, agent_id="a", sink=[])
    assert wrapped.invoke({"q": "x"}) == FakeGraph({"answer": 42}).invoke({"q": "x"})


def test_openai_wrapper_is_behavior_identical():
    _install_fake_agents_module()
    try:
        agent = object()
        wrapped = ao.trace(agent, agent_id="a", sink=[])
        got = wrapped.run_sync("hi")
        from agents import Runner
        assert got == Runner.run_sync(agent, "hi")  # same output, no hooks effect
    finally:
        sys.modules.pop("agents", None)


def test_wrappers_delegate_unknown_attributes():
    graph = FakeGraph({"a": 1})
    graph.custom_attr = "present"
    assert al.trace(graph, agent_id="a").custom_attr == "present"


# --- 2) public-API-only import surface -------------------------------------

_FRAMEWORK_PKGS = {"langchain", "langchain_core", "langgraph", "agents"}


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


@pytest.mark.parametrize("rel", [
    "adapters/langgraph/agenttic_langgraph/__init__.py",
    "adapters/openai_agents/agenttic_openai_agents/__init__.py",
])
def test_adapter_imports_are_public_only(rel):
    src = ROOT / rel
    for mod in _imported_modules(src):
        top = mod.split(".")[0]
        if top in _FRAMEWORK_PKGS:
            segments = mod.split(".")
            # no private (underscore-prefixed) segment in a framework import
            assert not any(s.startswith("_") for s in segments), \
                f"{rel} imports non-public framework module {mod}"


@pytest.mark.parametrize("rel", [
    "adapters/langgraph/agenttic_langgraph/__init__.py",
    "adapters/openai_agents/agenttic_openai_agents/__init__.py",
])
def test_adapter_does_not_monkeypatch(rel):
    text = (ROOT / rel).read_text()
    assert "monkeypatch" not in text.lower()
    assert "setattr(" not in text  # no patching framework classes
    assert "__wrapped__" not in text


# --- 3) enforce=True fails loud without a compiled policy -------------------

def test_langgraph_enforce_without_policy_fails_loud():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        with pytest.raises(EnforceConfigError):
            al.trace(FakeGraph({}), agent_id="a", enforce=True, reg=reg)


def test_openai_enforce_without_registry_fails_loud():
    with pytest.raises(EnforceConfigError):
        ao.trace(object(), agent_id="a", enforce=True)


# --- 4) round-trip: trace(agent) → spans → ingested Traces -----------------

def test_langgraph_roundtrips_into_ingested_trace():
    sink: list = []
    wrapped = al.trace(FakeGraph({"answer": 1}), agent_id="rt-agent",
                       agent_config_hash="cfg-x", sink=sink)
    wrapped.invoke({"q": "weather?"})
    assert len(sink) == 1
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        ingest_otlp_payload(reg, sink[0])
        live = reg.traces("rt-agent", mode="live")
        assert len(live) == 1
        kinds = {s.kind for s in live[0].spans}
        assert {"tool_call", "llm_call"} <= kinds
        assert live[0].source == "otel_ingest"
        assert live[0].agent_config_hash == "cfg-x"
        tool = [s for s in live[0].spans if s.kind == "tool_call"][0]
        assert "content_sha256" in tool.output
        # exclusion invariant holds for adapter-emitted traces too
        assert reg.traces("rt-agent", mode="batch") == []


def test_openai_roundtrips_into_ingested_trace():
    _install_fake_agents_module()
    try:
        sink: list = []
        wrapped = ao.trace(object(), agent_id="rt-oa", sink=sink)
        wrapped.run_sync("hi")
        assert len(sink) == 1
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registry(db_path=f"{tmp}/t.db")
            ingest_otlp_payload(reg, sink[0])
            live = reg.traces("rt-oa", mode="live")
            assert len(live) == 1
            assert {"tool_call", "llm_call"} <= {s.kind for s in live[0].spans}
    finally:
        sys.modules.pop("agents", None)
