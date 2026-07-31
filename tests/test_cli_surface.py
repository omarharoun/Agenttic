"""The operator path to a per-agent test space.

P6 built the derivation — descriptor in, `ScenarioSpace` out — and nothing called
it. `grep derive_space src/` returned hits only inside `stimulus/derive.py` and
its own tests, while `agenttic cdv` went on running the hand-written
`conversational_transactional` space for every agent. A capability reachable from
no shipped path is the exact defect this whole rescue exists to remove, so these
tests pin the wiring rather than the derivation: the commands exist, they are
honest about what they cannot test, and `cdv --surface` really does run the
derived space rather than the generic one.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agenttic.cli import app
from tests.conftest import plain

runner = CliRunner()


def _run(*args):
    return runner.invoke(app, list(args))


class TestTheCommandsExist:
    def test_list_names_every_describable_surface(self):
        r = _run("surface", "list")
        assert r.exit_code == 0, r.output
        out = plain(r.output)
        assert "reference" in out
        assert "support" in out

    def test_show_renders_the_declared_tool_semantics(self):
        """The declaration is the whole point — it is what lets the classifier
        read a fact instead of guessing from the tool's name."""
        r = _run("surface", "show", "support")
        assert r.exit_code == 0, r.output
        out = plain(r.output)
        assert "issue_refund" in out
        assert "derived space" in out

    def test_an_unknown_surface_is_a_usage_error_not_a_traceback(self):
        r = _run("surface", "show", "no-such-surface")
        assert r.exit_code != 0
        assert "Traceback" not in plain(r.output)


class TestItSaysWhatItCannotTest:
    def test_an_undeclared_risk_class_is_named(self):
        """The reference agent's calculator and lookup_kb declare no mutating
        flag. An expectation cannot forbid a tool whose risk class nobody
        stated, so the surface says so instead of quietly covering it."""
        r = _run("surface", "show", "reference")
        assert r.exit_code == 0, r.output
        out = plain(r.output)
        assert "undeclared" in out.lower()
        assert "calculator" in out

    def test_a_dimension_nothing_varies_is_named(self):
        """The `session_shape` failure, caught structurally this time.

        That coverpoint declared three bins and `realize()` read none of them, so
        asking for `multi_turn` produced text byte-identical to `single_turn` and
        recorded a stimulus hit for a corner it never produced. It took five
        rounds of review to find. Here the derived space reports the same class
        of defect about itself, at the moment an operator looks at it.
        """
        r = _run("surface", "show", "reference")
        out = plain(r.output)
        assert "realize identical stimulus" in out


class TestTheSpaceIsActuallyThisAgents:
    def test_two_surfaces_do_not_produce_the_same_space(self):
        """If every agent derived one space the derivation would be decorative."""
        a = plain(_run("surface", "show", "support").output)
        b = plain(_run("surface", "show", "reference").output)
        assert "space-support-retail" in a
        assert "space-anthropic-simple-ref" in b

    def test_a_read_only_agent_cannot_be_asked_to_refund(self):
        """The customisation that matters: the reference agent has no refund
        tool, so `refund` is not a legal intent in its space. Scoring it against
        a generic retail space would report a permanent hole in a dimension the
        agent has no way to enter."""
        out = plain(_run("surface", "show", "reference").output)
        # its intents are its own workflows, not the retail ones
        assert "answer_question" in out
        assert "refund," not in out


class TestCdvRunsTheDerivedSpace:
    def test_the_surface_flag_is_offered(self):
        r = _run("cdv", "--help")
        assert r.exit_code == 0
        assert "--surface" in plain(r.output)

    def test_an_unknown_surface_fails_before_any_spend(self):
        """--surface resolves before the adapter is built, so a typo costs
        nothing. A run that dies after paying for scenarios would be the worse
        failure."""
        r = _run("cdv", "--agent", "a", "--rubric", "r",
                 "--surface", "no-such-surface", "--mock")
        assert r.exit_code != 0
        assert "Traceback" not in plain(r.output)

    def test_the_generic_space_error_points_at_the_derived_one(self, tmp_path):
        """An operator who asks for a space that is not there should be told the
        per-agent path exists — that is how P6 stops being invisible."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(Path("config.yaml").read_text())
        r = _run("cdv", "--agent", "a", "--rubric", "r",
                 "--space", "space-does-not-exist", "--mock",
                 "--config", str(cfg))
        assert r.exit_code != 0
        assert "--surface" in plain(r.output)
