# SWE-bench Verified — attribution, scoring honesty & how to ingest

agenttic's `swebench-verified-v1` standard suite ingests **SWE-bench Verified**,
the gold-standard code-agent benchmark: real GitHub issues paired with the
repository at the buggy commit, where an agent must produce a patch that
**resolves** the issue. *Verified* is the 500-instance, human-validated subset.

- **Source:** https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified
  (Princeton NLP / collaborators). Project: https://www.swebench.com
- **Paper:** *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?*,
  Jimenez, Yang, Wettig, Yao, Pei, Press, Narasimhan — ICLR 2024,
  **arXiv:2310.06770**.
- **License:** **MIT** (see `LICENSE`). Public — not gated.

Each record carries: `repo`, `base_commit`, `problem_statement` (the issue),
the **gold** `patch`, the `test_patch`, and the test lists `FAIL_TO_PASS` /
`PASS_TO_PASS` (JSON-encoded string lists upstream).

## Scoring honesty — PROXY, not official resolve-rate

**This is the most important caveat.** SWE-bench's official metric is
**resolve-rate**: a candidate patch is applied to the repo at `base_commit` and
the tests are run **inside a per-instance Docker container** — an instance is
*resolved* iff every `FAIL_TO_PASS` test passes AND every `PASS_TO_PASS` test
still passes. Computing that requires the **SWE-bench Docker execution harness**
(per-repo images, builds, real test runs), which we do **not** run on the slim VM.

So agenttic scores this suite with an explicit **OFFLINE PROXY**, never the
official resolve-rate:

- `swebench_patch_generated` — did the agent emit a non-empty code patch (the
  patch-rate prerequisite for any resolve)?
- `swebench_patch_targets_gold_files` — of the files the **gold** patch edits,
  what fraction does the agent's patch also edit (file-localization)?

These proxies are labeled **"proxy, not official resolve-rate"** wherever they
surface. They are a tractable static signal that the agent localized the bug —
**NOT** verification that the hidden tests pass. We do **not** report or claim any
official SWE-bench numbers.

The official metric's *interface* lives in `ascore/metrics/swebench_resolve.py`
(`resolve_rate` + `ExecutionHarnessRequired`); it documents exactly what the real
metric needs and **raises** if asked to score without the Docker harness. The
`DatasetInfo` for this suite sets `requires_execution_harness=True` so the
UI/methodology page can show the caveat. **Wiring the real Docker harness is a
tracked FUTURE INFRA task.**

## What is vendored here

- `LICENSE` — the upstream SWE-bench MIT license.
- `ATTRIBUTION.md` — this note.
- `swebench_verified.sample.jsonl` — a small **REAL** sample of the Verified test
  split (a handful of instances across distinct repos: astropy, django,
  matplotlib, pylint, sphinx, sympy), vendored for offline ingest. Because
  SWE-bench is MIT-licensed and public, this is genuine upstream content (problem
  statements + test lists + gold patches), not a placeholder. The leading
  `_comment` line is a header, not a record.

## Ingesting the data

```
ascore standard ingest swebench           # vendored REAL sample (offline)
ascore standard ingest swebench --full     # whole 500-instance Verified split from HF
```

`--full` pulls the split via HuggingFace's public datasets-server rows API (no
token needed). Either way, scoring uses the OFFLINE PROXY above — `--full` does
**not** turn on official resolve-rate (that still needs the Docker harness).

## Citation

```bibtex
@inproceedings{jimenez2024swebench,
  title     = {{SWE-bench}: Can Language Models Resolve Real-World GitHub Issues?},
  author    = {Jimenez, Carlos E. and Yang, John and Wettig, Alexander and Yao,
               Shunyu and Pei, Kexin and Press, Ofir and Narasimhan, Karthik},
  booktitle = {International Conference on Learning Representations (ICLR)},
  year      = {2024},
  note      = {arXiv:2310.06770}
}
```
