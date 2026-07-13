# syntax=docker/dockerfile:1
# ---- stage 1: build the React frontend ----
FROM node:20-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build            # -> /ui/dist

# ---- stage 2: python runtime ----
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# install the package (deps resolved from pyproject)
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[postgres,redis]"

# the built frontend (served by the same app). AGENTTIC_UI_DIST tells the app
# where dist lives, since the package itself is installed under site-packages.
# (The legacy ASCORE_UI_DIST is still honored by the env shim as a fallback.)
COPY --from=ui /ui/dist ./ui/dist
ENV AGENTTIC_UI_DIST=/app/ui/dist
# default config baked in; override by mounting /app/config.yaml or env vars
COPY config.yaml ./config.yaml

# non-root runtime user
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/review /app/calibration /app/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8700
# liveness/readiness available at /health and /ready
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/health').status==200 else 1)"

# single worker by default; scale with --workers once a shared event transport
# (Redis) is configured — see docs/PRODUCTION_READINESS.md.
CMD ["uvicorn", "--factory", "agenttic.server.app:create_app", \
     "--host", "0.0.0.0", "--port", "8700"]
