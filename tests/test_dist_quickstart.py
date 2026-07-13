"""SPEC-8 T43.2 — the finish-line promise is machine-checkable.

Unattended, no API key: `agenttic init` in an empty dir yields a working config
+ sample that certifies the reference agent with no further edits, and the
init → certify --mock → verify path finishes under a minute. This exercises the
installed CLI (`python -m ascore`) in a temp dir; the full fresh-venv install
path lives in scripts/quickstart_check.sh (run in CI).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# The under-a-minute promise. Overridable for slow CI runners.
BUDGET_SECONDS = int(os.environ.get("QUICKSTART_BUDGET_SECONDS", "60"))


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "agenttic", *args],
                          cwd=str(cwd), capture_output=True, text=True)


def test_init_scaffolds_a_working_certifiable_quickstart(tmp_path: Path):
    # 1) init an empty dir
    r = _cli("init", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    for f in ("config.yaml", "kb.json", "agent_sample.py", "QUICKSTART.md"):
        assert (tmp_path / f).exists(), f

    # 2) certify the reference agent in mock mode — no edits, no API key
    r = _cli("certify", "--mock", "--out", "dossier.json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Tier" in r.stdout
    assert (tmp_path / "dossier.json").exists()

    # 3) verify the dossier offline
    r = _cli("dossier", "verify", "dossier.json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "VERIFIED" in r.stdout


def test_init_certify_verify_under_a_minute(tmp_path: Path):
    start = time.monotonic()
    assert _cli("init", cwd=tmp_path).returncode == 0
    assert _cli("certify", "--mock", "--out", "d.json", cwd=tmp_path).returncode == 0
    assert _cli("dossier", "verify", "d.json", cwd=tmp_path).returncode == 0
    elapsed = time.monotonic() - start
    assert elapsed < BUDGET_SECONDS, f"quickstart took {elapsed:.1f}s (> {BUDGET_SECONDS}s)"


def test_init_does_not_clobber_existing_files(tmp_path: Path):
    (tmp_path / "config.yaml").write_text("# my config\n")
    r = _cli("init", cwd=tmp_path)
    assert r.returncode == 0
    # existing file preserved (skipped), not overwritten
    assert (tmp_path / "config.yaml").read_text() == "# my config\n"
    assert "skipped" in r.stdout.lower()
    # --force overwrites
    r = _cli("init", "--force", cwd=tmp_path)
    assert r.returncode == 0
    assert (tmp_path / "config.yaml").read_text() != "# my config\n"


def test_quickstart_script_exists_and_is_executable():
    script = Path(__file__).resolve().parent.parent / "scripts" / "quickstart_check.sh"
    assert script.exists()
    assert os.access(script, os.X_OK), "quickstart_check.sh must be executable"
