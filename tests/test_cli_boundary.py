"""Ordinary bad input gets one clean line, not a stack trace.

The underlying exceptions were always correct and meaningful — they just reached
the terminal uncaught. The tell was the inconsistency: the same class of mistake
got opposite treatment depending on whether a command happened to wrap its
lookup in `typer.BadParameter`. `attest <bad-id>` printed "Invalid value:
unknown scorecard"; `report <bad-id>` dumped a traceback. Both are "you named
something that does not exist".

The worst case was a new user: the four commands that need a config printed
`FileNotFoundError: config.yaml` instead of naming `agenttic init`.

What this must NOT do is swallow surprises. An unanticipated exception is a bug
report, and a tidy message in its place costs more than it saves — so only known
domain faults are mapped, and `AGENTTIC_TRACEBACK=1` restores the full trace for
any of them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RUN = [sys.executable, "-c", "from agenttic.cli import main; main()"]


def cli(*args, cwd, env_extra=None):
    import os
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([*RUN, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=180)


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "config.yaml").write_text(
        Path("config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


class TestKnownFaultsAreOneLine:
    def test_an_unknown_scorecard_does_not_traceback(self, ws):
        r = cli("report", "nonexistent-id", cwd=ws)
        assert r.returncode == 2
        assert "Traceback" not in r.stderr
        assert "Not found" in r.stdout

    def test_an_unknown_suite_does_not_traceback(self, ws):
        r = cli("approve", "no-such-suite", cwd=ws)
        assert r.returncode == 2
        assert "Traceback" not in r.stderr

    def test_a_missing_config_names_the_fix(self, tmp_path):
        """The single most likely first experience of the tool."""
        r = cli("certify", "--mock", cwd=tmp_path)      # no config.yaml at all
        assert r.returncode == 2
        assert "Traceback" not in r.stderr
        assert "agenttic init" in r.stdout


class TestSurprisesStillSurface:
    def test_the_escape_hatch_restores_the_traceback(self, ws):
        r = cli("report", "nonexistent-id", cwd=ws,
                env_extra={"AGENTTIC_TRACEBACK": "1"})
        assert "Traceback" in r.stderr

    def test_only_known_exceptions_are_mapped(self):
        """A bare `except Exception` here would hide every real bug behind a
        tidy message. The handler must name what it catches."""
        import inspect

        from agenttic import cli

        src = inspect.getsource(cli.main)
        assert "except Exception" not in src
        for known in ("FileNotFoundError", "NotFoundError",
                      "DuplicateVersionError", "JSONDecodeError"):
            assert known in src

    def test_control_flow_exceptions_are_re_raised(self):
        import inspect

        from agenttic import cli

        src = inspect.getsource(cli.main)
        assert "typer.Exit, typer.Abort, SystemExit, KeyboardInterrupt" in src


class TestVersion:
    def test_the_tool_reports_its_version(self, ws):
        from agenttic import __version__

        r = cli("--version", cwd=ws)
        assert r.returncode == 0
        assert __version__ in r.stdout


class TestGenerateValidatesInputBeforeAuthenticating:
    """Auth was resolved inside the Anthropic client, so a missing key raised a
    raw TypeError and a broken input file never reached its own validation."""

    def test_no_key_is_an_honest_blocker_not_a_TypeError(self, ws):
        (ws / "doc.txt").write_text("A refund agent.", encoding="utf-8")
        r = cli("generate", "doc.txt", "s1", cwd=ws,
                env_extra={"ANTHROPIC_API_KEY": ""})
        assert "TypeError" not in r.stderr
        assert "No ANTHROPIC_API_KEY" in r.stdout
        assert "Nothing was spent" in r.stdout

    def test_an_empty_document_is_reported_even_without_a_key(self, ws):
        """Input first. A user with a broken file AND no key should hear about
        the file — that is the one they can act on."""
        (ws / "empty.txt").write_text("", encoding="utf-8")
        r = cli("generate", "empty.txt", "s1", cwd=ws,
                env_extra={"ANTHROPIC_API_KEY": ""})
        assert "is empty" in r.stdout + r.stderr
        assert "No ANTHROPIC_API_KEY" not in r.stdout

    def test_a_missing_document_is_reported(self, ws):
        r = cli("generate", "nope.txt", "s1", cwd=ws,
                env_extra={"ANTHROPIC_API_KEY": ""})
        assert "does not exist" in r.stdout + r.stderr


class TestJudgeCorpusGatesOnItsBlocker:
    def test_a_blocked_calibration_exits_non_zero(self, ws):
        """It printed BLOCKER and exited 0, so a CI gate on the exit code read a
        blocked calibration as success — the one reading that matters."""
        r = cli("judge", "corpus", cwd=ws)
        assert r.returncode == 1
        assert "BLOCKER" in r.stdout


class TestHintsMatchHowTheToolWasInstalled:
    def test_no_command_tells_a_pip_user_to_run_uv(self):
        import inspect

        from agenttic import cli

        assert "uv run agenttic" not in inspect.getsource(cli)
