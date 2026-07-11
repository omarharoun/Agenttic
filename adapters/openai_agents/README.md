# agenttic-openai-agents

Trace an [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
agent onto the Agenttic OTel-GenAI bus using the SDK's **public `RunHooks`
lifecycle only** — no monkey-patching, no private internals.

```python
from agenttic_openai_agents import trace

agent = trace(my_agent, agent_id="triage", endpoint="https://agenttic.internal")
result = await agent.run("hello")     # behaviour-identical; spans emitted
```

Or let Agenttic auto-detect the framework for you:

```python
from agenttic import trace          # pip install agenttic[openai]
agent = trace(my_agent)             # dispatches here automatically
```

`trace` returns a transparent wrapper: it only observes (Hard Rule 38 —
behaviour-identical), and the optional `enforce=` argument routes tool calls
through the Agenttic gateway at the ramp's non-blocking default posture. The span
builder lives in the `agenttic` distribution; this package is a thin adapter.
