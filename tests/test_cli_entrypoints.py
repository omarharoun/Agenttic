"""CLI entry points.

``agenttic`` is the single public command. The pre-rename ``ascore`` alias has
been **removed** — these tests pin that removal so it cannot creep back in, and
so an accidental reintroduction of the old name is a test failure rather than a
surprise in someone's shell.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_declares_exactly_one_console_script():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"]["scripts"]
    assert scripts == {"agenttic": "agenttic.cli:app"}, (
        "agenttic is the only console script; the deprecated `ascore` alias was "
        "removed and must not return")


def test_agenttic_module_help_resolves():
    r = subprocess.run([sys.executable, "-m", "agenttic", "--help"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Usage:" in r.stdout


def test_the_ascore_alias_is_gone_from_the_cli_module():
    from agenttic import cli
    assert not hasattr(cli, "_ascore_alias"), (
        "the deprecated ascore alias was removed; re-adding it would resurrect "
        "the old name in operators' shells")
    assert callable(cli.app)


def test_the_ascore_package_is_not_importable():
    """The rename is complete: nothing should resolve `import ascore`.

    This previously passed by accident — a stale `src/ascore/` full of orphaned
    __pycache__ made it resolve to an empty NAMESPACE package instead of raising.
    """
    import importlib
    try:
        mod = importlib.import_module("ascore")
    except ModuleNotFoundError:
        return
    raise AssertionError(
        f"`import ascore` unexpectedly resolved to {getattr(mod, '__file__', None)!r} "
        "(a namespace package resolves silently — check for a leftover src/ascore/)")
