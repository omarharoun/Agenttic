# AgentHarm — attribution & do-not-vendor-harmful-content note

agenttic's `agentharm-harmful-v1` standard suite scores **harmful-action
refusal rate** (AgentHarm methodology) on the AgentHarm benchmark.

- **Source:** https://huggingface.co/datasets/ai-safety-institute/AgentHarm
  (UK AI Safety Institute & Gray Swan AI) — paper: arXiv:2410.09024.
- **License:** MIT License *with an additional clause* prohibiting use of the
  dataset/benchmark for purposes other than improving the safety and security
  of AI systems (see `LICENSE` in this directory). Copyright (c) 2024 Gray Swan
  AI and UK AI Safety Institute. agenttic uses it solely to evaluate and improve
  agent refusal of harmful actions, which is within the licensed purpose.
- **Access:** the dataset is currently **public** on HuggingFace (not gated at
  the time of ingest). The harmful split's `prompt` fields nonetheless contain
  genuine harmful agentic instructions, so — by policy — **we do not vendor that
  content into this repository.**

## What is vendored here (and what is NOT)

- `LICENSE` — the upstream MIT-with-clause license, unmodified.
- `agentharm_harmful.sample.json` — a tiny set of **NON-OPERATIONAL placeholder
  records**. They mirror the real AgentHarm record schema (`id`, `name`,
  `category`, `prompt`, `target_functions`, `grading_function`) and preserve real
  **harm categories** (Fraud, Cybercrime, Disinformation, Harassment, Drugs,
  Copyright), but every `prompt` is redacted/sanitized and the `target_functions`
  are generic tool names. **No genuine harmful instruction is stored in this repo.**

The placeholders exist only so the adapter, the suite wiring, and the
`harmful_action_refused` metric can be exercised in tests and demos. They are a
schema/scoring proxy — refusing them passes, executing a forbidden tool fails —
not real attack content.

## Ingesting the real dataset

To score the real public AgentHarm harmful split (fetched at runtime into the
local registry; still never written into the repo):

```
agenttic standard ingest agentharm --full     # fetch the public harmful_behaviors_test_public split from HF
agenttic standard ingest agentharm            # ingest the vendored NON-OPERATIONAL placeholder sample
```

## Honesty

agenttic scores these cases with *our* AgentHarm-style refusal scorer (refused
*and* did not execute a forbidden tool). We implement the published methodology;
our numbers are produced by our scorer on the AgentHarm data and are not a claim
to reproduce the official AgentHarm leaderboard's exact figures.

## Citation

```bibtex
@inproceedings{andriushchenko2025agentharm,
  title  = {AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents},
  author = {Andriushchenko, Maksym and Souly, Alexandra and others},
  booktitle = {International Conference on Learning Representations (ICLR)},
  year   = {2025},
  note   = {arXiv:2410.09024}
}
```
