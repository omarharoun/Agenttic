"""Deterministic cache keys for run results.

A run's outputs are fully determined by its inputs: the exact suite version, the
agent configuration (captured by the adapter's ``config_hash``), the rubric
version (which fixes the criteria + scales), and the judge models that score it.
Hashing those gives a stable key — identical inputs => identical key => reuse the
prior scorecard instead of re-spending agent + judge tokens.

The pass/fail threshold is intentionally NOT part of the key: it is a post-hoc
display knob applied to already-computed per-criterion scores, so it changes no
LLM work and no cached per-criterion result.
"""

from __future__ import annotations

import hashlib
import json


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def judge_signature(cfg: dict) -> dict:
    """The scoring-model identity that affects judge outputs."""
    models = cfg.get("models", {}) or {}
    return {"judge_strong": models.get("judge_strong", ""),
            "judge_light": models.get("judge_light", "")}


def scorecard_cache_key(*, agent_id: str, suite_id: str, suite_version: int,
                        agent_config_hash: str, rubric_id: str,
                        rubric_version: int, cfg: dict) -> str:
    """Cache key for one agent run against one versioned suite+rubric.

    Includes ``agent_id``: two agents are distinct subjects (the scorecard and
    the leaderboard are keyed by agent_id) even if their config_hash matches.
    """
    return _hash({
        "kind": "scorecard",
        "agent_id": agent_id,
        "suite_id": suite_id, "suite_version": suite_version,
        "agent_config_hash": agent_config_hash,
        "rubric_id": rubric_id, "rubric_version": rubric_version,
        "judge": judge_signature(cfg),
    })


def canonical_cache_key(*, agent_config_hash: str, suite_sig: list[str],
                        k: int, cfg: dict) -> str:
    """Cache key for a standard (canonical Agenttic Index) run: the agent
    config, the exact set of canonical suites present, k, and the judge models."""
    return _hash({
        "kind": "canonical",
        "agent_config_hash": agent_config_hash,
        "suites": sorted(suite_sig), "k": int(k),
        "judge": judge_signature(cfg),
    })
