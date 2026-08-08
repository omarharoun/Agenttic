"""Seeding a fresh database from several processes at once must not kill any
of them.

The migration race was fixed and a second one took its place one layer up. Every
`seed_*` helper is documented idempotent and written check-then-act:

    try:
        reg.get_suite(id)
        return []              # already present
    except NotFoundError:
        pass
    reg.save_rubric(...)       # <- two processes both arrive here
    reg.save_suite(...)

`save_*` was check-then-act too (SELECT, then INSERT), so on one fresh database
two workers both passed both checks and both inserted. The loser died: a raw
`IntegrityError` traceback when both SELECTed before either committed, or a
`DuplicateVersionError` when it read after. 2 of 8 concurrent `certify --mock`
runs failed on a clean directory — which is anyone parallelising in CI, and
anyone's very first run in a new checkout.

There were TWO causes, and the second only became visible once the first was
fixed:

  1. the insert race itself — closed by letting the unique constraint decide
     (attempt, and on conflict re-read and judge the winner's row on content);
  2. `Rubric` filled its default weights by iterating a SET of criterion ids,
     so key order followed randomised string hashes and the same built-in
     rubric serialised to different bytes in different processes. Content-equal
     rows therefore looked like genuine conflicts. That one is a reproducibility
     defect in its own right and is tested separately below.

What must NOT regress is the guarantee underneath: a DIFFERENT payload at the
same (id, version) is still an error. Append-only versioning is what lets a
scorecard naming `rubric v1` be re-read later and mean what it meant.

Processes, not threads: cause (2) needs separate interpreters to show itself at
all, because PYTHONHASHSEED is fixed within one process.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agenttic.registry.sqlite_store import DuplicateVersionError, Registry
from agenttic.schema.rubric import Criterion, Rubric

WORKERS = 8

# A cold start in a throwaway directory, exactly as a user or a CI job gets it.
COLD_START = """
import sys
from agenttic.registry.sqlite_store import Registry
from agenttic.metrics.standard_suites import seed_standard_suites
from agenttic.metrics.safety_suite import seed_safety_content_suite
reg = Registry(url=sys.argv[1])
seed_standard_suites(reg)
seed_safety_content_suite(reg)
"""


def _crit(cid: str, desc: str = "d") -> Criterion:
    return Criterion(criterion_id=cid, description=desc, scorer="judge",
                     scale="binary", anchors={"pass": "ok", "fail": "no"})


def _rubric(rid: str = "r", *, desc: str = "first") -> Rubric:
    return Rubric(rubric_id=rid, version=1,
                  criteria=[_crit("a", desc), _crit("b", "second")])


class TestConcurrentColdStart:
    def test_eight_processes_seeding_one_fresh_db_all_survive(self, tmp_path):
        """The reported failure, reproduced at its own level."""
        url = f"sqlite:///{tmp_path / 'cold.db'}"
        procs = [
            subprocess.Popen([sys.executable, "-c", COLD_START, url],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
            for _ in range(WORKERS)
        ]
        results = [(p.wait(timeout=300), p.communicate()[1]) for p in procs]
        failed = [(code, err[-600:]) for code, err in results if code != 0]
        assert not failed, f"{len(failed)}/{WORKERS} died: {failed[:2]}"

    def test_the_seeded_content_is_there_exactly_once(self, tmp_path):
        """A survivor count is not the result — a race that swallowed every
        insert would also report eight zeros."""
        url = f"sqlite:///{tmp_path / 'cold.db'}"
        for _ in range(WORKERS):
            subprocess.run([sys.executable, "-c", COLD_START, url],
                           check=True, capture_output=True, timeout=300)
        reg = Registry(url=url)
        suites = reg.list_suites()
        assert suites, "nothing was seeded at all"
        seen = [(s["suite_id"], s["version"]) for s in suites]
        assert len(seen) == len(set(seen)), f"a suite was stored twice: {seen}"


class TestTheRefusalIsCleanUnderRacing:
    """The loser of a race must be told the same thing the slow path tells it.

    The SELECT-first branch raised `DuplicateVersionError`; the branch where the
    constraint fired raised a raw `sqlalchemy.exc.IntegrityError`, which nothing
    above maps — hence the traceback. One fault, one error type.
    """

    def test_eight_threads_saving_one_rubric_report_one_domain_error(self, tmp_path):
        reg = Registry(url=f"sqlite:///{tmp_path / 'r.db'}")
        raised: list[BaseException] = []

        def go():
            try:
                reg.save_rubric(_rubric())
            except BaseException as exc:    # noqa: BLE001
                raised.append(exc)

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(lambda _: go(), range(WORKERS)))

        assert len(raised) == WORKERS - 1, "exactly one writer should win"
        bad = [e for e in raised if not isinstance(e, DuplicateVersionError)]
        assert not bad, f"raw database errors reached the caller: {bad[:2]}"


class TestAppendOnlyStillHolds:
    """The guarantee the fix must not have bought its way out of.

    The first attempt at this fix made an identical re-save a silent no-op. That
    is a weaker contract than the one six existing tests already encode, and it
    is load-bearing: `rubric v1` must mean v1 forever. The tolerance belongs to
    the seeders, which asked for "make sure it is there", not to `save_*`.
    """

    def test_re_saving_the_very_same_rubric_still_raises(self, tmp_path):
        reg = Registry(url=f"sqlite:///{tmp_path / 'r.db'}")
        reg.save_rubric(_rubric())
        with pytest.raises(DuplicateVersionError):
            reg.save_rubric(_rubric())

    def test_only_the_seeders_are_allowed_to_shrug(self, tmp_path):
        from agenttic.registry.sqlite_store import already_seeded

        reg = Registry(url=f"sqlite:///{tmp_path / 'r.db'}")
        reg.save_rubric(_rubric())
        with already_seeded():              # what a seed_* helper does
            reg.save_rubric(_rubric())
        assert reg.get_rubric("r").version == 1

    def test_a_seeder_does_not_swallow_an_unrelated_failure(self, tmp_path):
        from agenttic.registry.sqlite_store import already_seeded

        with pytest.raises(ValueError):
            with already_seeded():
                raise ValueError("something else entirely")


class TestRubricSerialisesTheSameEverywhere:
    """Cause (2), on its own terms.

    This is not only a concurrency concern. A rubric whose bytes depend on which
    process wrote it cannot be quoted as evidence, and the whole product rests
    on evidence someone else can re-check.
    """

    def test_default_weights_follow_criterion_order_not_hash_order(self):
        r = Rubric(rubric_id="r", version=1,
                   criteria=[_crit(c) for c in ("zebra", "apple", "mango", "kiwi")])
        assert list(r.weights) == ["zebra", "apple", "mango", "kiwi"]

    def test_the_same_builtin_rubric_serialises_identically_in_new_processes(self):
        """Separate interpreters, so each gets its own PYTHONHASHSEED."""
        prog = ("from agenttic.metrics.safety_suite import _build\n"
                "print([r.model_dump_json() for r in _build().rubrics])")
        out = {
            subprocess.run([sys.executable, "-c", prog], check=True,
                           capture_output=True, text=True,
                           cwd=Path(__file__).parent.parent, timeout=120).stdout
            for _ in range(3)
        }
        assert len(out) == 1, "one rubric, three different serialisations"
