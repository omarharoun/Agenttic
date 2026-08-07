"""A command must not emit a confident artifact about a subject that does not exist.

Found by a read-only review on 2026-08-07. Three separate reports were one
habit: a command takes a subject (an agent id, a file path), never checks it,
and proceeds to a legitimate-looking result at exit 0.

The worst of them was `abom <unknown-agent>`, which emitted a structurally VALID
CycloneDX supply-chain attestation with zero components and a sha256 intended to
be referenced from a certification manifest. `validate_abom` only asks whether
the document matches the schema, never whether it says anything — so an
attestation about a subject that does not exist passed every check and exited 0.

In a product whose entire claim is that a green result means something, that is
the highest-severity class of defect available: it fails silently, and the
output looks exactly like a real one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agenttic.cli import app

runner = CliRunner()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A throwaway workspace with a real config and an empty registry."""
    cfg = Path("config.yaml").read_text(encoding="utf-8")
    (tmp_path / "config.yaml").write_text(cfg, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def declare(agent_id: str, *extra: str):
    return runner.invoke(app, ["agents", "add", agent_id, "--variant",
                               "reference", "--model", "claude-sonnet-4-6", *extra])


class TestAbomRefusesAnUnknownSubject:
    def test_an_unknown_agent_does_not_produce_an_attestation(self, ws):
        r = runner.invoke(app, ["abom", "ghost-agent"])
        assert r.exit_code != 0, r.output
        assert "unknown agent" in r.output
        assert not (ws / "abom.json").exists(), (
            "a file was written for a subject that does not exist")

    def test_the_failure_names_what_to_do(self, ws):
        r = runner.invoke(app, ["abom", "ghost-agent"])
        assert "agents add" in r.output          # how to declare it

    def test_a_declared_agent_still_emits_its_components(self, ws):
        assert declare("real-agent").exit_code == 0
        r = runner.invoke(app, ["abom", "real-agent",
                                "--model", "claude-sonnet-4-6", "--tool", "search"])
        assert r.exit_code == 0, r.output
        assert "2 components" in r.output
        assert (ws / "abom.json").exists()

    def test_an_EMPTY_bom_for_a_real_agent_is_warned_about(self, ws):
        """Structurally valid, semantically empty. It is still allowed — you may
        legitimately build up a BOM — but it must not look like a finished one,
        because the printed sha256 is meant to be referenced from a manifest."""
        assert declare("real-agent").exit_code == 0
        r = runner.invoke(app, ["abom", "real-agent"])
        assert r.exit_code == 0, r.output
        assert "0 components" in r.output
        assert "EMPTY supply chain" in r.output
        assert "WARNING" in r.output


class TestAgentsAddDoesNotSilentlyClobber:
    def test_re_adding_an_id_is_refused(self, ws):
        assert declare("dupe").exit_code == 0
        r = declare("dupe")          # same id, second time
        assert r.exit_code != 0
        assert "already declared" in r.output

    def test_force_still_replaces(self, ws):
        assert declare("dupe").exit_code == 0
        r = runner.invoke(app, ["agents", "add", "dupe", "--force",
                                "--variant", "blackbox", "--url", "https://ex.com"])
        assert r.exit_code == 0, r.output

    def test_the_refusal_says_what_would_happen(self, ws):
        declare("dupe")
        r = declare("dupe")
        assert "REPLACES the whole" in r.output
        assert "--force" in r.output


class TestEvaluateDistinguishesAPathFromProse:
    """The review reported that `evaluate <path>` classifies the FILENAME. That
    is not what happens — an existing file is read correctly. The real defect is
    narrower: a path-shaped argument that does NOT exist silently became prose,
    so one typo produced a confident `custom — 0.00 / needs_generation`."""

    def test_an_existing_file_is_read_not_classified_as_its_name(self, ws):
        doc = ws / "real.txt"
        doc.write_text("A customer support agent that issues refunds, checks "
                       "order status, and must escalate disputes to a human.",
                       encoding="utf-8")
        r = runner.invoke(app, ["evaluate", "./real.txt"])
        assert r.exit_code == 0, r.output
        # the FILE's content classified, not the string "./real.txt"
        assert "conversational_transactional" in r.output

    def test_a_path_shaped_argument_that_does_not_exist_is_refused(self, ws):
        r = runner.invoke(app, ["evaluate", "./nope-does-not-exist.txt"])
        assert "looks like a file path but does not exist" in r.output
        assert "needs_generation" not in r.output   # never silently classified

    def test_inline_prose_still_works(self, ws):
        r = runner.invoke(app, [
            "evaluate",
            "A customer support agent that issues refunds, checks order status, "
            "and must escalate disputes to a human."])
        assert r.exit_code == 0, r.output
        assert "conversational_transactional" in r.output


class TestTheGuardIsShared:
    def test_cards_autofill_uses_it_too(self, ws):
        """The same habit, one more command. Guarding only the reported ones
        would leave the pattern alive in its siblings."""
        r = runner.invoke(app, ["cards", "autofill", "ghost-agent"])
        assert r.exit_code != 0
        assert "unknown agent" in r.output

    def test_one_helper_backs_all_of_them(self):
        import inspect

        from agenttic import cli

        src = inspect.getsource(cli)
        assert src.count("_require_agent(cfg, reg,") >= 2
        assert "def _require_agent(" in src
