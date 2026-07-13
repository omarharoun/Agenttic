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
| `src/ascore/registry/sqlite_store.py` | 618 | `sqlite:///…'ascore.db'` (default when no `db_path`) |
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

Plus `python -m ascore` via `src/ascore/__main__.py` (→ `src/agenttic/__main__.py`).
The `ascore` CLI *verb* stays a working alias; only the module target moves.

## Build config (Stage 1)

`[tool.hatch.build.targets.wheel] packages = ["src/agenttic", "src/ascore"]`
→ single `["src/agenttic"]` after the merge.
