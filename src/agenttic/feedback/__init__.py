"""Feedback → tests pipeline (SPEC-2 Step 13).

Closes the OUTER learning loop: human feedback stored in Step 11 (with
provenance) is mined here into (a) draft test-suite versions gated by the
same human-approval gate as generated suites (Hard Rule 12 — no silent suite
growth) and (b) calibration labels for the judge (Hard Rule 6).
"""

from agenttic.feedback.miner import mine_cases, mine_labels

__all__ = ["mine_cases", "mine_labels"]
