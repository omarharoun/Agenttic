"""The archetype tree + inheritance resolution (SPEC-9 Step 39).

An archetype inherits its parent's core criteria and specializes them. Resolving
a core walks the lineage root-to-leaf and unions criteria by ``criterion_id``;
on a conflict the descendant (child) wins and the override is recorded, so a
resolved core = parent criteria ∪ own, conflicts child-wins, provenance kept.
"""

from __future__ import annotations

from agenttic.rubric_engine.cores import SEED_ARCHETYPES, SEED_CORES
from agenttic.schema.archetype import Archetype, ResolvedCore
from agenttic.schema.rubric import Criterion, Rubric


def _lineage(archetype_id: str, archetypes: dict[str, Archetype]) -> list[str]:
    """Root-to-leaf archetype ids for ``archetype_id`` (leaf last)."""
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = archetype_id
    while cur is not None:
        if cur in seen:
            raise ValueError(f"archetype cycle detected at {cur}")
        if cur not in archetypes:
            raise ValueError(f"unknown archetype in lineage: {cur}")
        seen.add(cur)
        chain.append(cur)
        cur = archetypes[cur].parent_id
    chain.reverse()
    return chain


def resolve_core(
    archetype_id: str,
    *,
    archetypes: dict[str, Archetype] | None = None,
    cores: dict[str, Rubric] | None = None,
) -> tuple[Rubric, ResolvedCore]:
    """Resolve an archetype's effective core rubric through inheritance.

    Returns the composed :class:`Rubric` and a :class:`ResolvedCore` record of
    how it was assembled (lineage, per-criterion source, overrides, features).
    """
    archetypes = archetypes or SEED_ARCHETYPES
    cores = cores or SEED_CORES
    lineage = _lineage(archetype_id, archetypes)

    merged: dict[str, Criterion] = {}          # criterion_id -> criterion
    source: dict[str, str] = {}                 # criterion_id -> contributing arch
    weights: dict[str, float] = {}
    overridden: list[str] = []
    features: list[str] = []

    for arch_id in lineage:                     # root first, leaf last => child wins
        arch = archetypes[arch_id]
        core = cores.get(arch.core_rubric_id)
        if core is None:
            raise ValueError(
                f"archetype {arch_id}: core rubric {arch.core_rubric_id} not found")
        for c in core.criteria:
            if c.criterion_id in merged and source[c.criterion_id] != arch_id:
                overridden.append(c.criterion_id)   # a descendant overrides an ancestor
            merged[c.criterion_id] = c
            source[c.criterion_id] = arch_id
            weights[c.criterion_id] = core.weights.get(c.criterion_id, 1.0)
        for f in arch.required_suite_features:
            if f not in features:
                features.append(f)

    leaf = archetypes[archetype_id]
    resolved_id = f"core-{archetype_id}-resolved-v{leaf.version}"
    rubric = Rubric(
        rubric_id=resolved_id,
        version=leaf.version,
        criteria=list(merged.values()),
        weights={cid: weights[cid] for cid in merged},
    )
    record = ResolvedCore(
        archetype_id=archetype_id,
        rubric_id=resolved_id,
        lineage=lineage,
        criterion_source=source,
        overridden=sorted(set(overridden)),
        required_suite_features=features,
    )
    return rubric, record


def validate_seed_taxonomy(
    archetypes: dict[str, Archetype] | None = None,
    cores: dict[str, Rubric] | None = None,
) -> None:
    """Fail loudly if the seed taxonomy is malformed: every archetype resolves,
    every core is a valid rubric, every parent exists. Called at import-adjacent
    boundaries and in the acceptance tests (Step 39)."""
    archetypes = archetypes or SEED_ARCHETYPES
    cores = cores or SEED_CORES
    for aid, arch in archetypes.items():
        if arch.parent_id is not None and arch.parent_id not in archetypes:
            raise ValueError(f"archetype {aid}: unknown parent {arch.parent_id}")
        if arch.core_rubric_id not in cores:
            raise ValueError(f"archetype {aid}: missing core {arch.core_rubric_id}")
        # resolving exercises lineage + rubric validation (raises on any fault)
        resolve_core(aid, archetypes=archetypes, cores=cores)
