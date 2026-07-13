"""T19.4 — agent card schema contracts (SPEC-2 M9)."""

from __future__ import annotations

import inspect
import tempfile

import pytest

from agenttic.cards import fields as fields_mod
from agenttic.cards.fields import generate_field_registry, registry_digest
from agenttic.registry.sqlite_store import AgentCardRow, Registry
from agenttic.schema.agent_card import AgentCard, FieldValue


def test_unprovenanced_value_is_impossible():
    # a present value with no backing refs at all is rejected (no refs ⇒ no value)
    with pytest.raises(ValueError):
        FieldValue(field_key="x", status="value_present", value="v")
    # declaring a provenance without its backing is rejected
    with pytest.raises(ValueError):
        FieldValue(field_key="x", status="value_present", value="v",
                   provenance="measured")  # no evidence_refs
    with pytest.raises(ValueError):
        FieldValue(field_key="x", status="value_present", value="v",
                   provenance="documented")  # no citations
    with pytest.raises(ValueError):
        FieldValue(field_key="x", status="value_present", value="v",
                   provenance="attested")  # no signature


def test_none_found_is_free_but_confirmed_none_needs_evidence():
    # none_found needs nothing
    FieldValue.none_found("bug_bounty")
    # confirmed_none WITHOUT evidence is rejected
    with pytest.raises(ValueError):
        FieldValue(field_key="bug_bounty", status="confirmed_none")
    # with a citation OR measurement it's allowed
    FieldValue(field_key="bug_bounty", status="confirmed_none",
               citations=["https://vendor/policy"])
    FieldValue(field_key="bug_bounty", status="confirmed_none",
               evidence_refs=["scorecard:1"])


def test_none_found_and_confirmed_none_are_distinct():
    a = FieldValue.none_found("x")
    b = FieldValue(field_key="x", status="confirmed_none", citations=["c"])
    assert a.status != b.status
    assert a.status == "none_found" and b.status == "confirmed_none"


def test_non_present_status_carries_no_value():
    with pytest.raises(ValueError):
        FieldValue(field_key="x", status="none_found", value="v")
    with pytest.raises(ValueError):
        FieldValue(field_key="x", status="not_applicable", value="v")


def test_field_registry_deterministic():
    generate_field_registry.cache_clear()
    d1 = registry_digest()
    generate_field_registry.cache_clear()
    d2 = registry_digest()
    assert d1 == d2
    reg = generate_field_registry()
    assert len(reg) == 6  # the six pinned categories


def test_no_mutation_helpers_on_field_registry():
    # the registry is generated, not edited — no add/set/mutate helpers
    names = {n for n, _ in inspect.getmembers(fields_mod)}
    for forbidden in ("add_field", "set_field", "edit_registry", "mutate"):
        assert forbidden not in names


def test_card_round_trip_and_append_only():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        c = AgentCard(agent_id="a1",
                      fields={"m": FieldValue.measured("m", "claude", ["s:1"])})
        reg.save_card(c)
        back = reg.get_card("a1")
        assert back.get("m").value == "claude"
        # append-only: saving again bumps the version, never overwrites v1
        reg.save_card(c)
        assert reg.get_card("a1").version == 2
        assert reg.get_card("a1", version=1).version == 1
        # AgentCardRow has no in-place update helper on the model
        assert not hasattr(AgentCardRow, "update_payload")
