"""Card field registry — generated from the vendored AI Agent Index dataset
(SPEC-2 T19.2).

The six card categories and their fields are **generated deterministically** by
reading the vendored ``2025_annotations.json`` — never hand-transcribed. Two runs
over the same file produce byte-identical output (fields are the ordered
first-seen union across all agents, per category).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# The six pinned categories, in the dataset's canonical order.
CATEGORIES: tuple[str, ...] = (
    "Product overview",
    "Company & accountability",
    "Technical capabilities & system architecture",
    "Autonomy & control",
    "Ecosystem interaction",
    "Safety, evaluation & impact",
)


def _vendor_path() -> Path:
    """Locate the vendored dataset from any reasonable cwd / install layout."""
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd() / "data/vendor/ai-agent-index/2025_annotations.json",
        # repo root is three parents up from src/agenttic/cards/fields.py
        here.parents[3] / "data/vendor/ai-agent-index/2025_annotations.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "vendored AI Agent Index dataset not found "
        "(data/vendor/ai-agent-index/2025_annotations.json)")


def _load_dataset() -> list[dict]:
    return json.loads(_vendor_path().read_text())


def field_key(category: str, field_name: str) -> str:
    """A stable, flat key for a category field (e.g.
    ``autonomy_and_control.autonomy_level_and_planning_depth``)."""
    def slug(s: str) -> str:
        out = []
        for ch in s.lower():
            if ch.isalnum():
                out.append(ch)
            elif ch in " -/&":
                out.append("_")
        key = "".join(out)
        while "__" in key:
            key = key.replace("__", "_")
        return key.strip("_")
    return f"{slug(category)}.{slug(field_name)}"


@lru_cache(maxsize=1)
def generate_field_registry() -> dict:
    """Deterministically build the field registry from the vendored dataset.

    Returns ``{category: [(field_name, field_key), ...]}`` — the ordered
    first-seen union of field names per category across every agent."""
    data = _load_dataset()
    registry: dict[str, list[tuple[str, str]]] = {c: [] for c in CATEGORIES}
    seen: dict[str, set[str]] = {c: set() for c in CATEGORIES}
    for agent in data:
        for cat in CATEGORIES:
            block = agent.get(cat)
            if not isinstance(block, dict):
                continue
            for name in block:  # dict preserves insertion order
                if name not in seen[cat]:
                    seen[cat].add(name)
                    registry[cat].append((name, field_key(cat, name)))
    return registry


def all_field_keys() -> list[str]:
    """Every card field key, in category then first-seen order."""
    reg = generate_field_registry()
    return [fk for cat in CATEGORIES for (_name, fk) in reg[cat]]


def card_completeness(card) -> dict:
    """Per-category completeness for a card: how many fields carry a present
    value out of the category's total (from the generated registry)."""
    reg = generate_field_registry()
    out: dict[str, dict] = {}
    total_present = total_fields = 0
    for cat in CATEGORIES:
        keys = [fk for (_n, fk) in reg[cat]]
        present = sum(1 for fk in keys
                      if (fv := card.fields.get(fk)) is not None
                      and fv.status == "value_present")
        out[cat] = {"present": present, "total": len(keys),
                    "pct": round(100 * present / len(keys), 1) if keys else 0.0}
        total_present += present
        total_fields += len(keys)
    out["_overall"] = {"present": total_present, "total": total_fields,
                       "pct": round(100 * total_present / total_fields, 1)
                       if total_fields else 0.0}
    return out


def registry_digest() -> str:
    """A stable digest of the generated registry — proves deterministic
    regeneration (two runs over the same dataset agree)."""
    import hashlib
    reg = generate_field_registry()
    payload = json.dumps(reg, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
