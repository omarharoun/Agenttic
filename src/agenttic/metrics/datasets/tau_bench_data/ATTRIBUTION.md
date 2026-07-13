# τ-bench (tau-bench, Sierra) — attribution

This directory vendors a **real sample subset** of the τ-bench dataset, used by
agenttic's `tau-bench-v1` standard suite to score tool-call accuracy and ordered
tool-trajectory correctness.

- **Source:** https://github.com/sierra-research/tau-bench
  (Sierra) — paper: https://arxiv.org/abs/2406.12045
- **License:** MIT (see `LICENSE` in this directory — Copyright (c) 2024 Sierra).
- **What's vendored:** `tau_bench.sample.json` — 20 **real** tasks (the first 12
  `retail` + first 8 `airline` tasks from the public *test* splits,
  `tau_bench/envs/{retail,airline}/tasks_test.py`), normalized by
  `tau_bench.py:_parse_task_module` (AST parse of the upstream Python task
  literals — no code execution, nothing fabricated). The full test split has 165
  tasks (115 retail + 50 airline). Ingest more where network allows:

  ```
  ascore standard ingest tau-bench --full     # fetch+parse full retail+airline test splits from GitHub
  ascore standard ingest tau-bench            # ingest the vendored 20-task real sample
  ```

## What we map vs. what we do NOT reproduce

τ-bench is a multi-turn **tool-agent-user** benchmark: an LLM user-simulator
converses with the agent, which calls domain tools against a stateful
retail/airline database, and the official reward compares the resulting
**database state** (plus required outputs) to a human-annotated ground-truth
trajectory.

- **Mapped (tractable, deterministic):** each task's human-annotated ground-truth
  `actions` — the ordered tool calls and their arguments — become a canonical
  `TestCase`. A candidate agent trajectory is scored with our tool-call-accuracy
  checks: tool selection, argument match (each ground-truth arg has exactly one
  acceptable value, including list/dict args), ordered sequence, and "acts when a
  task warrants tool use".
- **NOT reproduced (and not claimed):** the LLM user-simulator, the stateful
  retail/airline databases, the multi-turn conversation dynamics, and the
  official database-state-hash reward function. We score an annotated
  *trajectory* against the annotated ground-truth trajectory — exactly as we
  treat BFCL.
- **Known limitation:** when a task calls the same tool more than once (e.g.
  `get_product_details` twice), the argument check validates the first such call;
  the full ordered call sequence (including repeats) is still checked by
  `tool_sequence_accuracy`.

- **Honesty:** agenttic scores these *real* τ-bench tasks with our own scorer; we
  do not claim to reproduce the official τ-bench leaderboard's pass^k figures,
  which require the live user-simulator + environment.

## Citation

```bibtex
@misc{yao2024taubench,
  title  = {$\tau$-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains},
  author = {Yao, Shunyu and Shinn, Noah and Razavi, Pedram and Narasimhan, Karthik},
  year   = {2024},
  eprint = {2406.12045},
  archivePrefix = {arXiv},
  url    = {https://arxiv.org/abs/2406.12045}
}
```
