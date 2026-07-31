"""Stored scenario runs — list + detail.

A scenario run is what ``scenario/runner.py`` produces: one realized scenario
driven against one agent through the enforcement gateway, in a world that can be
made to fail on cue. Until ``Registry.save_scenario_run`` existed, the trace was
persisted and everything around it — the transcript, the fault report, the state
diff, the calls the gateway refused — was thrown away when the process exited, so
there was nothing for a surface to render. These two endpoints are the read side
of that store.

**Everything served here is stored evidence or is recomputed from it.** The
registry re-derives ``never_reached``, the fault counts, the elicitation verdict
and the scenario's content hash on every read (see
``registry/sqlite_store.py:_scenario_run_from_payload``); this module reshapes
nothing and computes nothing of its own. There is no place here for a number to
be invented, which is the point.

Five absences a renderer must handle rather than paper over, all of them
deliberate. Each one is a ``null`` whose ``[]`` means the opposite thing:

* ``faults.recorded == False`` — this run stored no fault report, and its
  ``planned`` / ``fired`` / ``skipped`` / ``never_reached`` are ``null``, not
  ``[]``. "Nobody wrote down what was staged" is not "nothing was staged".
* ``coverage.measured == False`` — no coverage was collected for this run, and
  ``coverage.bins`` is ``null``. A run whose bins were collected and came back
  empty reads ``measured: true, bins: []``, and the two must not look alike.
* ``coverage.divergence == null`` — nobody computed, for this run, which corners
  the point requested and the trace never produced. ``[]`` is the OPPOSITE
  claim: it was computed, and everything asked for appeared. ``measured`` does
  not speak for this field; only ``null`` vs ``[]`` does.
* ``turns == null`` — this row does not carry the counterparty's own record of
  the conversation (it was written before that was stored). ``[]`` means the
  counterparty took no turns, which is what a single-shot ticket honestly has.
* ``derived.n_user_turns == null`` — the turn count is taken off the trace and
  the trace could not be read. Zero would be a measurement.

Tenant-scoped through ``request.state.reg`` and mounted behind the same auth as
every other protected router.

The contract
------------

``GET /api/scenario-runs`` — query ``scenario_id``, ``agent_id`` (exact match,
both optional; an EMPTY value is no filter, not a filter matching nothing),
``limit`` (default 50, capped at 500)::

    {"count": int,
     "runs": [{"run_id": str,            # == trace_id unless the caller set one
               "scenario_id": str, "agent_id": str, "trace_id": str,
               "space_ref": str, "space_fingerprint": str, "seed": int,
               "created_at": str,        # ISO-8601, UTC (no offset on SQLite)
               "ended": str,             # "" for a single-shot run
               "conversational": bool, "world_changed": bool, "n_blocked": int,
               "faults": {"recorded": bool,
                          "counts": {"planned": int, "fired": int,
                                     "skipped": int, "never_reached": int}
                                    | null}}]}

``GET /api/scenario-runs/{run_id}`` — 404 with ``{"detail": str}`` for an
unknown id, otherwise::

    {"run_id": str, "scenario_id": str, "agent_id": str, "trace_id": str,
     "space_ref": str, "space_fingerprint": str, "seed": int,
     "created_at": str,
     "point": {str: str},              # the abstract point the solver drew
     "ticket": str,                    # the realized ticket text
     "session_id": str,                # "" for a single-shot run
     "ended": str,                     # "satisfied"|"gave_up"|"turn_cap"|…|""
     "turns": [{...}] | null,          # the counterparty's record — see below
     "transcript": [...],              # [] for a single-shot run — see below
     "state_diff": {str: {"before": any, "after": any}},   # {} = world unmoved
     "blocked": [str],                 # tool names the gateway refused
     "interactions": [{...}],          # escalations / confirmations
     "faults": {...},                  # see below
     "elicitation": {"disclosed": [str], "withheld": [str]},
     "coverage": {"measured": bool, "bins": [str] | null,
                  "divergence": [{...}] | null},           # see below
     "user_provenance": {...},         # which simulator stood in for a human
     "disclosures": [{...}],           # dicts; by convention "kind" + "note"
     "derived": {"conversational": bool,
                 "n_user_turns": int | null,
                 "world_changed": bool, "n_changed_fields": int,
                 "n_blocked": int,
                 "elicitation_complete": bool | null,   # null = single-shot
                 "content_sha256": str}}

A ``transcript`` entry is one of two shapes, and the difference is deliberate —
``delivered`` and ``revealed_fact`` are facts about a counterparty turn and mean
nothing for a reply::

    {"speaker": "user", "text": str, "kind": str, "discloses": str,
     "revealed_fact": bool, "delivered": bool}
    {"speaker": "agent", "text": str}

``kind`` is the counterparty's turn kind (``open``/``reveal``/``pushback``/
``reply``/``close``); ``discloses`` is the ``hidden_facts`` key that turn handed
over, ``""`` when it handed over nothing; ``delivered`` is false exactly for the
closing turn, which the agent was never given.

``turns`` is that same conversation as the COUNTERPARTY recorded it —
``UserTurn.as_dict()`` verbatim, ``{"kind", "text", "expect", "forbid",
"discloses", "reason", "source"}`` — and it is not a duplicate of the
transcript. The transcript is a join that keeps ``text``/``kind``/``discloses``
and drops the rest: ``expect`` and ``forbid`` are the whole input to
``UserTurn.grade``, which is how "the agent stated a value it was never told" is
detected; ``reason`` says why a close happened (only the last one reaches
``ended``, and a ``turn_cap`` run has no close at all); ``source`` is per-turn
(``scripted``/``llm``/``replayed-verbatim``) where ``user_provenance`` is
per-session. Render the transcript; audit against ``turns``.

``coverage.divergence`` rows are ``CoverageReport.divergence()``'s own dicts,
``{"coverpoint_id": str, "bin_id": str, "requested": int, "exhibited": 0}`` —
the corners the point asked for that the run never produced. The vocabulary for
them is fixed: **asked for, never exhibited**. They are a fact about the
GENERATOR's reach, not about the agent's behaviour, and must never be summed
into a coverage percentage.

``faults`` is either a recorded report or the absence of one::

    {"recorded": true, "source": str,
     "planned": [f], "fired": [f], "skipped": [f], "never_reached": [f],
     "counts": {"planned": int, "fired": int, "skipped": int,
                "never_reached": int}}
    {"recorded": false, "source": null, "planned": null, "fired": null,
     "skipped": null, "never_reached": null, "counts": null}

where ``f`` is ``{"tool": str, "call_index": int, "kind": str, "once": bool}``
plus ``"truncate_pct": int`` on a ``malformed_response``, plus ``"step": int``
once it is an event, plus ``"observable": bool`` on a fired fault and
``"reason": str`` on a skipped one. ``source`` is where the plan came from:
``scenario_plan`` | ``requested_tool_condition`` | ``explicit`` | ``none``.

The four fault lists are four different facts and a renderer must not merge
them: ``fired`` happened, ``skipped`` reached its call and could not happen (with
the reason), ``never_reached`` was staged on a call the agent never made, and
``planned`` is all of them together. Only ``fired`` is a thing that happened to
the run.

``never_reached`` and ``counts`` are RECOMPUTED on every read from
planned/fired/skipped, as are ``derived.*`` — the registry stores evidence and
derives summaries, so a stored summary can never outrank the evidence it came
from. If a stored report cannot be reconstructed (it names a tool the world does
not have, say), ``never_reached`` and ``counts`` come back ``null`` and a
``"problem": str`` key appears alongside the stored lists.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from agenttic.registry.sqlite_store import NotFoundError

router = APIRouter(tags=["scenarios"])


def _filter(value: str | None) -> str | None:
    """An empty filter is NO filter.

    FastAPI binds a bare ``?agent_id=`` to ``""``, not to ``None``, and the
    registry's contract is exact-match-on-anything-that-is-not-``None`` — so the
    empty string arrived as a real filter and the endpoint answered "no runs" for
    a query that named no agent. Zero rows is a RESULT ("this tenant has never
    run that agent"), and returning it for a question nobody asked is an absence
    dressed as a measurement, which is the one thing this surface exists not to
    do.

    Nothing is stored under an empty id — ``save_scenario_run`` reads
    ``agent_id`` off the trace and ``scenario_id`` off the realized scenario —
    so no answerable query is lost by treating ``""`` as unset. Over HTTP the two
    are not distinguishable anyway: a form that submits an untouched field and a
    caller who omitted it send the same bytes.
    """
    return value or None


@router.get("/scenario-runs")
def list_scenario_runs(request: Request, scenario_id: str | None = None,
                       agent_id: str | None = None, limit: int = 50):
    """This tenant's stored scenario runs, newest first.

    Newest-first because a run list is read to answer "what just happened",
    where the append-only artifact lists in this registry (suites, dossiers) are
    read as histories. ``limit`` is capped at 500 so one request cannot ask the
    server to parse every payload it has ever stored.

    ``?agent_id=`` and an absent ``agent_id`` are the same request — see
    :func:`_filter`. A filter that names a real value still filters.
    """
    runs = request.state.reg.list_scenario_runs(
        scenario_id=_filter(scenario_id), agent_id=_filter(agent_id),
        limit=max(1, min(int(limit), 500)))
    return {"runs": runs, "count": len(runs)}


@router.get("/scenario-runs/{run_id}")
def get_scenario_run(run_id: str, request: Request):
    """One stored run in full: the ticket that was sent, the transcript if it was
    a conversation, what the world staged and what actually fired, what changed
    in the store, and what the gateway refused."""
    try:
        return request.state.reg.get_scenario_run(run_id)
    except NotFoundError:
        raise HTTPException(404, f"scenario run {run_id} not found")
