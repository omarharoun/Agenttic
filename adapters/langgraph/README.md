# agenttic-langgraph

Trace a [LangGraph](https://github.com/langchain-ai/langgraph) agent onto the
Agenttic OTel-GenAI bus using **public LangChain callbacks only** — no
monkey-patching, no private internals.

```python
from agenttic_langgraph import trace

graph = trace(compiled_graph, agent_id="support-bot",
              endpoint="https://agenttic.internal")
result = graph.invoke({"messages": [...]})   # behaviour-identical; spans emitted
```

Or let Agenttic auto-detect the framework for you:

```python
from agenttic import trace          # pip install agenttic[langgraph]
graph = trace(compiled_graph)       # dispatches here automatically
```

`trace` returns a transparent wrapper: it only observes (Hard Rule 38 —
behaviour-identical), and the optional `enforce=` argument routes tool calls
through the Agenttic gateway at the ramp's non-blocking default posture. The span
builder lives in the `agenttic` distribution; this package is a thin adapter.
