# Third-party notices

Agenttic uses the following third-party components under their own licenses.

## Microsoft Presidio (`presidio-analyzer`) — MIT License

Agenttic's PII detection metric (`metrics/safety_checks.py`, the `pii_leakage`
metric and `no_pii_leak` check) uses **Microsoft Presidio** for named-entity PII
recognition when it is installed (optional `safety` extra). Presidio is licensed
under the **MIT License**:

> Copyright (c) Microsoft Corporation.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

Source: https://github.com/microsoft/presidio (verify the current license text
at the upstream repository before redistribution — this notice is provided in
good faith and is not legal advice).

Agenttic imports Presidio as a dependency and does **not** copy its source. The
regex-based PII recognizers in `metrics/safety_checks.py` are an independent
reimplementation of standard public patterns (email/phone/SSN/credit-card/IP/
IBAN) and are Agenttic's own code.

## AISI Inspect (`inspect_ai`) — MIT License

Agenttic's Inspect evaluator adapter
(`agenttic/evaluators/inspect_adapter.py`, optional `inspect` extra) wraps the
UK AI Security Institute's **Inspect** evaluation framework as one source in the
Evaluator Plugin Interface. Inspect is licensed under the **MIT License**:

> Copyright (c) 2024 UK AI Security Institute
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

Source: https://github.com/UKGovernmentBEIS/inspect_ai (verify the current
license text at the upstream repository before redistribution — this notice is
provided in good faith and is not legal advice).

Agenttic imports Inspect as an **optional, arm's-length dependency** (called
through its public API/types) and does **not** vendor or copy its source. The
base install pulls no `inspect_ai`. The mapping from Inspect's native categories
to Agenttic's controlled dimension vocabulary, and the deterministic offline
strategy, are Agenttic's own code. This MIT (permissive) license is why the
orchestrator's license gate lets the Inspect adapter run in both hosted and
self-hosted deployments.
