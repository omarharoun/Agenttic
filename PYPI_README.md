# Agenttic

**Give your AI agent a signed safety grade — an independent agent-safety
certification you can verify.**

Agenttic traces any AI agent, tests its safety-relevant behavior, and issues a
signed, offline-verifiable dossier with an honest Tier grade (A/B/C). It measures
what was actually tested — untested behavior is reported as **NOT ASSESSED**, not
quietly passed.

## Install

```bash
pip install agenttic
```

Optional framework adapters (lazy — the base install pulls no framework SDK):

```bash
pip install "agenttic[langgraph]"   # LangGraph / LangChain
pip install "agenttic[openai]"      # OpenAI Agents SDK
pip install "agenttic[all]"         # all adapters + OpenTelemetry
```

Requires Python 3.12+.

## Quickstart

Get a signed grade in under a minute — no API key — by certifying the built-in
reference agent:

```bash
agenttic certify --mock --out dossier.json   # → a signed Tier A/B/C dossier
agenttic dossier verify dossier.json         # → offline signature + evidence check
```

Or wrap your own agent with one line and trace its behavior:

```python
from agenttic import trace

agent = trace(my_agent)   # auto-detects LangGraph / OpenAI Agents / plain callables
```

## What you get

- A **signed, offline-verifiable dossier** — Ed25519-signed, checkable without
  calling home, backed by hash-chained evidence of exactly what was tested.
- **Honest coverage** — results state what was measured; anything untested is
  marked **NOT ASSESSED** rather than assumed safe.
- A single **Tier grade (A/B/C)** you can publish and third parties can verify.

Agenttic reports the measured behavior of an agent under the tests that were run.
It is a measurement of tested behavior, not a guarantee of safety in every
situation.

## Links

- Homepage & docs: <https://agenttic.io>
- Source: <https://github.com/omarharoun/Agenttic>
