# GAIA — attribution, gated-access note & how to ingest the real data

agenttic's `gaia-v1` standard suite scores **answer accuracy** (GAIA normalized
exact-match) on the **GAIA validation split** — the general AI-assistant
benchmark of real-world questions that require tool use and multi-step reasoning,
each with a single short ground-truth answer and a difficulty `Level` (1–3).

- **Source:** https://huggingface.co/datasets/gaia-benchmark/GAIA
  (Meta AI / HuggingFace) — paper: *GAIA: A Benchmark for General AI Assistants*,
  arXiv:2311.12983.
- **License:** released by the authors under the terms on the HuggingFace
  dataset card (CC-BY-4.0 at time of writing). See `LICENSE.txt`.
- **Access:** **GATED.** Unlike BFCL/AgentDojo, GAIA cannot be fetched
  anonymously — you must be logged in to HuggingFace and **accept the dataset's
  terms** on the dataset page first, then authenticate with an HF token. The
  **test split answers are held out**; only the **validation** split ships final
  answers, so agenttic ingests *validation*.

## Gated status (honesty)

At ingest time in this repo, GAIA was confirmed **gated**: an anonymous fetch of
`2023/validation/metadata.jsonl` returns **HTTP 401 Unauthorized**. agenttic
therefore does **not** vendor any real GAIA content — we cannot redistribute data
we have not been granted access to. The dataset info marks the suite
`gated=True` so the UI/methodology page can show **"gated — bring your own
access."**

## What is vendored here (and what is NOT)

- `LICENSE.txt`, `ATTRIBUTION.md` — this note.
- `gaia_validation.sample.jsonl` — a tiny set of **NON-OPERATIONAL, CLEARLY
  FABRICATED placeholder records**. They mirror the real GAIA validation record
  schema (`task_id`, `Question`, `Level`, `Final answer`, `file_name`,
  `Annotator Metadata`) but every question is an obvious stand-in and every
  answer is trivial. **No real GAIA question or answer is stored in this repo.**

The placeholders exist only so the `GAIAAdapter`, the suite wiring, and the
`gaia_answer_match` (GAIA normalized exact-match) metric can be exercised in
tests and demos. They are a schema/scoring proxy, not real benchmark content.

## Ingesting the REAL GAIA validation set

1. Visit the dataset page and **accept the terms/conditions** (one-time, while
   logged in): https://huggingface.co/datasets/gaia-benchmark/GAIA
2. Create an HF access token and export it:
   ```
   export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx     # or HUGGING_FACE_HUB_TOKEN
   ```
3. Ingest the real validation split into the local registry (never written back
   into the repo):
   ```
   agenttic standard ingest gaia --full      # fetch the gated validation split from HF (needs HF_TOKEN)
   agenttic standard ingest gaia             # ingest the vendored NON-OPERATIONAL placeholder sample
   ```
   Without a valid, terms-accepted token, `--full` fails with an authorization
   error (HTTP 401) — that is the gating, surfaced honestly, not a bug.

## Honesty

We score GAIA with *our* GAIA-normalized exact-match scorer (a re-implementation
of the official `question_scorer`: number/list/string normalization). We
implement the published methodology; our numbers are produced by our scorer on
the GAIA validation data and are not a claim to reproduce the official GAIA
leaderboard's exact figures or to use the held-out test set.

## Citation

```bibtex
@article{mialon2023gaia,
  title   = {GAIA: A Benchmark for General AI Assistants},
  author  = {Mialon, Gr{\'e}goire and Fourrier, Cl{\'e}mentine and Swift, Craig
             and Wolf, Thomas and LeCun, Yann and Scialom, Thomas},
  journal = {arXiv preprint arXiv:2311.12983},
  year    = {2023}
}
```
