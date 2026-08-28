# Agenttic

Agent verification: generate a benchmark from a requirement, run an agent
against it, score it, and issue evidence someone else can check.

## Hard rules — never violate

- Never edit or delete tests to make them pass
- Never change scoring-engine behaviour or the Step 14 promotion gate
- Colours, type, spacing come from tokens only — no raw hex in components
- Shared components (ProvenanceBadge, ScoreValue, ScorecardCard) have
  exactly one implementation
- Never fabricate social proof, metrics, or figures
- Motion encodes state; no ornamental animation
- Stop and ask rather than weakening an acceptance criterion

## Workflow — use the gstack skills

Don't hand-roll these. Each one has a checklist this repo has already been
burned by skipping.

| When | Skill |
|---|---|
| Shipping anything — merge base, tests, coverage, review, VERSION, CHANGELOG, PR | `/ship` |
| Reviewing a diff before it lands, on its own | `/review` |
| A failing test or a bug you cannot explain yet | `/investigate` |
| Driving the console in a real browser | `/qa` (report-only: `/qa-only`) |
| Architecture-level review of a plan, before code | `/plan-eng-review` |
| Frontend visual audit | `/design-review` |
| Syncing docs after a release | `/document-release` |
| Recording something that would save 5+ minutes next time | `/learn` |

`/ship` owns the version. It reads `VERSION`, and `pyproject.toml` and
`agenttic.__version__` move with it — `test_dist_surface` asserts the lockstep.
Note `gstack-version-bump classify` only reads `VERSION` and `package.json`, so
on this repo it reports `0.0.0.0` when `VERSION` is absent: cross-check
`pyproject.toml` and `git tag -l` before trusting a bump level.

## Verify

- Backend: `NO_COLOR=1 pytest -q`
- UI: `cd ui && npm run verify` (lint + `tsc --noEmit` + vitest)

Three environment traps, each of which has produced a confidently wrong result
here:

- **`NO_COLOR=1` is not optional.** With `FORCE_COLOR` set, Rich emits ANSI
  escapes and eight CLI tests that assert plain text fail — on unmodified
  `master`. They are not a regression you introduced.
- **Running `pytest` from a git worktree needs `PYTHONPATH`.** The venv lives in
  the main checkout and its editable `.pth` hardcodes that path, so a bare
  `pytest` in a worktree imports **master's** code and silently tests the wrong
  tree. Use
  `PYTHONPATH=<worktree>/src <main-checkout>/.venv/bin/pytest`.
- **`test_version_matches_distribution_metadata` fails locally after a version
  bump.** It compares installed dist metadata against `__version__`, and the
  editable install still carries the old number. It passes on a fresh
  `pip install -e .`, which is what CI does.

A UI parse error hides everything behind it: one unclosed JSX tag stops `tsc`
typechecking the whole project, so the error count understates the damage. Fix
parse errors first, then re-run before judging scope.
