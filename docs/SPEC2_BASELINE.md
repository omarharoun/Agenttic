# SPEC-2 Baseline — Survey of SPEC-1 Surfaces

Locating map of the existing `ascore` surfaces the certification track builds on.
Paths are `file` or `file:line`. This is a locator, not a review.

## 1. Schema module + schema_version
- `src/ascore/schema/` — Pydantic models per domain object.
- `schema/trace.py:26` `SCHEMA_VERSION = "0.1.0"`; `Trace.schema_version` auto-defaults.
- Semver: MAJOR breaking, MINOR new optional fields/Span kinds, PATCH docs.

## 2. Registry append-only pattern
- `src/ascore/registry/sqlite_store.py` — SQLModel ORM tables.
- Versioned rows with `UniqueConstraint(tenant_id, <id>, version)`: `SuiteRow:47`,
  `RubricRow:68`, `DeclaredAgentRow:77` (only `active` flag mutable), `TraceRow:92`,
  `ScorecardRow:103`, append-only `SpendRow:177`, `ReEvalRow:169`.
- Current state computed from version lineage; aggregates recomputed, not stored.
- `src/ascore/registry/store.py` — Registry facade over the store.

## 3. Harness entry + agent_config_hash
- `src/ascore/harness/runner.py` — `async run_suite()` (:66).
- `agent_config_hash` from `adapter.config_hash()` (:50, :100); pins traces; used as
  resume/cache selector (:103). Budget abort at :119.

## 4. Scoring / judge / calibration
- `scoring/engine.py:141` `score_run` — code checks + LLM judge + FI; pass at `score >=
  pass_threshold` (:216).
- `scoring/judge.py:76` `build_judge_prompt` — ONE criterion per call; strict JSON, one retry.
- `scoring/calibration.py:65` `calibration_report` — exact-match binary, Krippendorff alpha
  three-point; uncalibrated if n<5 or agreement<0.8.
- `scoring/judge_calibration.py:51` `_DEMONSTRATED_JUDGE` — recorded real run promotes
  criteria out of PROVISIONAL; corpus `judge_calibration_corpus.jsonl`.

## 5. stats.py bootstrap
- `src/ascore/stats.py`: `wilson_interval:46`, `wilson_lower_bound:60`,
  `proportion_stats:65`, `mcnemar:111`, `paired_bootstrap:182` (deterministic seed 1234,
  delta + CI + p-value + significance).

## 6. Result-cache keying
- `src/ascore/result_cache.py`: `scorecard_cache_key:32` (SHA256 of kind, agent_id,
  suite_id/version, agent_config_hash, rubric_id/version, judge_signature);
  `canonical_cache_key:50` (kind, agent_config_hash, sorted suites, k, judge_signature).
- No test-case-level cache.

## 7. Live monitor + ReEvalRequest
- `src/ascore/live/monitor.py`: `LiveMonitor:44` `ingest(trace)` samples prod traffic,
  scores live-tagged criteria; `status()` emits `ReEvalRequest` on drift.
- `ReEvalRow` (sqlite_store.py:169) append-only; `save_reeval_request(agent_id, reason)`.

## 8. Server auth / tenancy / PAT / SSRF / budgets
- `server/auth.py`: roles viewer<operator<admin; `require_auth():145` sets
  `request.state.{role, tenant, user_email, auth_method}`.
- `server/pats.py:44` `PatStore` — SHA256 tokens, `resolve()`→(role, tenant, email),
  `revoked_at` immediate, `last_used_at` bump.
- Tenancy `server/app.py:60-110`: SQLite DB-per-tenant (`stem.{tenant}.suffix`); Postgres
  row-level tenant_id. Tenant from token/cookie → `request.state.tenant`.
- SSRF `security.py:51` `validate_blackbox_url()` — scheme allowlist, optional host
  allowlist, rejects private/loopback/reserved/metadata; used at registration + request.
- Budgets `budget.py`: `max_run_cost_usd`, `max_daily_cost_usd`, `tenant_quota():32`;
  `check_pre_run()` raises `BudgetExceededError`; `RunBudget:101` runtime accumulate.

## 9. Report + PDF renderers
- `reporting/pdf_report.py:62` `render_pdf` — fpdf2 pure-Python; `_san():37` latin-1 map.
- `reporting/scorecard_report.py`, `reporting/ab_report.py` — markdown/plaintext.

## 10. Inspect interop
- `src/ascore/interop/inspect_log.py`: `to_inspect_log()`, `from_inspect_log()`.
- `INTEROP_VERSION = 1` (:86), `_EVAL_LOG_VERSION = 2` (:89). Task/Sample/Scorer map 1:1;
  foreign scores snapped to {0, 0.5, 1}, aggregates recomputed.

## 11. CLI wiring
- `src/ascore/cli.py`: `typer` app (`app = typer.Typer()`:23), global `--tenant` (:30),
  `_ctx():39` loads config + Registry. Commands via `@app.command()`.
- Entry point: `ascore = "ascore.cli:app"` (pyproject).

## 12. Existing certification
- `src/ascore/certification.py:61` `METHODOLOGY_VERSION = "agenttic-safety-cert/v1"` —
  `SafetyDimension:71`, `extract_dimension_scores()`, composite 0–100, Ed25519 signature.
- `server/certifications.py:51` `issue_certificate` — real results only, `CertStore` global
  engine. (SPEC-1 "certificate"; SPEC-2 adds the richer Dossier/Profile/Tier model.)

## 13. Config
- `config.py:13` `load_config` — YAML → plain dict; validates judge_strong != agent_default.
- Keys: `models.*`, `budget.*`, `paths.*`, `auth.*`, `security.*`, `quotas.*`.
- `config.yaml` at repo root; `config.prod.yaml` for prod overrides.

## 14. Metrics catalog
- `metrics/catalog.py:16` `CanonicalMetric` (id, name, methodology, category, weight,
  check_refs, status); `METRICS:29` (21 canonical, 7 index-weighted).
- `index_weights():362`, `catalog_payload():367`. Datasets under `metrics/datasets/`.

## Green baseline (T0.2)
- Command: `python -m pytest -q`
- Result: **1347 passed, 4 skipped, 1 warning**
- Runtime: **133.23s** (~2m13s)
- Status: GREEN. Certification track must keep the suite ≥ this baseline with zero new skips.
