# Agenttic quickstart

You just ran `agenttic init`. This directory now has everything needed to get a
**signed safety grade in under a minute — no API key required**.

## 1. Certify the built-in reference agent (offline, mock provider)

```bash
agenttic certify --mock --out dossier.json
```

This scores a built-in reference agent against the `cert-agent-safety-v1`
profile and writes an evidence **dossier** (Tier A/B/C, with any NOT ASSESSED
domains shown honestly).

## 2. Verify the dossier offline

```bash
agenttic dossier verify dossier.json
```

Recomputes every hash from the dossier JSON alone — proof the grade wasn't
tampered with.

## 3. Trace your own agent

Open `agent_sample.py`. One line wraps any framework:

```python
from agenttic import trace
agent = trace(my_agent)          # LangGraph / OpenAI Agents / any callable
```

or decorate a custom function:

```python
from agenttic import instrument

@instrument(agent_id="my-agent")
def my_agent(query: str) -> str:
    ...
```

Set `distribution.target` in `config.yaml` (or the `AGENTTIC_TARGET` env var) to
send runs to your Agenttic instance. With no target set, wrapping runs your
agent unchanged and emits nothing — it never phones home.

## Files in this scaffold

| File               | What it is                                             |
|--------------------|--------------------------------------------------------|
| `config.yaml`      | Complete, certify-ready config (edit `distribution.target`) |
| `kb.json`          | Knowledge base the reference agent uses                |
| `agent_sample.py`  | Copy-paste examples: `trace`, `@instrument`, `session`, `--url` |
| `QUICKSTART.md`    | This file                                              |
