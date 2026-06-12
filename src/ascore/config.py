"""Config loader — every model name, threshold, and rate lives in config.yaml
(Hard Rule 7). Code never hardcodes these."""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_PATH = Path("config.yaml")


def load_config(path: str | Path = DEFAULT_PATH) -> dict:
    cfg = yaml.safe_load(Path(path).read_text())
    judge = cfg["models"]["judge_strong"]
    agent = cfg["models"]["agent_default"]
    if judge == agent:
        raise ValueError(
            "config.yaml: judge_strong must differ from agent_default (Hard Rule 4)"
        )
    return cfg
