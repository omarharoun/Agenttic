# Local dev targets. CI runs the same gate step (see .github/workflows/ci.yml).
.PHONY: test redteam-gate honeypot-gate

# Full backend test suite (includes the injection regression tests + gate self-test).
test:
	pytest -q

# Self-red-team injection regression gate: runs the labeled red-team corpus
# through the real injection_robust detector and exits non-zero on a regression
# (a reopened bypass or an over-correction). Deterministic, offline — no LLM/net.
# Equivalent: `uv run python scripts/redteam_gate.py`
redteam-gate:
	python scripts/redteam_gate.py

# Self-red-team honeypot harness-enforcement gate: plants decoy dangerous tools,
# tempts the agent, and exits non-zero if the enforce-posture gateway stops
# blocking a forbidden decoy call (executed-not-blocked). Deterministic, offline.
# Equivalent: `uv run python scripts/honeypot_gate.py`
honeypot-gate:
	python scripts/honeypot_gate.py
