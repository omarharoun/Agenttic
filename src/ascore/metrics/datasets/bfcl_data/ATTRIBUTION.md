# BFCL (Berkeley Function-Calling Leaderboard) — attribution

This directory vendors a **real sample subset** of the Berkeley Function-Calling
Leaderboard (BFCL) dataset, used by agenttic's `bfcl-simple-v3` standard suite to
score tool-call accuracy.

- **Source:** https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard
  (Gorilla project, UC Berkeley) — https://gorilla.cs.berkeley.edu/
- **License:** Apache License 2.0 (see `LICENSE` in this directory).
- **What's vendored:** the first 25 records of the `BFCL_v3_simple` split
  (`BFCL_v3_simple.sample.json`) and their ground-truth answers
  (`BFCL_v3_simple.sample.answers.json`), unmodified. The full split has 400
  records; ingest it where network allows with:

  ```
  ascore standard ingest bfcl --full          # fetch the full simple split from HF
  ascore standard ingest bfcl                 # ingest the vendored 25-record sample
  ```

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
