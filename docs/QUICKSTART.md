# Agenttic quickstart — a signed safety grade in under a minute

The promise: **a developer who has never seen Agenttic can `pip install`, add one line, and get a signed safety grade in under a minute** — against a built-in reference agent, with zero setup and no API key.

Every command below is copy-paste runnable and is executed against the offline
mock provider by a test in the suite (`tests/test_dist_quickstart_doc.py`).

## 1. Install

```bash
pip install agenttic
```

Base install pulls **no framework SDK**. Add one when you need it:
`pip install 'agenttic[langgraph]'`, `agenttic[openai]`, or `agenttic[all]`.

## 2. Scaffold and certify — the signed grade

```bash
agenttic init
agenttic certify --mock --out dossier.json
agenttic dossier verify dossier.json
```

`agenttic init` drops a ready-to-run `config.yaml`, the reference agent's
`kb.json`, an `agent_sample.py`, and a `QUICKSTART.md` into the current
directory. `agenttic certify --mock` grades the built-in reference agent offline
(no API key) and writes an evidence **dossier** — a tier (A/B/C) with any
NOT ASSESSED domains shown honestly. `agenttic dossier verify` recomputes every
hash from the dossier JSON alone, proving the grade wasn't tampered with.

## 3. The one line — trace your own agent

Wrap any framework; Agenttic auto-detects it:

```python
from agenttic import trace

agent = trace(my_agent)   # LangGraph graph, OpenAI Agents agent, or any callable
```

Or, for a custom / homegrown agent, decorate the function:

```python
from agenttic import instrument

@instrument(agent_id="my-agent")
def my_agent(query: str) -> str:
    ...
```

Wrapping is behavior-identical — it observes, never blocks or mutates. Spans go
to the target you configure (`config.yaml` → `distribution.target`, or the
`AGENTTIC_TARGET` env var). **With no target set, wrapping runs your agent
unchanged and emits nothing — it never phones home.**

## 4. Certify your own agent

A black-box HTTP agent, no code change:

```bash
agenttic certify --url https://your-agent/endpoint --out my-dossier.json
```

That's it — install, one line, a signed grade.
