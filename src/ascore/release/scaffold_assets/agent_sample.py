"""Agenttic quickstart sample — the one line that gets you a traced agent.

Run the offline reference agent and certify it (no API key needed):

    agenttic certify --mock        # → a signed safety grade (Tier A/B/C)
    agenttic dossier verify dossier.json

Then wire Agenttic into YOUR agent — pick the shape that matches:
"""
from __future__ import annotations


# 1) Any framework — one import, auto-detected. Uncomment and pass your object:
#
#    from agenttic import trace
#    agent = trace(my_compiled_langgraph_graph)   # or an OpenAI Agents agent
#    agent = trace(my_plain_callable)             # or anything callable
#
# Spans go to the target you configure (config.yaml `distribution.target`, or the
# AGENTTIC_TARGET env var). With no target set, wrapping runs your agent
# unchanged and emits nothing — it never phones home.


# 2) A custom / homegrown agent — decorate the function:
from agenttic import instrument, session


@instrument(agent_id="my-agent")
def my_agent(query: str) -> str:
    # ... call your model / tools however you like ...
    return f"You said: {query}"


# 3) Code that isn't a single function — use the context manager:
def run_block(query: str) -> str:
    with session(agent_id="my-agent") as run:
        run.input = query
        run.output = f"You said: {query}"
        return run.output


# 4) A black-box HTTP agent — certify it by URL (no code change):
#
#    agenttic certify --url https://your-agent/endpoint \
#        --agent my-http-agent --profile cert-agent-safety-v1


if __name__ == "__main__":
    print(my_agent("hello"))
    print(run_block("hello"))
