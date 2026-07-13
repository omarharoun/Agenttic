"""Model pricing helpers — the one place that turns tokens into dollars.

Prices live in ``config.yaml`` under ``pricing`` (USD per 1M tokens, per model),
honoring Hard Rule 7 (no hardcoded prices in code). Unlisted models fall back to
the ``default`` entry, then to a conservative built-in default.
"""

from __future__ import annotations

_BUILTIN_DEFAULT = {"input": 3.0, "output": 15.0}


def model_price(cfg: dict, model: str | None) -> dict:
    """{"input": <usd/Mtok>, "output": <usd/Mtok>} for ``model``."""
    table = (cfg.get("pricing") or {})
    entry = (model and table.get(model)) or table.get("default") or _BUILTIN_DEFAULT
    return {"input": float(entry["input"]), "output": float(entry["output"])}


def token_cost(cfg: dict, model: str | None,
               tokens_in: int | None, tokens_out: int | None) -> float:
    """USD cost of one call given its token usage."""
    p = model_price(cfg, model)
    return ((tokens_in or 0) * p["input"]
            + (tokens_out or 0) * p["output"]) / 1_000_000
