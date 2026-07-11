"""SPEC-8 T40.4 — the public API surface is a semver'd promise, and importing
it must not drag in a framework SDK.

These tests enforce Hard Rules 36 (the top-level ``agenttic`` surface is exactly
the documented set; internal ``ascore.*`` stays internal) and 37 (base install
imports no framework SDK).
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
import types
from pathlib import Path

import agenttic
import ascore

# The exact, documented public surface. Adding a name here is a deliberate,
# semver-visible act; a name leaking in without being added fails the test.
EXPECTED_SURFACE = {"trace", "instrument", "session", "certify", "verify",
                    "Trace", "Span"}

# Framework SDKs that the base install must never import (Hard Rule 37).
FRAMEWORK_SDKS = ("langgraph", "langchain", "langchain_core", "agents",
                  "agenttic_langgraph", "agenttic_openai_agents")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _public_names(mod) -> set[str]:
    """Non-dunder, non-submodule names bound on a module — its public surface."""
    return {n for n in vars(mod)
            if not n.startswith("_")
            and not isinstance(getattr(mod, n), types.ModuleType)}


def test_all_declares_the_documented_surface():
    assert set(agenttic.__all__) == EXPECTED_SURFACE


def test_surface_is_exactly_all_no_leaks():
    # Nothing undocumented leaks past __all__ (e.g. a stray `Any`/`annotations`).
    assert _public_names(agenttic) == set(agenttic.__all__)


def test_every_exported_name_resolves():
    for name in agenttic.__all__:
        assert hasattr(agenttic, name), name
        assert getattr(agenttic, name) is not None, name


def test_version_matches_core():
    # The umbrella tracks the core version exactly (SPEC-8 Step 40).
    assert agenttic.__version__ == ascore.__version__


def test_version_matches_distribution_metadata():
    from importlib.metadata import PackageNotFoundError, version
    try:
        dist_version = version("agenttic")
    except PackageNotFoundError:  # not installed as a dist (pure source tree)
        return
    assert dist_version == agenttic.__version__


def test_pyproject_version_is_in_lockstep():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["name"] == "agenttic"
    assert data["project"]["version"] == ascore.__version__


def test_reexports_are_the_core_implementations():
    from ascore.certification.certify import certify as core_certify
    from ascore.certification.dossier import verify as core_verify
    from ascore.schema.trace import Span as CoreSpan
    from ascore.schema.trace import Trace as CoreTrace

    assert agenttic.certify is core_certify
    assert agenttic.verify is core_verify
    assert agenttic.Trace is CoreTrace
    assert agenttic.Span is CoreSpan


def test_import_agenttic_pulls_no_framework_sdk():
    """Import ``agenttic`` in a *fresh* interpreter and assert no framework SDK
    landed in sys.modules (proves lazy, OTel-first import — Hard Rule 37)."""
    probe = (
        "import sys; import agenttic; "
        f"leaked=[m for m in {FRAMEWORK_SDKS!r} if m in sys.modules]; "
        "assert not leaked, leaked; print('clean')"
    )
    out = subprocess.run([sys.executable, "-c", probe],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "clean" in out.stdout
