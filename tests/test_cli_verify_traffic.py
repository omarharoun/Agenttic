"""`agenttic ingest verify-traffic` printed a table and then died in the middle of it.

The flagship free, deterministic, zero-model-call command for auditing production
traffic formatted every coverpoint's closure with ``:>6.0%``. A coverpoint nothing
in the system can feed carries ``closure: None`` — and ``f"{None:>6.0%}"`` raises
``TypeError``. So the command printed trajectory, tool_condition and agent_steps,
reached ``session_shape``, and aborted with exit 1. The operator never saw
data_condition, action_risk, the instrumentation-fidelity block, or the list of
uninstrumented tools that tells them how to make action_risk real.

2427 backend tests passed over that. Nothing invoked the command: the coverage
tests stop at the report object and ``tests/test_traffic_closure.py`` stops at the
summary dict, so the one line between the dict and the operator was untested. The
lesson generalises — a three-state value is only safe once every renderer of it
has been executed against all three states — so these tests drive the real typer
command and read its real output.

Asserting ``exit_code == 0`` alone would pass on a command that printed nothing,
which is why every test here also names something that appears AFTER the row that
used to kill it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from agenttic.cli import app
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 7, 26, 12, 0, 0)


def _sp(i: int, kind: str, name: str, *, attrs=None, out=None) -> Span:
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input={}, output=out or {}, attributes=attrs or {})


def _instrumented(i: int) -> Trace:
    """A trace whose tool span carries real mutation semantics."""
    return Trace(
        trace_id=f"t{i}", agent_id="prod-bot", agent_config_hash="cfg-1",
        test_case_id=None, visibility="glass_box",
        spans=[_sp(0, "llm_call", "plan"),
               _sp(1, "tool_call", "issue_refund",
                   attrs={"mutating": True, "irreversible": True,
                          "entity_id": "o1"}),
               _sp(2, "final_output", "reply", out={"text": "done"})],
        final_output="ok", total_steps=3, source="otel_ingest")


def _opaque(i: int) -> Trace:
    """A tool nobody instrumented: not evidence of a read-only agent."""
    return Trace(
        trace_id=f"o{i}", agent_id="prod-bot", agent_config_hash="cfg-1",
        test_case_id=None, visibility="glass_box",
        spans=[_sp(0, "llm_call", "plan"),
               _sp(1, "tool_call", "process_request"),
               _sp(2, "final_output", "reply", out={"text": "done"})],
        final_output="ok", total_steps=3, source="otel_ingest")


@pytest.fixture()
def traffic(tmp_path):
    """A config plus a registry holding live (ingested) traffic to verify."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l, generator: g}\n"
        "harness: {timeout_seconds: 1, max_steps: 1, max_parallel: 1, "
        "transport_retries: 0}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'p.db'}, review_dir: r/, "
        "calibration_dir: c/}\n", encoding="utf-8")
    reg = Registry(tmp_path / "p.db")
    for i in range(3):
        reg.save_trace(_instrumented(i), mode="live")
    reg.save_trace(_opaque(9), mode="live")
    return cfg


def _run(cfg, *extra):
    return CliRunner().invoke(
        app, ["ingest", "verify-traffic", "--agent", "prod-bot",
              "--config", str(cfg), *extra])


class TestTheCommandRunsToTheEnd:
    def test_it_exits_zero_on_traffic_that_includes_a_not_measurable_dimension(
            self, traffic):
        """The regression itself: the baseline model always carries
        `session_shape` as not measurable, so EVERY invocation of this command hit
        the crash. There is no traffic that avoided it."""
        r = _run(traffic)
        assert r.exit_code == 0, r.output

    def test_it_gets_past_the_row_that_used_to_kill_it(self, traffic):
        """Exit 0 alone would pass on a command that printed nothing. These are
        the sections that come AFTER session_shape in the print order."""
        out = _run(traffic).output
        assert "session_shape" in out           # the not-measurable row printed
        assert "data_condition" in out          # ...and the table continued
        assert "action_risk" in out
        assert "instrumentation fidelity" in out
        assert "process_request" in out         # the uninstrumented-tools list

    def test_it_still_leads_with_the_scoped_closure_figure(self, traffic):
        """The command's whole point is a closure number with a stated
        population; the fix must not have cost that."""
        out = _run(traffic).output
        assert "production trace(s)" in out
        assert "not over an authored suite" in out
        assert "target" in out


class TestItSaysNotMeasurableRatherThanZero:
    def test_the_not_measurable_row_reads_in_words(self, traffic):
        out = _run(traffic).output
        assert "not measurable" in out

    def test_it_never_prints_a_zero_percent_for_that_row(self, traffic):
        """`0%` would read as "the suite never got there" — a gap a generator can
        be told to close. Nothing can close this one."""
        line = next(ln for ln in _run(traffic).output.splitlines()
                    if "session_shape" in ln and "excluded" not in ln)
        assert "0%" not in line

    def test_the_reason_travels_with_it(self, traffic):
        """Hard Rule 61: the disclosure is the reason, not the label."""
        out = _run(traffic).output
        assert "user_turn" in out               # from not_measurable_reason

    def test_an_uninstrumented_tool_name_is_printed_whole(self, tmp_path):
        """The uninstrumented-tools list is the operator's to-do list, and the
        names in it come from someone else's spans. Rich eats anything in square
        brackets as a style tag, so `fetch[v2]` printed as `fetch` — a tool they
        cannot find to instrument, and a disclosure missing a piece of itself."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "models: {agent_default: a, judge_strong: j, judge_light: l, "
            "generator: g}\n"
            "harness: {timeout_seconds: 1, max_steps: 1, max_parallel: 1, "
            "transport_retries: 0}\n"
            "scoring: {calibration_threshold: 0.8}\n"
            "live: {sample_rate: 0.05, drift_threshold: 0.15, "
            "drift_window_runs: 50}\n"
            f"paths: {{registry_db: {tmp_path / 'q.db'}, review_dir: r/, "
            "calibration_dir: c/}\n", encoding="utf-8")
        odd = Trace(
            trace_id="b1", agent_id="prod-bot", agent_config_hash="cfg-1",
            test_case_id=None, visibility="glass_box",
            spans=[_sp(0, "llm_call", "plan"),
                   _sp(1, "tool_call", "fetch[v2]"),
                   _sp(2, "final_output", "reply", out={"text": "done"})],
            final_output="ok", total_steps=3, source="otel_ingest")
        Registry(tmp_path / "q.db").save_trace(odd, mode="live")
        out = _run(cfg).output
        assert "fetch[v2]" in out

    def test_the_waived_bins_are_named_with_their_reasons(self, traffic):
        """The bins that left the denominator are stated in the deliverable, so a
        `missing:` list shorter than the model's bin count has a cause a reader
        can see."""
        out = _run(traffic).output
        assert "excluded from closure" in out
        assert "session_shape.single_turn" in out
        assert "resumed_with_memory" in out


class TestTheSiblingCommandOnTheSameSummary:
    """`agenttic hook verify` reads the identical ``verify_traffic`` dict.

    It survives today only because it prints the headline closure (always a
    float) and never the per-coverpoint table. That is one line away from the
    same crash, and like its sibling it had no test at all — so it gets one here,
    on the same three-state data, before someone adds the table.
    """

    def _spool(self, tmp_path):
        from agenttic.hooks.claude_code import record
        spool = tmp_path / "spool.jsonl"
        for cmd in ("rm -rf build", "ls -la"):
            assert record({"tool_name": "Bash", "session_id": "s1",
                           "tool_input": {"command": cmd}}, path=spool)
        record({"tool_name": "Read", "session_id": "s1",
                "tool_input": {"file_path": "/etc/hosts"}}, path=spool)
        return spool

    def test_it_runs_to_the_end_on_captured_tool_calls(self, tmp_path):
        r = CliRunner().invoke(app, ["hook", "verify", "--spool",
                                     str(self._spool(tmp_path))])
        assert r.exit_code == 0, r.output
        assert "closure" in r.output
        assert "action_risk trustable" in r.output   # the last block it prints

    def test_an_unclassifiable_tool_name_is_printed_whole(self, tmp_path):
        """Same list, same hazard as `ingest verify-traffic`: a captured tool
        called `MyTool[beta]` would print as `MyTool`, naming nothing."""
        from agenttic.hooks.claude_code import record
        spool = tmp_path / "spool.jsonl"
        assert record({"tool_name": "MyTool[beta]", "session_id": "s2",
                       "tool_input": {"x": 1}}, path=spool)
        r = CliRunner().invoke(app, ["hook", "verify", "--spool", str(spool)])
        assert r.exit_code == 0, r.output
        assert "MyTool[beta]" in r.output

    def test_it_states_what_the_closure_is_measured_over(self, tmp_path):
        """A closure figure with no stated population is the unscoped claim."""
        r = CliRunner().invoke(app, ["hook", "verify", "--spool",
                                     str(self._spool(tmp_path))])
        assert "session(s)" in r.output and "tool call(s)" in r.output

    def test_it_says_a_dimension_is_outside_that_denominator(self, tmp_path):
        """`closure 12.4% of 95%` with nothing beside it reads as a fraction of
        the whole model. It is a fraction of the measurable part of it — the
        baseline model always carries `session_shape` as not measurable, so this
        is true of every invocation, not an edge case.

        Under-disclosure rather than a wrong number, which is why it outlived the
        round that removed the over-reports: no figure moved. The sibling command
        above prints the same fact off the same dict, and a product whose claim is
        an honest account of what a run never exercised cannot have one command
        state it and the other stay silent."""
        r = CliRunner().invoke(app, ["hook", "verify", "--spool",
                                     str(self._spool(tmp_path))])
        assert r.exit_code == 0, r.output
        assert "session_shape" in r.output
        assert "not measurable" in r.output
        assert "user_turn" in r.output          # the reason, not just the label

    def test_that_row_is_never_printed_as_a_percentage(self, tmp_path):
        """Neither `0%` (a measurement — a gap someone could be told to close)
        nor a number of any kind. Guards the whole line rather than the substring,
        so a future `100%` cannot slip past a `0%` assertion."""
        r = CliRunner().invoke(app, ["hook", "verify", "--spool",
                                     str(self._spool(tmp_path))])
        line = next(ln for ln in r.output.splitlines() if "session_shape" in ln)
        assert "%" not in line, line

    def test_the_closure_headline_survives_it(self, tmp_path):
        """The disclosure is added to the closure line, so it is exactly where a
        formatting mistake would take the headline down with it."""
        r = CliRunner().invoke(app, ["hook", "verify", "--spool",
                                     str(self._spool(tmp_path))])
        assert "closure" in r.output
        assert "action_risk trustable" in r.output   # ...and the rest still ran


class TestTheTwoRenderersAgree:
    """One rule, two output languages.

    ``cli._closure_cell`` (console markup) and
    ``reporting.scorecard_report._closure_cell`` (Markdown) exist separately
    because their output languages differ, not because the rule does. Nothing but
    a test stops them drifting into two different answers about the same
    coverpoint, and the drift would be invisible: each surface would look
    self-consistent.
    """

    CASES = [
        ({"not_measurable": True, "not_measurable_reason": "no producer",
          "closure": None}, "not measurable"),
        ({"not_measurable": False, "closure": None}, "not measured"),
        ({"not_measurable": False, "closure": 0.25}, "25%"),
        ({"not_measurable": False, "closure": 0.0}, "0%"),
    ]

    @pytest.mark.parametrize("cp,expected", CASES)
    def test_the_console_renderer_picks_the_right_state(self, cp, expected):
        from agenttic.cli import _closure_cell
        assert expected in _closure_cell(cp)

    @pytest.mark.parametrize("cp,expected", CASES)
    def test_the_markdown_renderer_picks_the_same_one(self, cp, expected):
        from agenttic.reporting.scorecard_report import _closure_cell
        assert expected in _closure_cell(cp)

    def test_a_measured_zero_is_still_printed_as_zero(self):
        """The correction is not "never print 0%". A coverpoint that WAS measured
        and exhibited nothing is a real finding and must keep saying so."""
        from agenttic.cli import _closure_cell
        assert _closure_cell({"closure": 0.0}) .strip() == "0%"
