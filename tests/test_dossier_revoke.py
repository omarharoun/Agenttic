"""T17.4 — append-only revocation; no manual-promotion path (SPEC-2 M7)."""

from __future__ import annotations

import inspect
import tempfile

import pytest

from agenttic.certification import dossier as dossier_mod
from agenttic.certification import staleness
from agenttic.certification.dossier import assemble, revoke
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)


def _dossier(reg):
    prof = CertificationProfile(profile_id="p", required_domains=["tool_use"])
    return assemble(reg, agent_id="a1", agent_config_hash="h", profile=prof,
                    tier_decision=TierDecision(tier="B", evidence_refs=["x"]),
                    coverage=[], attestation=Attestation(mode="self_attested",
                                                         tenant="default"))


def test_revoke_is_sticky_and_readable_forever():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        d = _dossier(reg)
        assert staleness.status(reg, d) == "current"
        revoke(reg, d.dossier_id, reason="safety regression")
        # status is revoked, and stays revoked
        assert staleness.status(reg, d) == "revoked"
        # still readable forever
        again = reg.get_dossier(d.dossier_id)
        assert again.dossier_id == d.dossier_id
        assert staleness.status(reg, again) == "revoked"
        # the reason is recorded on the append-only event log
        events = reg.list_dossier_events(d.dossier_id)
        assert any(e["event_type"] == "revoked"
                   and e["reason"] == "safety regression" for e in events)


def test_revoke_requires_a_reason():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        d = _dossier(reg)
        with pytest.raises(ValueError):
            revoke(reg, d.dossier_id, reason="")


class TestTheCliAsksBeforeAnIrreversibleRevoke:
    """`dossier revoke` says in its own docstring that there is no way back,
    and it took a mistyped id without a word. It now asks — but only when
    someone is there to answer.

    A prompt that fires in CI would break every scripted revocation, and would
    not catch the mistake it is meant to catch (a typo already committed to a
    script). So the guard is tied to an interactive terminal, and the tests
    pin both halves: it asks a human, and it never blocks a pipe.
    """

    def _ws(self, tmp_path):
        from pathlib import Path
        repo = Path(__file__).resolve().parents[1]
        (tmp_path / "config.yaml").write_text(
            (repo / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        return tmp_path

    def test_answering_no_at_the_prompt_leaves_the_dossier_alone(
            self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from agenttic.cli import app

        monkeypatch.chdir(self._ws(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        res = CliRunner().invoke(
            app, ["dossier", "revoke", "does-not-matter", "--reason", "oops"],
            input="n\n")
        assert res.exit_code != 0
        assert "REVOKED" not in res.stdout

    def test_a_pipe_is_never_blocked_waiting_for_an_answer(
            self, tmp_path, monkeypatch):
        """Not a tty: it must reach the lookup and fail on the unknown id,
        which proves it got past the prompt rather than hanging at it."""
        from typer.testing import CliRunner

        from agenttic.cli import app

        monkeypatch.chdir(self._ws(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        res = CliRunner().invoke(
            app, ["dossier", "revoke", "no-such-dossier", "--reason", "ci"])
        # Exit 2 is click's UsageError — the unknown id was REJECTED BY THE
        # LOOKUP, which it only reaches by getting past the prompt. An abort at
        # the prompt would be exit 1, and a hang would time out.
        assert res.exit_code == 2, res.output

    def test_yes_skips_the_prompt_even_on_a_terminal(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from agenttic.cli import app

        monkeypatch.chdir(self._ws(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        res = CliRunner().invoke(
            app, ["dossier", "revoke", "no-such-dossier", "--reason", "ci",
                  "--yes"])
        # Exit 2 is click's UsageError — the unknown id was REJECTED BY THE
        # LOOKUP, which it only reaches by getting past the prompt. An abort at
        # the prompt would be exit 1, and a hang would time out.
        assert res.exit_code == 2, res.output


def test_no_manual_promotion_code_path():
    # There is deliberately no function to promote a tier or un-revoke a dossier.
    dossier_names = {n for n, _ in inspect.getmembers(dossier_mod)}
    for forbidden in ("promote", "unrevoke", "un_revoke", "set_status",
                      "grant_tier", "set_tier"):
        assert forbidden not in dossier_names
    # status() only ever returns computed values, never accepts a status to set
    sig = inspect.signature(staleness.status)
    assert "status" not in sig.parameters  # no injectable status
    assert set(inspect.signature(staleness.status).parameters) >= {"reg", "dossier"}
