"""Every event the backend emits must reach the GUI.

The console showed *that* a node progressed, not *what happened*. `ops` and the
generator emit 14 typed domain events; `server/nodes.py` flattens all of them
into one `node_progress` envelope, and `ui/src/store.ts` unpacked only four —
so budget stops, cost projections, scenario failures and every rubric-search
event rendered an empty string and vanished.

Nothing failed when that happened, which is why it lasted: an unhandled event
falls through `summarize()` to `return ""`, and an empty log line looks exactly
like no event at all.

This test is the drift alarm. It greps both sides rather than introducing a
generated schema, because the alternative — a codegen step and a shared IDL for
14 string constants — is more machinery than the problem deserves. If someone
adds an emit and no renderer, this fails and names it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "ui" / "src" / "store.ts"

#: Emitted, but deliberately NOT rendered as a log line. Keep this list short and
#: justified — it is the escape hatch that could quietly re-open the gap.
INTENTIONALLY_UNRENDERED: dict[str, str] = {
    # The envelope itself, not a domain event.
    "node_progress": "the wrapper server/nodes.py emits domain events inside",
}


def emitted_events() -> set[str]:
    """Every domain event name the backend emits, from the source."""
    names: set[str] = set()
    for path in (ROOT / "src" / "agenttic").rglob("*.py"):
        text = path.read_text(errors="ignore")
        # THREE call styles, and missing one is how this inventory goes quietly
        # vacuous: `on_event` was omitted at first and hid five real events.
        names |= set(re.findall(r'on_progress\(\s*"([a-z_]+)"', text))
        names |= set(re.findall(r'on_event\(\s*"([a-z_]+)"', text))
        names |= set(re.findall(r'\bemit\(\s*"([a-z_]+)"', text))
    return names


def rendered_events() -> set[str]:
    """Event names the GUI turns into something a human sees."""
    text = STORE.read_text()
    return set(re.findall(r'd\.event === "([a-z_]+)"', text))


def progress_unit_events() -> set[str]:
    text = STORE.read_text()
    block = text.split("const UNIT_DONE = new Set([")[1].split("]")[0]
    return set(re.findall(r'"([a-z_]+)"', block))


class TestNoEmittedEventIsSilentlyDropped:
    def test_every_emitted_event_reaches_the_gui(self):
        missing = emitted_events() - rendered_events() - set(INTENTIONALLY_UNRENDERED)
        assert not missing, (
            "these events are emitted by the backend and render NOTHING in the "
            f"console: {sorted(missing)}. Add a case to summarize() in "
            "ui/src/store.ts, or justify it in INTENTIONALLY_UNRENDERED. An "
            "unhandled event returns '' and is indistinguishable from no event.")

    def test_the_backend_still_emits_something(self):
        """Guards the guard: if the grep stops matching, the test above passes
        vacuously and the alarm is off with nobody noticing."""
        assert len(emitted_events()) >= 10, (
            f"only found {len(emitted_events())} emitted events — the source "
            "grep has probably broken, so this whole file is now vacuous")

    def test_the_gui_renders_something(self):
        assert len(rendered_events()) >= 10


class TestTheEventsThatMatterMost:
    """Spend and refusals, named individually — a regression here is the kind a
    user notices as 'the run just stopped and said nothing'."""

    @pytest.mark.parametrize("event", ["budget_stop", "budget_warning",
                                       "cost_projection"])
    def test_money_events_are_rendered(self, event):
        assert event in rendered_events()

    @pytest.mark.parametrize("event", ["scenario_executed",
                                       "scenario_run_not_stored"])
    def test_scenario_outcomes_are_rendered(self, event):
        assert event in rendered_events()


class TestProgressUnitsAreRealEvents:
    def test_no_unit_event_is_invented(self):
        """UNIT_DONE drives the progress bar. A name in it that the backend never
        emits is a bar that can never fill; this caught five such names."""
        unknown = progress_unit_events() - emitted_events()
        assert not unknown, (
            f"UNIT_DONE names events the backend does not emit: {sorted(unknown)}")
