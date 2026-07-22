"""The compounding library (SPEC-9 Step 43) — four sources, one versioned store.

Every engagement makes the library smarter, so the tenth client of an archetype
costs a fraction of the first. The four sources feeding one store:

1. **Authored cores** (Step 39) — owned, curated, the quality floor.
2. **Mined from engagements** — criteria that proved discriminating and stable
   get proposed (human-gated) into the relevant archetype core as a new version;
   failure-mode catalogues grow. (The Step-13 miner, pointed at rubrics.)
3. **Imported benchmarks** — τ/BFCL/AgentHarm/SWE-bench register as archetype
   *exemplars*: their criteria enrich the matching core, their tasks seed
   reference panels for the discrimination gate (Step 42).
4. **Novel archetypes** — recurring ``custom`` agents cluster into proposed new
   archetypes, so the taxonomy itself learns.

Every library criterion carries provenance and a running discrimination track
record across the agents it has scored; criteria that stop discriminating are
retired, not kept for appearance (Hard Rule 40). Cores are versioned IP; mined
additions are human-gated into cores, never auto-merged (Hard Rule 42).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from agenttic.rubric_engine.cores import SEED_ARCHETYPES, SEED_CORES
from agenttic.rubric_engine.discrimination import DiscriminationResult
from agenttic.schema.archetype import Archetype
from agenttic.schema.rubric import Criterion, Rubric

# Which archetype a public benchmark exemplifies.
BENCHMARK_ARCHETYPE = {
    "tau_bench": "conversational_transactional",
    "agentharm": "conversational_transactional",   # cross-cutting safety pressure
    "swebench": "coding",
    "bfcl": "workflow_automation",
    "agentdojo": "workflow_automation",
    "injecagent": "workflow_automation",
    "gaia": "research_analysis",
    "assistantbench": "research_analysis",
}

DISCRIMINATION_FLOOR = 0.05     # mean spread below this == stopped discriminating
_STOPWORDS = frozenset(
    "a an the that this it its of to and or for with from into over agent "
    "user users an as is are be by on in at it's you your our their they them "
    "when where which who what how does can will would each other also".split())


@dataclass
class LibraryCriterion:
    """A criterion in the library with its origin and discrimination history."""

    criterion: Criterion
    provenance: str                 # "authored:<arch>" | "mined:<eng>" | "imported:<bench>"
    archetype_id: str
    track_record: list[float] = field(default_factory=list)  # spreads observed

    @property
    def observations(self) -> int:
        return len(self.track_record)

    @property
    def mean_discrimination(self) -> float:
        return sum(self.track_record) / len(self.track_record) if self.track_record else 0.0


@dataclass
class Proposal:
    """A human-gated change to the library. Nothing lands in a core until an
    operator approves it (Hard Rule 42)."""

    kind: str                       # "add_criterion" | "new_archetype"
    archetype_id: str
    rationale: str
    provenance: str
    criterion: Criterion | None = None
    signals: list[str] = field(default_factory=list)   # for new_archetype
    approved: bool = False


@dataclass
class Exemplar:
    """An imported benchmark registered against an archetype."""

    benchmark_id: str
    archetype_id: str
    criteria: list[Criterion]
    seed_case_count: int
    seed_cases: list = field(default_factory=list)


def _prov_from_tags(c: Criterion, archetype_id: str) -> str:
    for t in c.tags:
        if t.startswith("prov:"):
            return f"{t[5:]}:{archetype_id}"
    return f"authored:{archetype_id}"


class RubricLibrary:
    """The versioned store. Seeds from the authored cores; grows via proposals."""

    def __init__(self, archetypes: dict[str, Archetype] | None = None,
                 cores: dict[str, Rubric] | None = None):
        self.archetypes: dict[str, Archetype] = dict(archetypes or SEED_ARCHETYPES)
        # core_rubric_id -> version history (latest last)
        self._cores: dict[str, list[Rubric]] = {
            rid: [r] for rid, r in (cores or SEED_CORES).items()}
        # criterion_id -> LibraryCriterion (provenance + track record)
        self._criteria: dict[str, LibraryCriterion] = {}
        for aid, arch in self.archetypes.items():
            core = self.core(arch.core_rubric_id)
            if core is None:
                continue
            for c in core.criteria:
                self._criteria.setdefault(
                    c.criterion_id,
                    LibraryCriterion(c, _prov_from_tags(c, aid), aid))
        self.exemplars: list[Exemplar] = []
        self.proposals: list[Proposal] = []

    # -- source 1: authored cores ------------------------------------------
    def core(self, core_rubric_id: str) -> Rubric | None:
        v = self._cores.get(core_rubric_id)
        return v[-1] if v else None

    def core_version(self, core_rubric_id: str) -> int:
        core = self.core(core_rubric_id)
        return core.version if core else 0

    def criterion(self, criterion_id: str) -> LibraryCriterion | None:
        return self._criteria.get(criterion_id)

    # -- track record + retire (Hard Rule 40) ------------------------------
    def record_discrimination(self, criterion_id: str, spread: float) -> None:
        lc = self._criteria.get(criterion_id)
        if lc is not None:
            lc.track_record.append(round(float(spread), 4))

    def ingest_discrimination_result(self, result: DiscriminationResult) -> None:
        """Fold a whole discrimination run into the track records."""
        for cd in result.per_criterion:
            self.record_discrimination(cd.criterion_id, cd.spread)

    def retire_candidates(self, *, floor: float = DISCRIMINATION_FLOOR,
                          min_n: int = 3) -> list[str]:
        """Criteria that have stopped discriminating across >= min_n agents."""
        return sorted(
            cid for cid, lc in self._criteria.items()
            if lc.observations >= min_n and lc.mean_discrimination < floor)

    # -- source 2: mine from an engagement ---------------------------------
    def propose_from_engagement(
        self, archetype_id: str, result: DiscriminationResult, *,
        engagement: str, min_spread: float = 0.2,
        draft_criteria: list[Criterion] | None = None,
    ) -> list[Proposal]:
        """Propose the delta criteria that proved discriminating + stable into
        the archetype core, human-gated. A criterion qualifies if its spread in
        this engagement cleared ``min_spread`` and it is not already in the
        core."""
        core = self.core(self.archetypes[archetype_id].core_rubric_id)
        existing = {c.criterion_id for c in core.criteria} if core else set()
        by_id = {c.criterion_id: c for c in (draft_criteria or [])}
        out: list[Proposal] = []
        for cd in result.per_criterion:
            if cd.criterion_id in existing or not cd.discriminates:
                continue
            if cd.spread < min_spread:
                continue
            crit = by_id.get(cd.criterion_id)
            if crit is None:
                continue                     # only mine criteria we can carry
            p = Proposal(
                kind="add_criterion", archetype_id=archetype_id,
                rationale=(f"discriminated the {engagement} panel "
                           f"(spread {cd.spread:.2f} >= {min_spread})"),
                provenance=f"mined:{engagement}", criterion=crit)
            out.append(p)
            self.proposals.append(p)
        return out

    def approve(self, proposal: Proposal) -> Rubric | Archetype:
        """Apply a human-approved proposal. For a criterion add, bump the core to
        a new version with the criterion (provenance recorded). For a new
        archetype, register it. Never auto-merges — must be called explicitly."""
        proposal.approved = True
        if proposal.kind == "add_criterion":
            arch = self.archetypes[proposal.archetype_id]
            core = self.core(arch.core_rubric_id)
            assert core is not None and proposal.criterion is not None
            tags = [t for t in proposal.criterion.tags if not t.startswith("prov:")]
            tags += [f"prov:{proposal.provenance.split(':')[0]}",
                     f"arch:{proposal.archetype_id}", f"src:{proposal.provenance}"]
            crit = proposal.criterion.model_copy(update={"tags": tags})
            new_core = Rubric(
                rubric_id=core.rubric_id, version=core.version + 1,
                criteria=list(core.criteria) + [crit])
            self._cores[core.rubric_id].append(new_core)
            self._criteria[crit.criterion_id] = LibraryCriterion(
                crit, proposal.provenance, proposal.archetype_id)
            return new_core
        if proposal.kind == "new_archetype":
            arch = Archetype(
                archetype_id=proposal.archetype_id, name=proposal.archetype_id,
                description=proposal.rationale,
                signals=proposal.signals,
                core_rubric_id=f"core-{proposal.archetype_id}-v1",
                required_suite_features=["pressure_case"],
                failure_modes=[])
            self.archetypes[arch.archetype_id] = arch
            return arch
        raise ValueError(f"unknown proposal kind {proposal.kind}")

    # -- source 3: import a benchmark as an exemplar -----------------------
    def register_exemplar(self, benchmark_id: str, adapter, *,
                          archetype_id: str | None = None,
                          full: bool = False) -> Exemplar:
        """Register a benchmark (a DatasetAdapter, or any object exposing
        ``rubric()`` and ``load_records(full=...)``) as an archetype exemplar:
        its criteria enrich the core (as proposals) and its tasks seed a
        reference panel for Step 42."""
        aid = archetype_id or BENCHMARK_ARCHETYPE.get(benchmark_id)
        if aid is None or aid not in self.archetypes:
            raise ValueError(
                f"no archetype mapping for benchmark {benchmark_id}")
        try:
            bench_rubric = adapter.rubric()
            criteria = list(bench_rubric.criteria)
        except Exception:
            criteria = []
        try:
            cases = list(adapter.load_records(full=full))
        except TypeError:
            cases = list(adapter.load_records())
        except Exception:
            cases = []
        ex = Exemplar(benchmark_id=benchmark_id, archetype_id=aid,
                      criteria=criteria, seed_case_count=len(cases),
                      seed_cases=cases)
        self.exemplars.append(ex)
        # enrich: propose each benchmark criterion the core doesn't already have
        core = self.core(self.archetypes[aid].core_rubric_id)
        existing = {c.criterion_id for c in core.criteria} if core else set()
        for c in criteria:
            if c.criterion_id in existing:
                continue
            self.proposals.append(Proposal(
                kind="add_criterion", archetype_id=aid,
                rationale=f"imported from benchmark {benchmark_id}",
                provenance=f"imported:{benchmark_id}", criterion=c))
        return ex

    def reference_panel_seed(self, archetype_id: str) -> list:
        """Seed cases for an archetype's reference panel, from its exemplars."""
        return [case for ex in self.exemplars if ex.archetype_id == archetype_id
                for case in ex.seed_cases]

    # -- source 4: cluster recurring custom agents into new archetypes -----
    def cluster_custom(self, descriptions: list[str], *,
                       min_cluster: int = 3) -> list[Proposal]:
        """A recurring ``custom`` agent shape becomes a proposed new archetype.
        Cluster descriptions by their most frequent significant shared token; a
        cluster of >= min_cluster surfaces a proposal (human-promoted)."""
        tokens_per_desc = [
            {w for w in re.findall(r"[a-z]+", d.lower())
             if len(w) > 3 and w not in _STOPWORDS}
            for d in descriptions]
        freq = Counter(w for toks in tokens_per_desc for w in toks)
        out: list[Proposal] = []
        used: set[int] = set()
        for token, count in freq.most_common():
            if count < min_cluster:
                break
            members = [i for i, toks in enumerate(tokens_per_desc)
                       if token in toks and i not in used]
            if len(members) < min_cluster:
                continue
            used.update(members)
            shared = Counter(
                w for i in members for w in tokens_per_desc[i]).most_common(6)
            signals = [w for w, _ in shared]
            aid = f"custom_{token}"
            if aid in self.archetypes:
                continue
            p = Proposal(
                kind="new_archetype", archetype_id=aid,
                rationale=(f"{len(members)} custom agents cluster around "
                           f"'{token}' — candidate new archetype"),
                provenance="clustered:custom", signals=signals)
            out.append(p)
            self.proposals.append(p)
        return out
