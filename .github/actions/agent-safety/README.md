# Agenttic Agent Safety Gate (GitHub Action)

Run a safety battery against your AI agent on every pull request and **block the
merge if its grade drops**. Runs entirely in CI — no production access, no
runtime latency, no data leaving your pipeline.

This is the lowest-friction way to adopt Agenttic: it needs no hosted account and
no access to your live traffic. The agent is exercised in the CI runner against
published, licensed safety suites (injection robustness, harmful-action refusal,
tool-scope adherence, and more), then graded A / B / C. The grade posts as a
status check and a PR comment.

## 60-second start (self-test)

1. Copy `.github/workflows/agent-safety.yml` into your repo.
2. Open a PR. With the defaults (mock mode, `fail-under: NONE`) it runs against a
   built-in reference agent and posts a summary — so you can see it work before
   wiring your own agent.

## Gate your real agent

In the workflow, set:

```yaml
with:
  agent-url: "https://staging.your-agent.com/chat"   # your endpoint
  agent-auth-header: ${{ secrets.AGENT_AUTH }}       # optional, via secret
  fail-under: "B"        # block the merge below grade B
  mock: "false"
```

Then make the check **required**: Settings → Branches → branch protection → require
the "Agent Safety" status. Now a PR that regresses your agent's safety can't merge.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `agent-url` | `""` | Black-box HTTP endpoint to test. Empty → built-in reference agent. |
| `profile` | `cert-agent-safety-v1` | Certification profile (suite of safety tests). |
| `fail-under` | `B` | Minimum grade to pass. `A`/`B`/`C`/`NONE`. `NONE` = report-only. |
| `mock` | `false` | Offline deterministic provider (no API key). For self-tests/demos. |
| `agent-auth-header` | `""` | Authorization header sent to `agent-url`. Pass via a secret. |
| `comment-on-pr` | `true` | Post/update a Markdown summary comment on the PR. |
| `base-dossier` | `""` | Path to the base-branch dossier JSON. Enables per-dimension deltas + regression gating. |
| `regression-check` | `true` | Fail on a regression vs `base-dossier` (only when set). `false` = show deltas without gating. |

## Outputs

| Output | Meaning |
|---|---|
| `grade` | The grade the agent received (`A`/`B`/`C`). |
| `passed` | Whether the check passed (grade met `fail-under` **and** no regression). |
| `dossier-path` | Path to the signed dossier JSON (upload it as a build artifact if you want history). |
| `regressed` | Whether any dimension regressed vs the base dossier. |

## What a grade means — and doesn't

The PR comment always shows a **coverage table**. Domains marked **NOT ASSESSED**
are not covered by the current profile's suites — the grade attests only to what
was tested. This is deliberate: a safety gate that overstated its coverage would
be worse than none. Read the table before relying on the grade, and raise it with
us if a domain you care about is uncovered.

Grades are **pinned to the tested agent version** (model + prompt + tools). Change
any of them and the grade no longer applies — which is exactly why running this on
every PR is the point: it re-checks the thing that changed.

## Self-contained / offline mode

This action needs **no hosted Agenttic account and no network egress at gate
time**. Two independent guarantees:

- **`mock: "true"`** runs the safety battery through an offline deterministic
  provider — no API key, no external model calls. Used for the self-test and for
  air-gapped runners.
- **Container entry.** Instead of the default composite path (which `pip install`s
  ascore in the runner), you can run the gate from a pinned image that already
  contains ascore + the gate script, so nothing is fetched at gate time:

  ```bash
  # build once (from the repo root), then run hermetically:
  docker build -f .github/actions/agent-safety/Dockerfile -t agenttic-gate .
  docker run --rm --network none \
    -e USE_MOCK=true -e FAIL_UNDER=B \
    -e GITHUB_WORKSPACE=/work -v "$PWD:/work" agenttic-gate
  ```

  `--network none` proves the point: the whole battery + grade completes with
  the network disabled. This is the mode used by the air-gapped deployment
  (SPEC-7 Step 38).

Nothing about the grade requires a hosted service: the dossier is content-hashed
and signed locally and can be verified offline.

## Gate on regressions vs the base branch (advanced)

The absolute grade gate (`fail-under`) catches a bad grade. To also catch a
*quiet erosion* — injection robustness slipping while the letter grade holds —
supply the base branch's dossier so the gate can diff dimensions:

```yaml
- name: Certify base branch
  uses: ./.github/actions/agent-safety
  with:
    fail-under: "NONE"          # base run is measurement-only
    comment-on-pr: "false"
  # → writes agenttic-dossier.json for the base; save it as agenttic-base.json

- name: Certify PR head + gate on regression
  uses: ./.github/actions/agent-safety
  with:
    fail-under: "B"
    base-dossier: "agenttic-base.json"   # per-dimension deltas + regression gate
    regression-check: "true"             # "false" → show deltas without blocking
```

Any dimension that drops vs base fails the check and is named in the PR comment
(and as a GitHub error annotation), even when the grade is unchanged.

## How it works

The action installs the `ascore` CLI, runs `ascore certify` against your agent,
parses the signed dossier, and exits non-zero when the grade is below your bar —
which fails the required check and blocks the merge. The dossier is
content-hashed and signed, so the grade it reports can be independently verified.
