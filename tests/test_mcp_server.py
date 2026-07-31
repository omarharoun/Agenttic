"""The MCP tool named `what_is_untested` was omitting the one dimension nothing
can measure.

Over-reporting coverage is the worst defect this product can ship, and omission is
the quietest form of it: no crash, no `0%`, no warning. ``_what_is_untested``
projected ``per_coverpoint`` through ``if d.get("unhit")``, and a NOT-MEASURABLE
coverpoint reports ``unhit: []`` — for the opposite reason a closed one does. So
the tool returned a closure figure plus a list that silently dropped
``session_shape``, and a caller reasonably read that list as the complete answer
to the question the tool's own name asks.

It is worse over MCP than on any other surface. A human reading the console sees a
per-coverpoint table and can notice a missing row; the scorecard prints an
"excluded from closure" block. An LLM consuming this JSON has neither. It gets one
dict, no cross-check, and it will act on the list as complete.

These tests drive the real JSON-RPC entry point — ``handle()`` with a
``tools/call`` frame, result parsed back out of the MCP text content — rather than
calling ``_what_is_untested`` directly. The defect was a projection at the surface,
and a test that stops at the helper is a test that never sees the surface. The same
lesson as ``tests/test_cli_verify_traffic.py``: a three-state value is only safe
once every renderer of it has been executed.
"""

from __future__ import annotations

import json

import pytest

from agenttic.mcp_server import TOOLS, handle

#: The dimension nothing in the build can feed. Carried by the baseline coverage
#: model on EVERY run, so there is no captured session that avoids this case —
#: which is exactly why the omission was total rather than occasional.
NOT_MEASURABLE_CP = "session_shape"


# --------------------------------------------------------------------------- #
# a real captured session, through the real spool
# --------------------------------------------------------------------------- #

@pytest.fixture()
def session(tmp_path, monkeypatch):
    """A hook spool holding a real mix of tool calls, wired in via the env var.

    ``_load_traces`` calls ``load_spool()`` with no argument, so the spool env
    override is the only seam — and using it means these tests exercise the same
    path a live MCP client does, spans and all, rather than a hand-built trace.
    """
    from agenttic.hooks.claude_code import SPOOL_ENV, record

    spool = tmp_path / "hook-spans.jsonl"
    monkeypatch.setenv(SPOOL_ENV, str(spool))
    for cmd in ("rm -rf build", "ls -la", "git push --force"):
        assert record({"tool_name": "Bash", "session_id": "s1",
                       "tool_input": {"command": cmd}})
    assert record({"tool_name": "Read", "session_id": "s1",
                   "tool_input": {"file_path": "/etc/hosts"}})
    # a tool nobody instrumented: not evidence of a read-only agent
    assert record({"tool_name": "process_request", "session_id": "s1",
                   "tool_input": {"x": 1}})
    return spool


def call(name: str, **arguments) -> dict:
    """Invoke a tool the way an MCP client does and return the decoded result.

    Asserts the frame shape on the way through: a tool that answered by raising
    would come back as ``isError`` with a text blob, and a test that only looked
    at the payload would report that as "the key is missing" instead of "the tool
    fell over".
    """
    frame = handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
    assert frame is not None and "result" in frame, frame
    result = frame["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


# --------------------------------------------------------------------------- #

class TestWhatIsUntestedNamesWhatCannotBeMeasured:
    """The finding itself."""

    def test_the_not_measurable_dimension_is_in_the_answer(self, session):
        """It was in no key of the returned dict at all — neither as a gap nor as
        a declaration. The tool's name promises this dimension specifically."""
        out = call("what_is_untested")
        assert NOT_MEASURABLE_CP in out["not_measurable"]

    def test_the_reason_travels_with_it(self, session):
        """Hard Rule 61: the disclosure is the reason, not the label. A bare id
        tells a caller a name; the reason tells it that instrumenting a
        `user_turn` span is the fix, and that no number of runs is."""
        reason = call("what_is_untested")["not_measurable"][NOT_MEASURABLE_CP]
        assert "user_turn" in reason
        assert len(reason) > 40, reason

    def test_it_is_not_smuggled_into_the_go_exercise_list(self, session):
        """`unhit_situations` is a to-do list. Putting a dimension no run can
        reach on it would aim the operator (and the CDV solver) at it forever —
        the mirror-image over-correction, and just as wrong."""
        out = call("what_is_untested")
        assert NOT_MEASURABLE_CP not in out["unhit_situations"]

    def test_the_two_lists_are_disjoint(self, session):
        """One coverpoint is in exactly one of the two answers. Appearing in both
        would let a reader double-count the space, or close a gap that is not
        one."""
        out = call("what_is_untested")
        assert not (set(out["unhit_situations"]) & set(out["not_measurable"]))

    def test_the_measurable_gaps_are_still_reported(self, session):
        """The fix must not have cost the tool its original job."""
        out = call("what_is_untested")
        assert "action_risk" in out["unhit_situations"]
        assert "read_only" in out["unhit_situations"]["action_risk"]

    def test_the_note_says_exercising_will_never_close_it(self, session):
        """The two lists need different actions, and only the note says so. A
        caller told "exercise these situations" over a not-measurable dimension
        will burn runs against a wall."""
        note = call("what_is_untested")["note"]
        assert "not_measurable" in note
        assert "instrumentation" in note


class TestClosureSaysWhatItIsAFractionOf:
    """A closure figure beside a dimension outside its denominator."""

    def test_verify_session_discloses_it_next_to_the_number(self, session):
        """`closure: 0.12 of 0.95` reads as a fraction of the whole model. It is
        a fraction of the measurable part of it, and this is the only key that
        says so."""
        out = call("verify_session")
        assert NOT_MEASURABLE_CP in out["not_measurable"]

    def test_both_tools_use_the_same_word_for_it(self, session):
        """One vocabulary across every surface. `ops.verify_op`, the scorecard and
        `agenttic ingest verify-traffic` all call this `not_measurable` with an
        id -> reason shape; a caller who learned the word on one surface must not
        need a second word here."""
        a = call("verify_session")["not_measurable"]
        b = call("what_is_untested")["not_measurable"]
        assert a == b

    def test_it_matches_what_the_verification_layer_actually_reported(
            self, session):
        """Pinned to the source of truth rather than to the literal
        `session_shape`: if a future model declares a second dimension
        unmeasurable, this tool must carry that one too — a hard-coded expectation
        would pass while the new omission shipped."""
        from agenttic.mcp_server import _load_traces
        from agenttic.verification.traffic import verify_traffic

        traces, _n = _load_traces("")
        expected = verify_traffic(traces)["not_measurable"]
        assert expected, "the fixture must exercise the not-measurable case"
        assert call("what_is_untested")["not_measurable"] == expected


class TestTheSweepOfTheSameDefectElsewhere:
    """`not_measurable` was the named finding. Two more fields carrying a
    disclosure were dropped by the same projections, found by walking every key
    of the verification summary against every key these tools return."""

    def test_a_bin_the_hook_cannot_credit_is_not_sold_as_a_gap(self, session):
        """`agent_steps` drifts at 1.0 on this path: the hook emits no `llm_call`
        spans, so every session's step count lands in `other` and is unobservable.
        `unhit_situations` nonetheless lists `single_step` and `multi_step`. With
        no `other_drift` the tool sends the caller to exercise two bins its own
        instrumentation can never credit — and the caller reads the failure to
        close them as the agent's, not the instrumentation's."""
        out = call("what_is_untested")
        assert out["other_drift"].get("agent_steps") == 1.0
        assert "agent_steps" in out["unhit_situations"]   # the pairing is the point

    def test_drift_is_reported_beside_the_gaps_not_inside_them(self, session):
        """A drifting coverpoint was REACHED. Folding it into `unhit_situations`
        would claim the opposite, and folding it into `not_measurable` would claim
        the dimension has no producer when it has one the model cannot read."""
        out = call("what_is_untested")
        assert not (set(out["other_drift"]) & set(out["not_measurable"]))

    def test_both_tools_say_which_model_produced_the_figure(self, session):
        """`BASELINE_LIMITS` is written to be "the only copy that travels with the
        number" so a baseline closure is never read as a fitted one. These
        projections were the surface it never reached: `0.12 of 0.95` over MCP
        looked like a verdict on intent and policy pressure, which this model does
        not examine at all."""
        for tool in ("verify_session", "what_is_untested"):
            out = call(tool)
            assert "baseline" in (out["model_ref"] or "").lower(), tool
            assert "intent" in out["limits"], tool

    def test_the_limits_text_is_the_repos_one_copy_not_a_paraphrase(self, session):
        """Restating it here would be a second implementation of a disclosure —
        the kind that drifts silently because each surface stays self-consistent."""
        from agenttic.coverage.models.baseline import BASELINE_LIMITS
        assert call("verify_session")["limits"] == BASELINE_LIMITS

    def test_no_waived_bin_leaves_the_denominator_undisclosed(self, session):
        """`unhit_situations` lists a measurable coverpoint's UNHIT bins. A bin
        waived individually on a measurable coverpoint would be in neither that
        list nor `not_measurable`: out of the denominator with nothing here saying
        so — Hard Rule 61's silent hole, in the one place a caller cannot
        cross-check.

        Vacuously true today, and that is exactly the point of writing it as an
        invariant over the report rather than as a snapshot: every waived bin in
        the baseline model belongs to `session_shape`, which `not_measurable`
        already discloses. It fires the day a model waives a bin on a dimension it
        CAN measure, which is the day the omission comes back in a shape no other
        test covers."""
        from agenttic.mcp_server import _load_traces
        from agenttic.verification.traffic import verify_traffic

        traces, _n = _load_traces("")
        waived = verify_traffic(traces).get("waived_bins") or {}
        assert waived, "the fixture must exercise at least one waived bin"
        out = call("what_is_untested")
        undisclosed = {b for b in waived
                       if b.split(".")[0] not in out["not_measurable"]}
        assert not undisclosed, undisclosed

    def test_what_is_untested_carries_the_warning_that_qualifies_its_gap_list(
            self, session):
        """An unhit `action_risk` bin has two possible causes: nothing exercised
        it, or the tools that did carry no risk class and could not be credited.
        The warning is the only thing that tells them apart, and this tool — the
        one whose whole output is that gap list — was the one dropping it."""
        out = call("what_is_untested")
        assert "action_risk" in out["unhit_situations"]
        assert any("no usable risk class" in w for w in out["warnings"]), out


class TestTheKeyIsAlwaysThere:
    """A field that appears only in the interesting case is a field consumers
    forget to handle — the stance ``server/routes/capabilities.py`` already takes
    about this same projection."""

    def test_no_captured_calls_yields_no_figure_to_qualify(self, tmp_path,
                                                           monkeypatch):
        """With no captured calls the tool returns `status: no_data` and no
        closure figure. There is no number to qualify, so an empty
        `not_measurable` would be an answer about a model nothing was run against
        — the status is the honest reply."""
        from agenttic.hooks.claude_code import SPOOL_ENV
        monkeypatch.setenv(SPOOL_ENV, str(tmp_path / "empty.jsonl"))
        out = call("what_is_untested")
        assert out["status"] == "no_data"
        assert "closure" not in out

    def test_a_populated_answer_always_carries_the_key(self, session):
        for tool in ("verify_session", "what_is_untested"):
            out = call(tool)
            assert out["status"] == "populated"
            assert isinstance(out["not_measurable"], dict), tool

    def test_a_populated_answer_always_carries_the_swept_keys_too(self, session):
        assert isinstance(call("what_is_untested")["other_drift"], dict)
        for tool in ("verify_session", "what_is_untested"):
            assert call(tool)["limits"], tool


class TestTheToolDescriptionsPromiseIt:
    """The description is what an LLM reads BEFORE calling, and it is the only
    part of the contract a client sees when deciding whether one call answered
    the question. A tool that returns the disclosure but advertises a single list
    still invites the caller to stop reading at `unhit_situations`."""

    def _described(self, name: str) -> str:
        return next(t["description"] for t in TOOLS if t["name"] == name)

    def test_what_is_untested_advertises_every_part_of_its_answer(self):
        """A description that undercounts the answer is the same defect one layer
        out: it invites the caller to stop reading at `unhit_situations`."""
        d = self._described("what_is_untested")
        for key in ("unhit_situations", "not_measurable", "other_drift", "limits"):
            assert key in d, key

    def test_the_advertised_parts_are_the_parts_it_returns(self, session):
        """Pins the prose to the payload. The two drift apart silently — each
        reads fine on its own — and the description is the half a client sees
        before deciding whether one call answered the question."""
        d = self._described("what_is_untested")
        returned = set(call("what_is_untested"))
        for key in ("unhit_situations", "not_measurable", "other_drift", "limits"):
            assert key in returned and key in d, key

    def test_verify_session_says_what_closure_is_a_fraction_of(self):
        assert "not_measurable" in self._described("verify_session")

    def test_every_advertised_tool_is_dispatchable(self, session):
        """A described tool that `tools/call` rejects is a contract the server
        does not keep. Takes the fixture so the spool env is pinned: without it
        these calls would read the developer's own ``~/.agenttic`` spool."""
        listed = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        for tool in listed["result"]["tools"]:
            frame = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": tool["name"], "arguments": {}}})
            assert "error" not in frame, tool["name"]
