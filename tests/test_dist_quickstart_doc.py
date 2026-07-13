"""SPEC-8 T43.3 — every command in the quickstart doc is copy-paste runnable.

Parses docs/QUICKSTART.md, executes its shell commands against the offline mock
provider (translating the `agenttic` command to `python -m ascore`), and checks
its Python snippets compile. This is the doctest-style guarantee that the doc
never drifts from a working reality.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs" / "QUICKSTART.md"

FINISH_LINE = ("a developer who has never seen Agenttic can `pip install`, add "
               "one line, and get a signed safety grade in under a minute")


def _blocks(lang: str) -> list[str]:
    text = DOC.read_text()
    return re.findall(rf"```{lang}\n(.*?)```", text, re.DOTALL)


def test_doc_states_the_finish_line_promise_verbatim():
    assert FINISH_LINE in DOC.read_text()


def test_every_shell_command_is_runnable_against_mock(tmp_path):
    ran = []
    for block in _blocks("bash"):
        for line in block.splitlines():
            cmd = line.strip()
            if not cmd or cmd.startswith("#"):
                continue
            if cmd.startswith("pip install"):
                continue  # install is exercised by the fresh-venv CI script
            assert cmd.startswith("agenttic "), f"unexpected command: {cmd!r}"
            # commands needing a live external agent can't run offline
            if "--url" in cmd or "http://" in cmd or "https://" in cmd:
                continue
            args = cmd.split()[1:]  # drop the leading `agenttic`
            r = subprocess.run([sys.executable, "-m", "agenttic", *args],
                               cwd=str(tmp_path), capture_output=True, text=True)
            assert r.returncode == 0, f"`{cmd}` failed:\n{r.stderr}"
            ran.append(cmd)
    # the promise path (init → certify → verify) must have actually executed
    assert any(c.startswith("agenttic init") for c in ran)
    assert any("certify" in c for c in ran)
    assert any("verify" in c for c in ran)


def test_python_snippets_compile():
    blocks = _blocks("python")
    assert blocks, "quickstart should show the one-line Python usage"
    for i, block in enumerate(blocks):
        compile(block, f"<quickstart-python-{i}>", "exec")
