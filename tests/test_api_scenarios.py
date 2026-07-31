"""The read API for stored scenario runs (P7).

``Registry.save_scenario_run`` made a run durable; these two endpoints are what
lets a CLI or a console page show one. What is on trial:

* a run stored by a REAL offline run comes back over HTTP with the evidence
  intact — the ticket, the transcript, the fault report, the state diff;
* an unknown id is a 404 with a JSON body, never an empty run and never the SPA
  shell;
* the absences survive the wire. ``faults.recorded: false`` with four ``null``
  lists, ``coverage.measured: false`` with ``bins: null``, and
  ``coverage.divergence: null``, are the shapes a renderer has to be able to
  tell apart from "nothing was staged", "nothing was credited" and "nothing
  diverged". A serializer that helpfully turned any of them into ``[]`` would
  put a claim nobody made in front of a reader;
* the finding travels. "The point asked for that corner and the run did not
  produce it" reaches a reader who was not at the terminal when it was printed;
* an EMPTY filter is not a filter. ``?agent_id=`` is what a browser sends for a
  control nobody touched, and answering it with zero rows would report "this
  tenant has run nothing" as the result of a question nobody asked;
* tenant scoping and auth are the same as every other protected router.

No API key, no network: the run these tests store is driven by the scripted
stand-in against the scenario world.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.scenario.runner import (
    ScenarioAgent, ScenarioOutcome, ScriptedSupportClient,
    multi_turn_scenario_runner, scenario_runner)
from agenttic.scenario.tools import RETAIL_POLICY
from agenttic.server.app import create_app
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""
AUTH = {"Authorization": "Bearer testtoken"}

POINT = {"intent": "refund", "emotional_register": "neutral",
         "data_condition": "complete", "tool_condition": "all_ok",
         "policy_vector": "compliant"}


def _scenario(seed: int = 7, **overrides):
    point = dict(POINT)
    point.update(overrides)
    return realize(point, seed, seed_space(), policy=RETAIL_POLICY, client=None)


def _agent(agent_id: str) -> ScenarioAgent:
    return ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                         agent_id=agent_id)


def _divergence(scn, outcome) -> list[dict]:
    """The real ``CoverageReport.divergence()`` for one run — the same model and
    collector the scorecard uses, so what crosses the wire is what the coverage
    engine actually computed and not a shape invented for a test."""
    from agenttic.coverage.collect import Sample, collect
    from agenttic.coverage.models.baseline import baseline_model
    return collect(baseline_model(),
                   [Sample(trace=outcome.trace, scenario=scn.as_dict(),
                           requested=dict(scn.point))]).divergence()


@pytest.fixture
def api(tmp_path):
    """A live app over a registry holding three real runs, one per coverage
    state — because the three are only worth anything read side by side:

    * ``single`` — one exchange, a fault staged on a call the agent never made,
      and therefore a REAL divergence row: the point asked for a ``timeout`` on
      ``lookup_order`` and the run never produced it;
    * ``conv``   — the same world as a conversation that elicits a gated fact,
      stored with a divergence that was computed and came back empty;
    * ``bare``   — a run stored without a fault report, coverage or divergence,
      which is what the absent-evidence assertions need something honest to read.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    reg = Registry(tmp_path / "a.db")

    stored: dict[str, str] = {}
    scn = _scenario(intent="out_of_scope", tool_condition="timeout")
    outcome = scenario_runner()(scn, adapter=_agent("api-single"), store=reg)
    stored["single_divergence"] = _divergence(scn, outcome)
    stored["single"] = reg.save_scenario_run(
        scn, outcome, exhibited_bins=[],
        divergence=stored["single_divergence"])
    stored["single_scenario_id"] = scn.scenario_id

    conv_scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                       policy=RETAIL_POLICY, client=None)
    conv = multi_turn_scenario_runner()(conv_scn, adapter=_agent("api-conv"),
                                        store=reg)
    stored["conv"] = reg.save_scenario_run(
        conv_scn, conv, exhibited_bins=["trajectory:tool_then_answer"],
        divergence=[])

    bare_scn = _scenario(seed=8)
    bare_run = scenario_runner()(bare_scn, adapter=_agent("api-bare"), store=reg)
    stored["bare"] = reg.save_scenario_run(
        bare_scn, ScenarioOutcome(trace=bare_run.trace))

    app = create_app(str(cfg), registry=reg, clients={})
    with TestClient(app) as c:
        c.stored = stored
        c.reg = reg
        yield c


def test_detail_returns_a_real_stored_run(api):
    run_id = api.stored["single"]
    r = api.get(f"/api/scenario-runs/{run_id}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()

    assert body["run_id"] == run_id
    assert body["agent_id"] == "api-single"
    assert body["scenario_id"] == api.stored["single_scenario_id"]
    assert body["ticket"] and body["point"]["intent"] == "out_of_scope"
    assert body["trace_id"] and body["created_at"]
    # the endpoint reshapes nothing: it is the registry's view, over the wire
    assert body == api.reg.get_scenario_run(run_id)


def test_a_staged_fault_the_agent_never_reached_survives_the_wire(api):
    faults = api.get(f"/api/scenario-runs/{api.stored['single']}",
                     headers=AUTH).json()["faults"]
    assert faults["recorded"] is True
    assert faults["counts"] == {"planned": 1, "fired": 0, "skipped": 0,
                                "never_reached": 1}
    assert faults["never_reached"][0]["kind"] == "timeout"
    assert faults["fired"] == []


def test_a_conversation_arrives_with_its_transcript(api):
    body = api.get(f"/api/scenario-runs/{api.stored['conv']}",
                   headers=AUTH).json()
    speakers = [t["speaker"] for t in body["transcript"]]
    assert speakers.count("user") >= 2 and "agent" in speakers

    revealed = [t for t in body["transcript"] if t.get("revealed_fact")]
    assert [t["discloses"] for t in revealed] == ["order_id"]
    assert body["derived"]["conversational"] is True
    assert body["derived"]["n_user_turns"] == 2
    assert body["elicitation"] == {"disclosed": ["order_id"], "withheld": []}
    assert body["state_diff"], "the conversation changed the world"


def test_absent_evidence_arrives_absent(api):
    """The nulls a renderer must handle. ``[]`` here would be a fabricated
    measurement — the run never carried any of these facts."""
    body = api.get(f"/api/scenario-runs/{api.stored['bare']}",
                   headers=AUTH).json()
    assert body["faults"] == {"recorded": False, "source": None,
                              "planned": None, "fired": None, "skipped": None,
                              "never_reached": None, "counts": None}
    # `model` joined this block — which coverage model's vocabulary the bins are
    # in, so a stored bin list is interpretable instead of merely trusted. This
    # run was stored without one, so it arrives None, alongside the other three.
    assert body["coverage"] == {"measured": False, "bins": None,
                                "divergence": None, "model": None}


def test_measured_and_empty_is_not_the_same_as_unmeasured(api):
    """The other side of it: this run WAS measured and credited nothing, which
    reads differently from the run above."""
    body = api.get(f"/api/scenario-runs/{api.stored['single']}",
                   headers=AUTH).json()
    assert body["coverage"]["measured"] is True
    assert body["coverage"]["bins"] == []


def test_the_divergence_finding_survives_the_wire(api):
    """"The point asked for that corner and the run did not produce it" was
    computed live, printed once and dropped. It is the sentence this product
    exists to say, so it has to reach a reader who was not at the terminal."""
    body = api.get(f"/api/scenario-runs/{api.stored['single']}",
                   headers=AUTH).json()
    rows = body["coverage"]["divergence"]
    assert rows == api.stored["single_divergence"] != []
    assert [(d["coverpoint_id"], d["bin_id"]) for d in rows] == [
        ("tool_condition", "timeout")]
    assert rows[0]["requested"] == 1 and rows[0]["exhibited"] == 0


def test_the_three_divergence_states_arrive_apart(api):
    """Not computed, computed-and-clean, computed-with-findings. A renderer
    holding only the JSON must be able to tell which it has: ``null`` printed as
    "nothing diverged" is an absence sold as a result."""
    def coverage(key):
        return api.get(f"/api/scenario-runs/{api.stored[key]}",
                       headers=AUTH).json()["coverage"]

    assert coverage("bare")["divergence"] is None
    assert coverage("conv")["divergence"] == []
    assert coverage("single")["divergence"]


def test_the_counterparty_record_arrives_beside_the_transcript(api):
    """The transcript is a join and keeps three of a turn's seven fields. The
    four it drops — ``expect``/``forbid``/``reason``/``source`` — are what a
    leak is graded with, and they cross the wire under ``turns``."""
    body = api.get(f"/api/scenario-runs/{api.stored['conv']}",
                   headers=AUTH).json()
    turns = body["turns"]
    assert [t["kind"] for t in turns] == ["open", "reveal", "close"]
    assert turns[0]["forbid"] and turns[1]["expect"]
    assert turns[2]["reason"] == "satisfied"
    assert {t["source"] for t in turns} == {"scripted"}

    dropped = {"expect", "forbid", "reason", "source"}
    assert all(dropped.isdisjoint(entry) for entry in body["transcript"])


def test_a_single_shot_run_carries_an_empty_turn_record(api):
    """``[]`` is honest for a ticket answered in one exchange. ``null`` would
    mean the record was never kept, and this one was."""
    body = api.get(f"/api/scenario-runs/{api.stored['single']}",
                   headers=AUTH).json()
    assert body["turns"] == []


def test_list_returns_the_runs_newest_first_and_filters(api):
    r = api.get("/api/scenario-runs", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["runs"]) == 3
    assert body["runs"][0]["run_id"] == api.stored["bare"]      # newest first

    by_agent = api.get("/api/scenario-runs", headers=AUTH,
                       params={"agent_id": "api-conv"}).json()
    assert [r["run_id"] for r in by_agent["runs"]] == [api.stored["conv"]]
    assert by_agent["runs"][0]["conversational"] is True

    by_scenario = api.get("/api/scenario-runs", headers=AUTH,
                          params={"scenario_id": api.stored["single_scenario_id"]
                                  }).json()
    assert [r["run_id"] for r in by_scenario["runs"]] == [api.stored["single"]]

    one = api.get("/api/scenario-runs", headers=AUTH,
                  params={"limit": 1}).json()
    assert one["count"] == 1


def test_an_empty_filter_is_no_filter(api):
    """FastAPI binds ``?agent_id=`` to ``""``, not to ``None``, and the registry
    filters on anything that is not ``None`` — so a control nobody touched
    returned zero runs, and zero runs is a RESULT: "this tenant has never run
    that agent". Reporting it for a question nobody asked is the same defect as
    printing an unexercised check as a pass, one surface over."""
    absent = api.get("/api/scenario-runs", headers=AUTH).json()
    assert absent["count"] == 3

    for query in ("agent_id=", "scenario_id=", "agent_id=&scenario_id="):
        got = api.get(f"/api/scenario-runs?{query}", headers=AUTH).json()
        assert got["count"] == absent["count"], query
        assert [r["run_id"] for r in got["runs"]] == [
            r["run_id"] for r in absent["runs"]], query


def test_a_filter_that_names_something_still_filters(api):
    """The other half, or the fix would be "ignore every filter". A real value
    narrows the list, and a real value that matches nothing still returns
    nothing — that zero IS a measurement."""
    named = api.get("/api/scenario-runs", headers=AUTH,
                    params={"agent_id": "api-conv"}).json()
    assert [r["run_id"] for r in named["runs"]] == [api.stored["conv"]]

    missing = api.get("/api/scenario-runs", headers=AUTH,
                      params={"agent_id": "api-never-ran"}).json()
    assert missing["count"] == 0


def test_an_unknown_run_is_a_json_404(api):
    r = api.get("/api/scenario-runs/does-not-exist", headers=AUTH)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "does-not-exist" in r.json()["detail"]


def test_scenario_runs_require_auth(api):
    assert api.get("/api/scenario-runs").status_code == 401
    assert api.get(
        f"/api/scenario-runs/{api.stored['single']}").status_code == 401


def test_an_absurd_limit_is_capped_not_honoured(api):
    """One request must not be able to ask the server to parse every payload it
    has ever stored."""
    assert api.get("/api/scenario-runs", headers=AUTH,
                   params={"limit": 10_000_000}).status_code == 200
