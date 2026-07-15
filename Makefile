# Local dev targets. CI runs the same gate step (see .github/workflows/ci.yml).
.PHONY: test redteam-gate

# Full backend test suite (includes the injection regression tests + gate self-test).
test:
	pytest -q

# Self-red-team injection regression gate: runs the labeled red-team corpus
# through the real injection_robust detector and exits non-zero on a regression
# (a reopened bypass or an over-correction). Deterministic, offline — no LLM/net.
# Equivalent: `uv run python scripts/redteam_gate.py`
redteam-gate:
	python scripts/redteam_gate.py
