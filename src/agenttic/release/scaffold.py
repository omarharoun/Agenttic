"""`agenttic init` — scaffold a runnable quickstart (SPEC-8 Step 43, T43.1).

Writes a minimal, working starting point into a directory: a complete
certify-ready ``config.yaml``, the reference agent's ``kb.json``, a
``agent_sample.py`` showing ``trace``/``@instrument``/``session``/``--url``, and
a ``QUICKSTART.md``. With these in place, ``agenttic certify --mock`` produces a
signed grade with no further edits and no API key.
"""
from __future__ import annotations

import importlib.resources as resources
from pathlib import Path

# The files copied into the target directory, in stable order.
SCAFFOLD_FILES = ("config.yaml", "kb.json", "agent_sample.py", "QUICKSTART.md")

_ASSET_PKG = "agenttic.release.scaffold_assets"


def _asset_text(name: str) -> str:
    return resources.files(_ASSET_PKG).joinpath(name).read_text(encoding="utf-8")


def scaffold(dest: str | Path = ".", *, target: str = "",
             force: bool = False) -> dict:
    """Write the quickstart files into ``dest``.

    ``target`` (optional) is written into ``config.yaml``'s ``distribution.target``
    so wrapped agents emit to that endpoint; blank leaves the offline default.
    Existing files are skipped unless ``force`` is set. Returns
    ``{"written": [...], "skipped": [...], "dest": <abs path>}``."""
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[str] = []
    for name in SCAFFOLD_FILES:
        out = dest_path / name
        if out.exists() and not force:
            skipped.append(name)
            continue
        text = _asset_text(name)
        if name == "config.yaml" and target:
            text = _set_target(text, target)
        out.write_text(text, encoding="utf-8")
        written.append(name)

    return {"written": written, "skipped": skipped, "dest": str(dest_path.resolve())}


def _set_target(config_text: str, target: str) -> str:
    """Set ``distribution.target`` in the scaffolded config. Targeted line
    replacement so the config's comments survive."""
    out_lines = []
    replaced = False
    for line in config_text.splitlines():
        if not replaced and line.lstrip().startswith("target:") \
                and 'v1/traces' in line:  # the distribution.target line
            indent = line[: len(line) - len(line.lstrip())]
            out_lines.append(f'{indent}target: "{target}"'
                             "          # set by `agenttic init --target`")
            replaced = True
        else:
            out_lines.append(line)
    text = "\n".join(out_lines)
    if config_text.endswith("\n"):
        text += "\n"
    return text
