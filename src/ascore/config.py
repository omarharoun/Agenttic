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
    _validate_certification_surface(cfg)
    return cfg


# Severities that MUST carry an SLA clock (Incident model, SPEC-2 M6).
_REQUIRED_SLA_SEVERITIES = ("S1", "S2", "S3", "S4")


def _validate_certification_surface(cfg: dict) -> None:
    """Fail loudly if the certification/incidents config surface is malformed.

    The certification track keys thresholds, SLA clocks, and posture entirely off
    config (Hard Rule 5). A missing ``incidents.sla_hours.S1`` (or any required
    severity) would silently break the SLA clock, so we reject it at load time.
    """
    incidents = cfg.get("incidents")
    if incidents is None:
        # Certification surface is optional for pure SPEC-1 deployments; only
        # validate it when present.
        return
    sla = incidents.get("sla_hours")
    if not isinstance(sla, dict):
        raise ValueError(
            "config.yaml: incidents.sla_hours must be a mapping of severity -> hours"
        )
    for sev in _REQUIRED_SLA_SEVERITIES:
        if sev not in sla:
            raise ValueError(
                f"config.yaml: incidents.sla_hours.{sev} is required "
                f"(certification incident SLA clock)"
            )
        if not isinstance(sla[sev], (int, float)) or sla[sev] <= 0:
            raise ValueError(
                f"config.yaml: incidents.sla_hours.{sev} must be a positive number"
            )
