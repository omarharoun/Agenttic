"""Real public-dataset ingestion adapters (BFCL + τ-bench + AgentHarm +
InjecAgent via the same DatasetAdapter pattern)."""

from __future__ import annotations

from ascore.metrics.datasets.agentharm import AgentHarmAdapter
from ascore.metrics.datasets.base import DatasetAdapter, DatasetInfo
from ascore.metrics.datasets.bfcl import BFCLAdapter
from ascore.metrics.datasets.injecagent import InjecAgentAdapter
from ascore.metrics.datasets.tau_bench import TauBenchAdapter

# dataset_id -> adapter factory (union of all sibling branches)
ADAPTERS = {"bfcl": BFCLAdapter, "tau-bench": TauBenchAdapter,
            "agentharm": AgentHarmAdapter, "injecagent": InjecAgentAdapter}


def get_adapter(dataset_id: str) -> DatasetAdapter:
    if dataset_id not in ADAPTERS:
        raise KeyError(f"unknown dataset {dataset_id!r}; known: {sorted(ADAPTERS)}")
    return ADAPTERS[dataset_id]()


def dataset_infos() -> list[DatasetInfo]:
    return [factory().info for factory in ADAPTERS.values()]


__all__ = ["DatasetAdapter", "DatasetInfo", "ADAPTERS", "get_adapter", "dataset_infos"]
