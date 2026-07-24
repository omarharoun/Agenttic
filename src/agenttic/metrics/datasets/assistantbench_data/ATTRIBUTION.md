# AssistantBench — attribution

This directory vendors a **real sample subset** of the AssistantBench dataset,
used by agenttic's `assistantbench-v1` standard suite to score web-agent
**answer accuracy** (fractional, partial-credit) and **answer rate**.

- **Source:** https://huggingface.co/datasets/AssistantBench/AssistantBench
- **Paper:** Ori Yoran, Samuel Joseph Amouyal, Chaitanya Malaviya, Ben Bogin,
  Ofir Press, Jonathan Berant. *AssistantBench: Can Web Agents Solve Realistic
  and Time-Consuming Tasks?* (2024). arXiv:2407.15711 —
  https://arxiv.org/abs/2407.15711
- **License:** Apache License 2.0 (per the dataset card on HuggingFace; see
  `LICENSE` in this directory). The dataset card declares `license: apache-2.0`.
- **What's vendored:** 16 leading records of the **validation/dev** split
  (`assistant_bench_v1.0_dev.jsonl`), unmodified, in
  `assistant_bench_v1.0_dev.sample.jsonl`. The vendored subset is chosen to
  cover all gold-answer types: free-text strings, newline-separated string
  lists, numbers, and JSON-dict (one object per line) answers.

  | split            | suite               | sample | full |
  |------------------|---------------------|--------|------|
  | `validation`/dev | `assistantbench-v1` | 16     | 33   |

  Ingest where network/license allows (`--full` fetches the whole dev split
  from HuggingFace):

  ```
  agenttic standard ingest assistantbench           # vendored 16-record sample
  agenttic standard ingest assistantbench --full     # full 33-question dev split
  ```

  The **test** split's gold answers are held out by the authors (the official
  leaderboard scores them), so only the validation/dev split is scoreable
  offline.

## Scoring methodology (faithful port)

AssistantBench questions are realistic, time-consuming web tasks with short
factual gold answers. Our `answer_accuracy` / `answer_attempted` canonical
checks implement AssistantBench's own evaluation
(`evaluation/evaluate_utils` in the AssistantBench leaderboard space),
ported to pure Python in `agenttic/metrics/answer_match.py`:

- **strings / lists** — DROP-style token-F1 with optimal 1-1 bag alignment;
- **numbers** — a symmetric log-ratio `max(0, 1 - |ln(pred/gold)|)`
  (exact -> 1.0, decaying to 0 at a factor of *e*);
- **JSON dicts** — recall over gold keys × precision over predicted keys as an
  F1, with values scored by their own typed evaluator.

**Answer rate** is the fraction of questions the agent attempts rather than
abstains on. AssistantBench rewards abstaining over guessing wrong, so answer
rate is **reported but UNWEIGHTED** in the Agenttic Index, while answer accuracy
carries a small index weight (0.05).

## Honesty

agenttic scores these **real** AssistantBench questions with AssistantBench's
**own** fractional answer-accuracy metric, applied to a candidate agent's final
answer. We do **not** reproduce the paper's leaderboard figures — those require
running an agent live against the open web (the browsing environment is not
vendored). Our numbers are produced by *our* port of *their* scorer on *their*
data.
