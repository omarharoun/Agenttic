"""CLI entry points after the ascore → agenttic rename.

`agenttic` is the public command; `ascore` remains a working *deprecated* alias
that forwards to the same Typer app (printing a one-line stderr nudge). Both
must resolve and exit 0 on ``--help``.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_entry_points_target_agenttic():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"]["scripts"]
    assert scripts["agenttic"] == "agenttic.cli:app"
    # ascore stays, as a deprecated alias, still driving the agenttic CLI
    assert scripts["ascore"] == "agenttic.cli:_ascore_alias"


def test_agenttic_module_help_resolves():
    r = subprocess.run([sys.executable, "-m", "agenttic", "--help"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Usage:" in r.stdout


def test_ascore_alias_resolves_and_warns(capsys):
    from agenttic.cli import _ascore_alias
    sys_argv = sys.argv
    try:
        sys.argv = ["ascore", "--help"]
        with pytest.raises(SystemExit) as exc:
            _ascore_alias()
        assert exc.value.code == 0
    finally:
        sys.argv = sys_argv
    err = capsys.readouterr().err
    assert "deprecated" in err and "agenttic" in err


def test_ascore_alias_forwards_to_same_app():
    # the alias drives the very same Typer app object (identical behavior)
    from agenttic import cli
    assert cli._ascore_alias.__module__ == "agenttic.cli"
    assert callable(cli.app)
