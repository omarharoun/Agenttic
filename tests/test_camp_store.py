"""CampStore persistence: the async lifecycle (running → progress → succeeded),
the failure path, and the stale-run sweep (orphaned 'running' rows from a dead
process get marked failed on startup)."""

from agenttic.camp.store import CampStore
from agenttic.migrations import run_migrations
from agenttic.registry.sqlite_store import make_engine


def _store(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/c.db")
    run_migrations(engine)
    return CampStore(engine)


def _mk(store, run_id="r1", total=500):
    store.create_run(run_id, kind="single", task_id="support_triage", mode="mock",
                     agent_label="", threshold=0.99, min_episodes_for_gate=200,
                     seed=0, total_episodes=total)


def test_create_run_starts_running_with_progress(tmp_path):
    s = _store(tmp_path)
    _mk(s)
    run = s.get_run("r1")
    assert run["status"] == "running"
    assert run["total_episodes"] == 500
    assert run["episodes_completed"] == 0
    assert run["updated_at"] is not None


def test_update_progress_then_finish(tmp_path):
    s = _store(tmp_path)
    _mk(s)
    s.update_progress("r1", episodes_completed=250, phase="250/500 episodes")
    mid = s.get_run("r1")
    assert mid["episodes_completed"] == 250 and mid["status"] == "running"
    assert mid["phase"] == "250/500 episodes"
    s.finish_run("r1", episodes=500, passes=430, wilson_lower_95=0.82,
                 pass_rate=0.86, report={"x": 1}, gate={"promoted": False})
    done = s.get_run("r1")
    assert done["status"] == "succeeded"
    assert done["episodes_completed"] == 500 and done["passes"] == 430
    assert done["finished_at"] is not None


def test_progress_does_not_resurrect_terminal_run(tmp_path):
    s = _store(tmp_path)
    _mk(s)
    s.fail_run("r1", "boom")
    s.update_progress("r1", episodes_completed=999, phase="late")
    run = s.get_run("r1")
    assert run["status"] == "failed"          # not resurrected
    assert run["episodes_completed"] != 999


def test_fail_run_records_message(tmp_path):
    s = _store(tmp_path)
    _mk(s)
    s.fail_run("r1", "RuntimeError: kaboom")
    run = s.get_run("r1")
    assert run["status"] == "failed"
    assert "kaboom" in run["error"]


def test_interrupt_orphans_sweeps_running_only(tmp_path):
    s = _store(tmp_path)
    _mk(s, "running-one")
    _mk(s, "done-one")
    s.finish_run("done-one", episodes=500, passes=500, wilson_lower_95=0.99,
                 pass_rate=1.0, report={}, gate={"promoted": True})
    swept = s.interrupt_orphans()
    assert swept == 1
    orphan = s.get_run("running-one")
    assert orphan["status"] == "failed"
    assert "restart" in orphan["error"]
    # the succeeded run is untouched
    assert s.get_run("done-one")["status"] == "succeeded"
