# RENAME_AUDIT — internal package `ascore` → `agenttic`

Snapshot taken at Stage 0 on branch `rename-ascore-to-agenttic`, cut from live
`master` (`dcbea03`). Baseline suite: **1722 passed, 5 skipped** (green).

This is the staged rename of the *internal* engine package `ascore` into the
*public* umbrella package `agenttic`. SPEC-8 had already created `src/agenttic`
as a thin umbrella that re-exported from `ascore.*`; this rename folds the two
into a single `src/agenttic` package while preserving three hard back-compat
contracts (env vars, CLI verb, on-disk filenames).

## Category 1 — import hits (rewritten in Stage 2)

`from ascore …` / `import ascore …` statements and dotted-string targets that
must be rewritten `ascore` → `agenttic`:

| Where | `from/import ascore` | Notes |
|-------|---------------------:|-------|
| `src/` | 880 | intra-package + umbrella re-exports |
| `tests/` | 735 | |
| patch/dotted-string targets (`"ascore.…"`, `'ascore.…'`) | 44 | `unittest.mock.patch("ascore.x.y")`, dynamic import strings |
| **distinct files containing `ascore`** | **349** | across `src/` + `tests/` |

Rewrite scope is imports and dotted module paths ONLY. Stage 2 must NOT touch
`ASCORE_` env names (Cat 2), the `ascore.db` on-disk literal (Cat 3), or the
`ascore` CLI verb (Cat 4).

## Category 2 — `ASCORE_*` env vars (LIVE PROD CONTRACT — shimmed in Stage 3)

node1's `.env` supplies `ASCORE_CERT_SIGNING_KEY` and
`ASCORE_PASSPORT_SIGNING_KEY`. If the renamed code stops honoring `ASCORE_*`,
cert/passport signing fails **closed** and the app 502s. These are NOT renamed
on node1. Every read goes through a back-compat shim:
`AGENTTIC_<NAME>` first → `ASCORE_<NAME>` fallback (DeprecationWarning) → default.

Distinct `ASCORE_*` vars (`grep -Eoh 'ASCORE_[A-Z0-9_]+'`), 21 total:

```
ASCORE_ADMIN_EMAIL          ASCORE_ADMIN_PASSWORD       ASCORE_AIRGAP
ASCORE_API_TOKEN            ASCORE_API_TOKEN_FILE       ASCORE_BUILD
ASCORE_CERT_PUBLIC_KEYS     ASCORE_CERT_SIGNING_KEY *   ASCORE_DB
ASCORE_ENV                  ASCORE_ENVIRONMENT          ASCORE_LLM_BASE_URL
ASCORE_PASSPORT_SIGNING_KEY *  ASCORE_REDIS_URL         ASCORE_SECRET_KEY
ASCORE_SESSION_SECRET       ASCORE_SWEBENCH_HARNESS     ASCORE_TENANT
ASCORE_TEST_PG              ASCORE_TEST_REDIS           ASCORE_UI_DIST
```
`*` = signing keys; the production canary. Read sites: **51** across `src/`.

Central read paths that the shim must cover:
- `ascore/secrets.py::get_secret(name)` — env + `<name>_FILE` file fallback; used
  for `ASCORE_API_TOKEN`, `ASCORE_SECRET_KEY`, `ASCORE_ADMIN_PASSWORD`,
  `ASCORE_SESSION_SECRET`.
- direct `os.environ.get("ASCORE_…")` (cli, server/app, server/crypto,
  server/auth, server/health, server/events, server/ratelimit, airgap, …).
- module-level constants naming the var:
  `safety_cert.SIGNING_KEY_ENV = "ASCORE_CERT_SIGNING_KEY"`,
  `safety_cert.PUBLIC_KEYS_ENV = "ASCORE_CERT_PUBLIC_KEYS"`,
  `passport/keys._ENV_KEY = "ASCORE_PASSPORT_SIGNING_KEY"`,
  `metrics/swebench_resolve.HARNESS_ENV = "ASCORE_SWEBENCH_HARNESS"`.

## Category 3 — on-disk literals (fallback added in Stage 5)

No `.ascore` dot-directory exists. The only on-disk literal is the default
SQLite filename `ascore.db`:

| File | Line | Literal |
|------|-----:|---------|
| `src/agenttic/registry/sqlite_store.py` | 618 | `sqlite:///…'ascore.db'` (default when no `db_path`) |
| `tests/test_tenancy.py` | 28, 30 | test fixtures pin `ascore.db` |

DB URLs are otherwise `sqlite:///<path>` / `postgresql+psycopg://…` built from
`ASCORE_DB` or config — no hard-coded `ascore` brand. Stage 5: new installs
prefer `agenttic.db`, but an existing `ascore.db` on disk is still loaded (no
data loss).

## Category 4 — entry points (reconciled in Stage 4)

`pyproject.toml [project.scripts]` (SPEC-8 already added `agenttic`):

```
agenttic = "ascore.cli:app"   # public command → retarget to agenttic.cli:app
ascore   = "ascore.cli:app"   # keep as working DEPRECATED ALIAS → agenttic.cli:app
```

Plus `python -m ascore` via `src/agenttic/__main__.py` (→ `src/agenttic/__main__.py`).
The `ascore` CLI *verb* stays a working alias; only the module target moves.

## Build config (Stage 1)

`[tool.hatch.build.targets.wheel] packages = ["src/agenttic", "src/ascore"]`
→ single `["src/agenttic"]` after the merge.

---

# Stage 6 — final triage of every remaining `ascore`

After Stages 1–5, `grep -rIi ascore` (excluding `.git`, `*.db*`) still returns
matches. Each is classified below as **intentional** (a deliberate back-compat
contract, the deprecated alias, rename machinery, or an unrelated identifier) or
**missed** (a stale reference not yet updated). No *functional* miss remains —
every match that would affect runtime, the deploy, or the definition of done has
been resolved.

## Intentional — keep (do NOT rewrite)

| Category | ~count | Why it stays `ascore` |
|----------|-------:|-----------------------|
| `ASCORE_*` env-var reads/mentions | ~249 | LIVE PROD CONTRACT. The Stage-3 shim reads `AGENTTIC_*` first, falls back to `ASCORE_*` (DeprecationWarning). node1's `.env` (incl. the cert/passport signing keys) is unchanged. |
| On-disk `ascore.db` / `ascore.<tenant>.db` / `ascore*.db` | ~38 | Stage-5 no-data-loss fallback. `config.prod.yaml registry_db: /app/data/ascore.db` MUST stay — it points at node1's existing data. Backup/restore globs operate on those real files. |
| CLI alias (`_ascore_alias`, `ascore = "agenttic.cli:_ascore_alias"`, README/skill notes) | ~15 | The `ascore` command is a *deprecated but working* alias (Stage 4). |
| Runtime identifiers: `logging.getLogger("ascore")`, OTel `get_tracer("ascore")`, Prometheus `ascore_*` metric names, cookies `ascore_session`/`ascore_csrf`, redis key prefix `ascore:events:`, dev-only secrets `ascore-dev-insecure-*`, managed-agent env name `ascore-workflows`, health dist tuple `("agenttic","ascore")` | ~56 | Renaming these changes a channel/metric/cookie/key/secret-derivation/deploy-name — an operational break, not a code cleanup. The health probe intentionally checks BOTH distribution names. |
| Postgres role/db name `ascore` (compose, CI, backup/restore, OPERATIONS.md) | — | Names an existing database/role; renaming would orphan data. Infra choice, not the package. |
| UI identifiers: `AscoreNode` component, `.ascore-node` CSS, canvas node-type string `"ascore"` / `application/ascore-node`, localStorage/sessionStorage keys `ascore_token`/`ascore_theme`/`ascore_onboarding_*`/`ascore_editor_mode`/`ascore_key_nudge_dismissed` | ~34 | Self-consistent front-end identifiers. The node-type string must match across store/canvas/palette/templates; the storage keys persist real user state (renaming logs users out / resets their canvas). Cosmetic-only, deferred. |
| Rename-machinery docstrings (`agenttic/_env.py`, `secrets.py`, `safety_cert.py` shim comment, `cli.py` alias docstring, `test_env_shim.py`, `test_cli_entrypoints.py`, `test_tenancy.py`, RENAME_AUDIT) | — | These *describe* the `ascore→agenttic` shim/alias; the word `ascore` is the subject. |
| `CHANGELOG.md`, `docs/SPEC2_*`, `docs/HONEST_REVIEW.md` historical entries | — | History; must name what shipped at the time. |
| Vendored `metrics/datasets/**` (ATTRIBUTION.md, `*.sample.json*`) | — | Third-party corpus content; not ours to rebrand. |

## Missed — stale but non-functional (cosmetic follow-up)

| Where | ~count | Nature |
|-------|-------:|--------|
| Secondary docs' `src/agenttic/…` path references + `ascore <verb>` CLI examples (`docs/PRODUCTION_READINESS.md`, `BILLING.md`, `SPEC_INDEX.md`, `SPEC2_BASELINE.md`, `GAMING_SPEC.md`, `OTEL_INTEROP.md`, `INSPECT_INTEROP.md`, `OPERATIONS.md`, `CONNECT.md`, `CERTIFICATION.md`, `COPILOT.md`, `AIRGAP.md`, `RESEARCH_TESTING_SURVEY.md`, `adapters/README.md`, `cert-swe-v1/README.md`, example `.sh`/`.yaml`) | ~81 | The files moved to `src/agenttic/…`; the paths are now stale. The `ascore <verb>` examples still work via the alias. Non-functional — deferred to a doc sweep. Primary docs (README/SPEC/CAPABILITIES/PLAYBOOK) were fully updated in this stage. |
| UI/JS comment path refs (`ui/src/*.ts(x)` "src/agenttic/…", `verifier/js/sdk.js` "Mirrors ascore/verify/sdk.py") | ~6 | Stale source-path comments; no runtime effect. |
| `uv.lock` root `name = "ascore"` | 1 | Generated lock predates the rename. The Docker image installs via `pip install .` (reads `pyproject.toml`, name `agenttic`), so the deploy is unaffected. Regenerate with `uv lock` in a follow-up. |

## Deploy-critical fixes made in Stage 6 (were true functional misses)

* `Dockerfile` `CMD` `uvicorn --factory ascore.server.app:create_app` → `agenttic.server.app:create_app` — **the module no longer exists; this would crash-loop / 502 the container.** Also `ENV ASCORE_UI_DIST` → `AGENTTIC_UI_DIST` (shim still honors the old name).
* `scripts/quickstart_check.sh`, `.github/actions/agent-safety/gate.py` — `python -m ascore` / `-m ascore.cli` → `agenttic` (module gone).
* Copilot product knowledge (`copilot/knowledge.md`, `copilot/skill.py`) rebranded to `agenttic` (`ascore` noted as deprecated alias).

## Definition of done — verified

* baseline green (1745 → see final gate); `import agenttic` works; **zero** intra-repo `import ascore` / `from ascore` / dotted `ascore.<module>` (only `ascore.db` on-disk literal remains).
* both CLIs resolve: `agenttic --help` and `ascore --help` (alias warns) exit 0.
* `AGENTTIC_*` preferred, `ASCORE_*` honored with a DeprecationWarning; the cert + passport signing keys still load from `ASCORE_*` (integration-tested — the deploy canary).
* new installs default to `agenttic.db`; an existing `ascore.db` is still opened (no data loss).
