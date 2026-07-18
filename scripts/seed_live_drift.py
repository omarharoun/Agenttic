"""SPEC-5 23.4 — seed a rolling window of live scores for the demo agent so the
console drift strip-chart has real data: `injection` has drifted well below its
batch baseline (fires a re-eval), while `tool_call` / `refusal` stay stable.
Idempotent-ish (appends a fresh window each run; the chart reads the newest).
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")
from agenttic.config import load_config
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.trace import Trace, SCHEMA_VERSION

AGENT = "support-triage-agent"
N = 30

# per-response scores (newest appended last); injection degrades, others steady
def stream(mean_pattern):
    return mean_pattern


PATTERNS = {
    # tool_call: steady ~0.85
    "tool_call": [1.0, 1.0, 0.5, 1.0, 1.0] * 6,
    # refusal: steady ~0.80
    "refusal": [1.0, 0.5, 1.0, 1.0, 0.5] * 6,
    # injection: drifted low ~0.55 (baseline 0.90) -> fires
    "injection": [0.5, 0.0, 1.0, 0.5, 0.5, 0.0, 1.0, 0.5, 0.5, 0.5] * 3,
}


def main():
    reg = Registry(load_config("config.yaml")["paths"]["registry_db"])
    for i in range(N):
        tid = f"live-{AGENT}-{i:03d}"
        # a minimal trace so the score rows have a home
        reg.save_trace(Trace(
            trace_id=tid, agent_id=AGENT, agent_config_hash="cfg_promoted02",
            test_case_id=None, spans=[], visibility="black_box",
            final_output="(live sample)", schema_version=SCHEMA_VERSION),
            mode="live")
        scores = {cid: PATTERNS[cid][i] for cid in PATTERNS}
        reg.save_live_scores(AGENT, tid, scores)
    # report the resulting window means
    for cid in PATTERNS:
        w = reg.live_scores(AGENT, cid, 50)
        print(f"  {cid}: n={len(w)} mean={sum(w)/len(w):.2f}")
    print("SEED DONE — live drift window")


if __name__ == "__main__":
    main()
