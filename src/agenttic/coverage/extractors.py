"""Deterministic coverage extractors (SPEC-13 Step 59).

Pure predicates over ``(trace, scenario)``. No model calls, no network — these
run over traces you already have, which is what lets you state *what was never
exercised* today, for free.

**Trajectory coverage is the one almost nobody has.** Whether the recovery path
was exercised at all — `retry_after_error`, `recovered_from_tool_failure`,
`escalated_to_human`, `max_steps_hit` — is invisible to a pass rate: an agent can
score 100% having never once been made to recover from a tool failure.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from agenttic.schema.trace import Span, Trace

#: predicate(trace, scenario) -> bool. ``scenario`` is the realized scenario dict
#: when one exists (SPEC-13 M42); None for traces collected before stimulus.
Predicate = Callable[[Trace, dict | None], bool]

PREDICATES: dict[str, Predicate] = {}


class UnknownPredicateError(KeyError):
    """A coverage model references a predicate that is not registered."""


def predicate(name: str) -> Callable[[Predicate], Predicate]:
    """Register a deterministic coverage predicate (mirrors ``@check``)."""
    def deco(fn: Predicate) -> Predicate:
        if name in PREDICATES:
            raise ValueError(f"coverage predicate {name!r} already registered")
        PREDICATES[name] = fn
        return fn
    return deco


def run_predicate(name: str, trace: Trace, scenario: dict | None = None) -> bool:
    if name not in PREDICATES:
        raise UnknownPredicateError(name)
    return bool(PREDICATES[name](trace, scenario))


# --------------------------------------------------------------------------- #
# span vocabulary
# --------------------------------------------------------------------------- #

_ERROR_HINTS = ("timeout", "timed out", "rate limit", "429", "5xx", "500", "502",
                "503", "504", "malformed", "stale")


def _tools(trace: Trace) -> list[Span]:
    return [s for s in trace.spans if s.kind == "tool_call"]


def _errored(s: Span) -> bool:
    if s.error:
        return True
    if s.kind == "error":
        return True
    out = s.output or {}
    return bool(out.get("error") or out.get("isError") is True)


def _blob(s: Span) -> str:
    parts = [s.name or "", s.error or ""]
    for src in (s.input, s.output, s.attributes):
        try:
            parts.append(json.dumps(src, sort_keys=True, default=str))
        except Exception:
            parts.append(str(src))
    return " ".join(parts).lower()


def _attr(trace: Trace, key: str):
    for s in trace.spans:
        if key in (s.attributes or {}):
            return s.attributes[key]
    return None


# --------------------------------------------------------------------------- #
# trajectory — deterministic by construction, never provisional
# --------------------------------------------------------------------------- #

@predicate("traj_direct_answer")
def _direct(trace: Trace, scenario=None) -> bool:
    return not _tools(trace) and bool((trace.final_output or "").strip())


@predicate("traj_tool_then_answer")
def _tool_then_answer(trace: Trace, scenario=None) -> bool:
    return len(_tools(trace)) == 1 and bool((trace.final_output or "").strip())


@predicate("traj_multi_tool_chain")
def _multi_tool(trace: Trace, scenario=None) -> bool:
    return len(_tools(trace)) >= 2


@predicate("traj_retry_after_error")
def _retry_after_error(trace: Trace, scenario=None) -> bool:
    """A tool errored and the SAME tool was called again afterwards."""
    seen_error: set[str] = set()
    for s in trace.spans:
        if s.kind != "tool_call":
            continue
        name = (s.name or "").lower()
        if name in seen_error:
            return True
        if _errored(s):
            seen_error.add(name)
    return False


@predicate("traj_recovered_from_tool_failure")
def _recovered(trace: Trace, scenario=None) -> bool:
    """A tool failed, a later tool call succeeded, and the run still answered."""
    failed_at = next((i for i, s in enumerate(trace.spans)
                      if s.kind == "tool_call" and _errored(s)), None)
    if failed_at is None:
        return False
    later_ok = any(s.kind == "tool_call" and not _errored(s)
                   for s in trace.spans[failed_at + 1:])
    return later_ok and bool((trace.final_output or "").strip())


@predicate("traj_escalated_to_human")
def _escalated(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "escalated") is True:
        return True
    return any("escalat" in (s.name or "").lower() or "handoff" in (s.name or "").lower()
               for s in trace.spans)


_REFUSAL = re.compile(
    r"(?i)\b(i (can'?t|cannot|won'?t|am unable to)|unable to help|"
    r"not able to (help|do that)|i must decline|can'?t assist)")


@predicate("traj_refused")
def _refused(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "refused") is True:
        return True
    return bool(_REFUSAL.search(trace.final_output or ""))


@predicate("traj_max_steps_hit")
def _max_steps(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "max_steps_hit") is True:
        return True
    limit = _attr(trace, "max_steps")
    return bool(limit and trace.total_steps and trace.total_steps >= int(limit))


@predicate("traj_budget_exceeded")
def _budget(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "budget_exceeded") is True:
        return True
    cap = _attr(trace, "max_cost_usd")
    return bool(cap and trace.total_cost_usd and trace.total_cost_usd > float(cap))


# --------------------------------------------------------------------------- #
# tool_condition — what the environment did to the agent
# --------------------------------------------------------------------------- #

def _tool_signal(trace: Trace, *needles: str) -> bool:
    return any(any(n in _blob(s) for n in needles)
               for s in trace.spans if s.kind in ("tool_call", "error"))


@predicate("tool_all_ok")
def _all_ok(trace: Trace, scenario=None) -> bool:
    ts = _tools(trace)
    return bool(ts) and not any(_errored(s) for s in ts)


@predicate("tool_timeout")
def _timeout(trace: Trace, scenario=None) -> bool:
    return _tool_signal(trace, "timeout", "timed out", "deadline exceeded")


@predicate("tool_error_5xx")
def _err5xx(trace: Trace, scenario=None) -> bool:
    return _tool_signal(trace, "5xx", "500", "502", "503", "504",
                        "internal server error")


@predicate("tool_rate_limited")
def _rate_limited(trace: Trace, scenario=None) -> bool:
    return _tool_signal(trace, "rate limit", "rate_limit", "429", "too many requests")


@predicate("tool_stale_data")
def _stale(trace: Trace, scenario=None) -> bool:
    return _tool_signal(trace, "stale", "out of date", "outdated", "cached copy")


@predicate("tool_malformed_response")
def _malformed(trace: Trace, scenario=None) -> bool:
    return _tool_signal(trace, "malformed", "invalid json", "unparseable",
                        "schema mismatch")


# --------------------------------------------------------------------------- #
# session_shape
# --------------------------------------------------------------------------- #

def _turns(trace: Trace) -> int:
    return sum(1 for s in trace.spans if s.kind == "llm_call")


@predicate("session_single_turn")
def _single(trace: Trace, scenario=None) -> bool:
    return _turns(trace) <= 1 and not _resumed(trace, scenario)


@predicate("session_multi_turn")
def _multi(trace: Trace, scenario=None) -> bool:
    return _turns(trace) >= 2 and not _resumed(trace, scenario)


@predicate("session_resumed_with_memory")
def _resumed(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "resumed") is True or _attr(trace, "memory_seeded") is True:
        return True
    return any("memory" in (s.name or "").lower()
               or "resume" in (s.name or "").lower() for s in trace.spans)


# --------------------------------------------------------------------------- #
# data_condition — the shape of the data the agent was handed
# --------------------------------------------------------------------------- #

@predicate("data_entity_not_found")
def _not_found(trace: Trace, scenario=None) -> bool:
    return _tool_signal(trace, "not found", "not_found", "no such", "404",
                        "does not exist")


@predicate("data_missing_field")
def _missing(trace: Trace, scenario=None) -> bool:
    return _tool_signal(trace, "missing required", "missing field",
                        "required parameter", "is required")


@predicate("data_ambiguous")
def _ambiguous(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "data_condition") == "ambiguous":
        return True
    return _tool_signal(trace, "ambiguous", "multiple matches", "more than one match")


@predicate("data_contradictory")
def _contradictory(trace: Trace, scenario=None) -> bool:
    if _attr(trace, "data_condition") == "contradictory":
        return True
    return _tool_signal(trace, "contradict", "conflicting", "mismatch between")


@predicate("data_complete")
def _complete(trace: Trace, scenario=None) -> bool:
    """Complete = the run saw none of the degraded data conditions."""
    if not _tools(trace):
        return False
    return not any(f(trace, scenario) for f in
                   (_not_found, _missing, _ambiguous, _contradictory))
