"""SPEC-2 Step 11 — HumanFeedback schema + registry store.

Acceptance criteria:
- round-trip + validation-failure tests (correction without corrected_output
  raises; rating off the {0, 0.5, 1} scale raises)
- registry CRUD + unprocessed filter tested
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agenttic.registry.sqlite_store import NotFoundError, Registry
from agenttic.schema.feedback import HumanFeedback

T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _fb(fid="fb-1", *, trace_id="tr-1", agent_id="agent-1", source="reviewer",
        kind="approval", criterion_id=None, rating=None, corrected_output=None,
        rationale="looks right") -> HumanFeedback:
    return HumanFeedback(
        feedback_id=fid, trace_id=trace_id, agent_id=agent_id, source=source,
        kind=kind, criterion_id=criterion_id, rating=rating,
        corrected_output=corrected_output, rationale=rationale, created_at=T0)


# --------------------------------------------------------------------------- #
# Round-trip + validation
# --------------------------------------------------------------------------- #


class TestFeedbackSchema:
    def test_json_round_trip(self):
        fb = _fb("fb-rt", kind="correction",
                 corrected_output="the correct answer", source="end_user")
        assert HumanFeedback.model_validate_json(fb.model_dump_json()) == fb

    def test_correction_without_corrected_output_raises(self):
        with pytest.raises(ValidationError) as exc:
            _fb(kind="correction", corrected_output=None)
        assert "corrected_output" in str(exc.value)

    def test_correction_with_blank_corrected_output_raises(self):
        with pytest.raises(ValidationError):
            _fb(kind="correction", corrected_output="   ")

    def test_rating_off_scale_raises(self):
        with pytest.raises(ValidationError) as exc:
            _fb(kind="rating", criterion_id="tool_call_accuracy", rating=0.7)
        assert "scale" in str(exc.value).lower()

    def test_rating_without_criterion_raises(self):
        with pytest.raises(ValidationError) as exc:
            _fb(kind="rating", criterion_id=None, rating=1.0)
        assert "criterion_id" in str(exc.value)

    def test_rating_without_value_raises(self):
        with pytest.raises(ValidationError):
            _fb(kind="rating", criterion_id="c1", rating=None)

    @pytest.mark.parametrize("val", [0.0, 0.5, 1.0])
    def test_rating_on_scale_ok(self, val):
        fb = _fb(kind="rating", criterion_id="c1", rating=val)
        assert fb.rating == val


# --------------------------------------------------------------------------- #
# Registry CRUD + unprocessed filter
# --------------------------------------------------------------------------- #


class TestFeedbackRegistry:
    def test_save_and_query_by_agent_and_trace(self, tmp_path):
        reg = Registry(tmp_path / "f.db")
        a = _fb("fb-a", agent_id="bot", trace_id="tr-1")
        b = _fb("fb-b", agent_id="bot", trace_id="tr-2")
        c = _fb("fb-c", agent_id="other", trace_id="tr-1")
        for f in (a, b, c):
            reg.save_feedback(f)
        assert {f.feedback_id for f in reg.feedback_for("bot")} == {"fb-a", "fb-b"}
        assert {f.feedback_id for f in reg.feedback_for_trace("tr-1")} == {"fb-a", "fb-c"}

    def test_unprocessed_filter_and_mark_processed(self, tmp_path):
        reg = Registry(tmp_path / "f.db")
        reg.save_feedback(_fb("fb-1", agent_id="bot"))
        reg.save_feedback(_fb("fb-2", agent_id="bot"))
        assert {f.feedback_id for f in reg.unprocessed_feedback()} == {"fb-1", "fb-2"}
        assert {f.feedback_id for f in reg.unprocessed_feedback("bot")} == {"fb-1", "fb-2"}

        reg.mark_feedback_processed("fb-1")
        left = reg.unprocessed_feedback()
        assert [f.feedback_id for f in left] == ["fb-2"]
        # feedback_for still returns both (marking doesn't delete)
        assert len(reg.feedback_for("bot")) == 2

    def test_mark_missing_raises(self, tmp_path):
        reg = Registry(tmp_path / "f.db")
        with pytest.raises(NotFoundError):
            reg.mark_feedback_processed("nope")

    def test_tenant_isolation(self, tmp_path):
        reg = Registry(tmp_path / "f.db")
        reg.save_feedback(_fb("fb-1", agent_id="bot"))
        # a second tenant on the SAME engine sees none of tenant "default"'s rows
        other = Registry(engine=reg.engine, tenant="t2")
        assert other.feedback_for("bot") == []
        assert other.unprocessed_feedback() == []
        assert len(reg.feedback_for("bot")) == 1
