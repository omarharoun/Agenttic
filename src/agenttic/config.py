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
    _validate_coverage_surface(cfg)
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


def _validate_coverage_surface(cfg: dict) -> None:
    """Reject a closure target that cannot mean anything, at load time.

    ``coverage.closure_target`` is the bar sign-off gates on, and the code that
    reads it (``coverage.targets.closure_target``) is called from inside coverage
    model construction — where raising would cost a run its coverage entirely, so
    it falls back to the documented default instead. That makes a typo here
    invisible at exactly the moment it matters: ``closure_target: 1.5`` loads
    clean, every run is silently measured against 0.95, and the operator believes
    they raised the bar. The loudness belongs here, where a human is watching a
    command refuse to start.

    A fraction, not a percentage: ``95`` is the mistake this rejects, and it is
    the one that would otherwise read as "closure can never be reached".
    """
    coverage = cfg.get("coverage")
    if coverage is None:
        # Optional section, like the certification surface above: a config
        # predating it is valid and runs on the documented default.
        return
    if not isinstance(coverage, dict):
        raise ValueError("config.yaml: coverage must be a mapping")
    if "closure_target" not in coverage:
        return
    target = coverage["closure_target"]
    # bool before number: `isinstance(True, int)` is True, and float(True) == 1.0
    # would accept `closure_target: yes` as "close everything".
    if isinstance(target, bool) or not isinstance(target, (int, float)):
        raise ValueError(
            f"config.yaml: coverage.closure_target must be a number in (0, 1] "
            f"(got {target!r})"
        )
    if not 0.0 < float(target) <= 1.0:
        raise ValueError(
            f"config.yaml: coverage.closure_target must be in (0, 1] — a closure "
            f"fraction, not a percentage (got {target!r})"
        )
