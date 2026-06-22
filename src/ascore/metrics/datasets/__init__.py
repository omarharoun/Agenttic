"""Real public-dataset ingestion adapters (BFCL + τ-bench + AgentHarm +
InjecAgent + AgentDojo via the same DatasetAdapter pattern)."""

from __future__ import annotations

from ascore.metrics.datasets.agentdojo import AgentDojoAdapter
from ascore.metrics.datasets.agentharm import AgentHarmAdapter
from ascore.metrics.datasets.assistantbench import AssistantBenchAdapter
from ascore.metrics.datasets.base import DatasetAdapter, DatasetInfo
from ascore.metrics.datasets.bfcl import BFCL_SPLIT_ADAPTERS, BFCLAdapter
from ascore.metrics.datasets.gaia import GAIAAdapter
from ascore.metrics.datasets.injecagent import InjecAgentAdapter
from ascore.metrics.datasets.swebench import SWEBenchAdapter
from ascore.metrics.datasets.tau_bench import TauBenchAdapter

# dataset_id -> adapter factory (union of all sibling branches)
ADAPTERS = {"bfcl": BFCLAdapter, "tau-bench": TauBenchAdapter,
            "agentharm": AgentHarmAdapter, "injecagent": InjecAgentAdapter,
            "agentdojo": AgentDojoAdapter,
            # AssistantBench — realistic web-agent QA, fractional answer accuracy
            # + answer rate (Apache-2.0; vendored dev sample).
            "assistantbench": AssistantBenchAdapter,
            # GAIA general AI-assistant benchmark (gated; validation split).
            "gaia": GAIAAdapter,
            # SWE-bench Verified — real GitHub-issue code-fix benchmark (MIT).
            # Scored by an OFFLINE PROXY (patch produced / gold files localized);
            # official resolve-rate needs the Docker execution harness (future).
            "swebench": SWEBenchAdapter,
            # additional BFCL v3 splits (parallel / multiple / parallel_multiple
            # / live_*) — share the BFCL vendored data + license.
            **BFCL_SPLIT_ADAPTERS}


def get_adapter(dataset_id: str) -> DatasetAdapter:
    if dataset_id not in ADAPTERS:
        raise KeyError(f"unknown dataset {dataset_id!r}; known: {sorted(ADAPTERS)}")
    return ADAPTERS[dataset_id]()


def dataset_infos() -> list[DatasetInfo]:
    return [factory().info for factory in ADAPTERS.values()]


__all__ = ["DatasetAdapter", "DatasetInfo", "ADAPTERS", "get_adapter", "dataset_infos"]
