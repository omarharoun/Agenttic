# syntax=docker/dockerfile:1
# Container entry for the Agenttic agent-safety gate.
#
# The action's default `runs.using: composite` path installs ascore in the
# runner and is the friendliest for most consumers. This image is the
# *self-contained* alternative: it bakes ascore + the gate script into a pinned
# container so the safety battery runs with ZERO network install at gate time —
# the mode used for air-gapped runners and reproducible, hermetic CI (T37.4).
#
# Build (from repo root):
#   docker build -f .github/actions/agent-safety/Dockerfile -t agenttic-gate .
# Run offline against the reference agent:
#   docker run --rm --network none -e USE_MOCK=true -e FAIL_UNDER=NONE \
#     -e GITHUB_WORKSPACE=/work -v "$PWD:/work" agenttic-gate
FROM python:3.12-slim AS gate
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /opt/agenttic
# Install ascore from source so the container is a faithful, offline copy of the
# CLI the composite action would pip-install. No extras: the gate only needs the
# core scanner + certify path, which run fully offline in --mock mode.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# The gate script itself (kept alongside the action).
COPY .github/actions/agent-safety/gate.py /opt/agenttic/gate.py

# Non-root: the gate never needs privileges.
RUN useradd --create-home --uid 10001 gate && mkdir -p /work && chown gate /work
USER gate
WORKDIR /work
ENV GITHUB_WORKSPACE=/work

# Defaults chosen so a bare `docker run --network none agenttic-gate` performs a
# hermetic self-test (offline reference agent, report-only) and exits 0.
ENV USE_MOCK=true FAIL_UNDER=NONE PROFILE=cert-agent-safety-v1
ENTRYPOINT ["python", "/opt/agenttic/gate.py"]
