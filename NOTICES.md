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
