# BFCL (Berkeley Function-Calling Leaderboard) — attribution

This directory vendors **real sample subsets** of the Berkeley Function-Calling
Leaderboard (BFCL) v3 dataset, used by agenttic's BFCL standard suites to score
tool-call accuracy across single-call, multi-call, and select-among-many
scenarios.

- **Source:** https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard
  (Gorilla project, UC Berkeley) — https://gorilla.cs.berkeley.edu/
- **License:** Apache License 2.0 (see `LICENSE` in this directory).
- **What's vendored:** the leading records of each BFCL v3 split below, with
  their ground-truth answers, unmodified (`BFCL_v3_<split>.sample.json` +
  `BFCL_v3_<split>.sample.answers.json`):

  | split              | suite                          | sample | full |
  |--------------------|--------------------------------|--------|------|
  | `simple`           | `bfcl-simple-v3`               | 25     | 400  |
  | `parallel`         | `bfcl-parallel-v3`             | 20     | 200  |
  | `multiple`         | `bfcl-multiple-v3`             | 20     | 200  |
  | `parallel_multiple`| `bfcl-parallel-multiple-v3`    | 20     | 200  |
  | `live_simple`      | `bfcl-live-simple-v3`          | 18     | 258  |
  | `live_multiple`    | `bfcl-live-multiple-v3`        | 15     | 1053 |

  Ingest where network allows (`--full` fetches the whole split from HF):

  ```
  ascore standard ingest bfcl --full                   # full simple split
  ascore standard ingest bfcl-parallel                 # vendored parallel sample
  ascore standard ingest bfcl-parallel-multiple --full # full parallel_multiple split
  ```

- **Multi-call ground truth:** for the `parallel*` splits BFCL's ground truth is
  an ordered *list* of expected calls; agenttic preserves the full ordered
  sequence (incl. repeated function names) plus the deduped required-tool set, so
  the canonical selection / parameter / sequencing checks score every call.

- **Honesty:** agenttic scores these *real* BFCL cases with our tool-call-accuracy
  methodology (the heaviest-weighted Agenttic Index component). Our numbers are
  produced by *our* scorer on the BFCL data — we do not claim to reproduce the
  official BFCL leaderboard's exact figures.

## Citation

```bibtex
@misc{berkeley-function-calling-leaderboard,
  title  = {Berkeley Function Calling Leaderboard},
  author = {Patil, Shishir G. and Mao, Huanzhi and others},
  year   = {2024},
  howpublished = {\url{https://gorilla.cs.berkeley.edu/leaderboard.html}}
}
```
