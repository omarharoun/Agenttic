"""AI Agent Index import/export (SPEC-2 T22.1/T22.2).

Imports the vendored, CC-BY-4.0 AI Agent Index dataset into agent cards:

* one card per dataset agent, ``source="index_import"``;
* every populated field is **documented** provenance, with citations preserved
  (the dataset DOI + the agent's website);
* the CC BY **attribution** is set on every card;
* **no scores** are imported — Index-derived data is Catalog-only and never mixes
  into measured scores or score leaderboards (Hard Rule 17).
"""

from __future__ import annotations

import csv
import io
import json

from ascore.cards.fields import CATEGORIES, field_key
from ascore.schema.agent_card import AgentCard, FieldValue

DATASET_DOI = "10.5281/zenodo.19592546"
DATASET_URL = "https://zenodo.org/records/19592546"
ATTRIBUTION = (
    "The 2025 AI Agent Index (Zenodo, DOI 10.5281/zenodo.19592546), "
    "licensed CC BY 4.0."
)

_EMPTY = {"", "none", "n/a", "na", "unknown", "null", "-"}


def _is_empty(v) -> bool:
    if v is None:
        return True
    return str(v).strip().lower() in _EMPTY


def _agent_citations(agent: dict) -> list[str]:
    cites = [f"doi:{DATASET_DOI}"]
    website = (agent.get("Product overview", {}) or {}).get("Website")
    if website and not _is_empty(website):
        cites.append(str(website).strip())
    return cites


def _card_from_agent(agent: dict) -> AgentCard:
    name = agent.get("agent_name") or "unknown-agent"
    agent_id = f"index:{name}"
    citations = _agent_citations(agent)
    fields: dict[str, FieldValue] = {}
    for cat in CATEGORIES:
        block = agent.get(cat)
        if not isinstance(block, dict):
            continue
        for field_name, value in block.items():
            key = field_key(cat, field_name)
            if _is_empty(value):
                fields[key] = FieldValue.none_found(key)
            else:
                # documented provenance with citations preserved (Hard Rule 17)
                fields[key] = FieldValue.documented(key, str(value), citations)
    return AgentCard(agent_id=agent_id, source="index_import", fields=fields,
                     attribution=ATTRIBUTION)


def load_index_cards(path=None) -> list[AgentCard]:
    """Build (but don't persist) one card per dataset agent."""
    from ascore.cards.fields import _vendor_path
    p = path or _vendor_path()
    data = json.loads(open(p).read()) if path else json.loads(p.read_text())
    return [_card_from_agent(a) for a in data]


def import_index_cards(reg, path=None) -> int:
    """Import all Index cards into ``reg`` (Catalog-only). Returns the count."""
    cards = load_index_cards(path)
    for card in cards:
        reg.save_card(card)
    return len(cards)


# --------------------------------------------------------------------------- #
# Export (T22.2) — Index-schema JSON / CSV.
# --------------------------------------------------------------------------- #


def export_field_keys() -> set[str]:
    """The set of valid card field keys, from the generated registry."""
    from ascore.cards.fields import all_field_keys
    return set(all_field_keys())


def validate_export_round_trip(card: AgentCard) -> bool:
    """Export the card and confirm every exported field key round-trips against
    the *generated* registry (no hand-invented keys leak into an export)."""
    exported = json.loads(export_card(card, "json"))
    valid = export_field_keys()
    reserved = {"agent_id", "source", "attribution"}
    for key in exported:
        if key in reserved:
            continue
        if key not in valid:
            return False
    return True


def export_card(card: AgentCard, fmt: str = "json"):
    """Export a card to Index-schema JSON or CSV (flat field_key → value)."""
    flat = {
        "agent_id": card.agent_id,
        "source": card.source,
        "attribution": card.attribution,
    }
    for key, fv in card.fields.items():
        flat[key] = fv.value if fv.status == "value_present" else None
    if fmt == "json":
        return json.dumps(flat, sort_keys=True)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(list(flat.keys()))
        w.writerow([("" if v is None else v) for v in flat.values()])
        return buf.getvalue()
    raise ValueError(f"unknown export format {fmt!r}")
