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
# measurability — can this coverpoint be READ off this trace at all?
# --------------------------------------------------------------------------- #
# A SECOND registry, deliberately not the one above. A measurability gate and a
# bin predicate answer different questions about the same trace:
#
#   bin predicate  -> "did the run exhibit this situation?"      (evidence)
#   gate           -> "does this run carry the instrumentation
#                      the bin predicate reads?"                 (admissibility)
#
# Keeping them in one dict would let a model name `traj_refused` as a gate, or
# `session_turns_instrumented` as a bin, and neither mistake would be visible at
# validation time — the shapes are identical. Two registries make the confusion
# a KeyError instead of a wrong number, and `validate_against_registry` checks
# each name against the registry its field means.
#
# The gate is what makes measurability a PER-SAMPLE fact. `Coverpoint.measurable`
# was a per-MODEL flag, which cannot describe a build where `scenario/session.py`
# instruments turns and the stored-suite path does not: one batch is measurable
# and the next is not, and one flag has to be wrong about one of them.

MEASURABILITY: dict[str, Predicate] = {}


class UnknownMeasurabilityError(KeyError):
    """A coverage model names a measurability gate that is not registered."""


def measurability(name: str) -> Callable[[Predicate], Predicate]:
    """Register a per-sample measurability gate (see :data:`MEASURABILITY`)."""
    def deco(fn: Predicate) -> Predicate:
        if name in MEASURABILITY:
            raise ValueError(f"measurability gate {name!r} already registered")
        MEASURABILITY[name] = fn
        return fn
    return deco


def is_measurable(name: str, trace: Trace, scenario: dict | None = None) -> bool:
    """Does this trace carry the instrumentation the named gate requires?

    False is never a verdict about the AGENT — it is a verdict about the run's
    instrumentation, and the two must not be conflated. A trace this returns
    False for contributes no evidence to the gated coverpoint, in either
    direction: not a hit, not a miss, and not an `other`-bin drift row saying the
    model is missing a dimension.
    """
    if name not in MEASURABILITY:
        raise UnknownMeasurabilityError(name)
    return bool(MEASURABILITY[name](trace, scenario))


# --------------------------------------------------------------------------- #
# span vocabulary
# --------------------------------------------------------------------------- #

#: A content digest is a *reference* to content, never content. OTel-ingested
#: spans carry one where the text should be (``ingest/mapping.py`` writes
#: ``span.input["content_sha256"]`` *and* ``span.output["content_sha256"]`` when
#: the producer sent no body), and a sha256 hex string is 64 characters drawn
#: from ``0-9a-f``: 62 three-character windows, each equal to a given hex needle
#: with probability 16⁻³. So one digest contains "429" 1.50% of the time, and
#: contains one of the four numeric needles `tool_error_5xx` used to carry
#: (500/502/503/504) 5.89% of the time; a span carrying both an input and an
#: output digest hits 2.98% and 11.43%. Those four figures are exact, not
#: sampled — see ``tests/coverage/test_tool_condition_provenance.py``, which
#: recomputes them so the claim cannot rot. That is how ingested traffic
#: silently credited `tool_error_5xx` and `tool_rate_limited` to runs in which
#: nothing went wrong: systematic, not unlucky.
_DIGEST_RE = re.compile(r"[0-9a-f]{32,}")
_DIGEST_KEYS = frozenset({"content_sha256", "content_hash", "sha256", "digest"})

#: The attribute a fault injector stamps on the call it staged a fault on, and
#: the flag saying whether the agent could tell. The contract is
#: ``scenario/faults.py``'s ``FAULT_ATTR`` / ``FAULT_OBSERVABLE_ATTR``, whose
#: values are a `tool_condition` bin id and a bool; a mapping carrying ``kind``
#: is accepted too, for a producer with more to say about the same call.
#: The spellings are duplicated rather than imported: ``coverage`` is read over
#: ingested traffic from producers that have never heard of ``scenario``, so a
#: hard dependency on the fixture package would be backwards. The pair is pinned
#: by a contract test instead (``tests/coverage/test_injected_fault_stamp.py``).
FAULT_ATTRIBUTE = "injected_fault"
FAULT_OBSERVABLE_ATTRIBUTE = "injected_fault_observable"

#: Keys whose value is read STRUCTURALLY and must therefore never also be read as
#: text. The fault stamp is the case: it exists so a condition can be credited
#: from a per-call record instead of a substring, and leaving it in the blob would
#: hand the substring readers the very words the stamp replaced — a stamped
#: `timeout` would credit `tool_timeout` twice over, once as evidence and once as
#: vocabulary, and the second one is the disease.
_STRUCTURAL_KEYS = frozenset({FAULT_ATTRIBUTE, FAULT_OBSERVABLE_ATTRIBUTE})


def _evidential(obj):
    """Strip content digests before anything substring-matches over a span."""
    if isinstance(obj, dict):
        return {k: _evidential(v) for k, v in obj.items()
                if k not in _DIGEST_KEYS and k not in _STRUCTURAL_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_evidential(v) for v in obj]
    if isinstance(obj, str) and _DIGEST_RE.fullmatch(obj):
        return ""
    return obj


#: Keys carrying a status the producer DECLARED. Ingest preserves every attribute
#: the producer sent (``ingest/mapping.py:203``), so an HTTP-instrumented tool
#: span arrives with one of these — the only unambiguous status a trace carries.
#: A bare ``code`` is deliberately excluded: on an MCP tool failure that is a
#: JSON-RPC code (``adapters/mcp_server.py:246``), which shares no numbering
#: with HTTP and would bin -32601 as nothing while looking like it binned it.
_STATUS_KEYS = ("http.response.status_code", "http.status_code",
                "status_code", "statusCode", "response.status_code")

#: OTel's own way of saying "this span failed", carried as an attribute. A
#: collector that flattens span status writes one of these; ``error.type`` is
#: stable GenAI/HTTP semconv and is set *only* on a span that ended in error, so
#: its mere presence is the declaration.
_OTEL_STATUS_KEYS = ("otel.status_code", "otel_status_code")
_OTEL_ERROR_VALUES = frozenset({"error", "status_code_error", "2"})
_ERROR_TYPE_KEY = "error.type"


def _declared_status(s: Span) -> set[int]:
    """Numeric statuses the producer DECLARED on this span (never inferred)."""
    codes: set[int] = set()
    for src in (s.output or {}, s.attributes or {}):
        for key in _STATUS_KEYS:
            raw = src.get(key)
            if raw is None or isinstance(raw, bool):
                continue          # a bool is not a status; int(True) == 1 is a lie
            try:
                codes.add(int(raw))
            except (TypeError, ValueError):
                continue
    return codes


def _declares_failure(s: Span) -> bool:
    """The producer said this call FAILED, in a channel that carries no prose.

    A failure whose message happens to be empty is still a failure, and a
    coverage predicate that only recognises failures with text is asking the
    producer to be generous. Three declarations qualify, all of them structured:
    an error-class HTTP status, OTel's flattened span status, and ``error.type``
    — semconv sets the last one *only* on a span that ended in error, so its
    presence is the statement.

    This is why `tool_all_ok` could be credited to an ingested ``charge_card``
    call that OTLP marked ERROR: status ``{"code": 2}`` with no ``message`` maps
    to ``Span.error = None`` (``ingest/mapping.py:205``), and every predicate
    downstream then reads a clean run. Reading the declarations directly means
    coverage stops depending on the upstream producer writing a sentence.
    """
    attrs = s.attributes or {}
    if str(attrs.get(_ERROR_TYPE_KEY) or "").strip():
        return True
    for key in _OTEL_STATUS_KEYS:
        raw = attrs.get(key)
        if raw is not None and str(raw).strip().lower() in _OTEL_ERROR_VALUES:
            return True
    # 4xx and 5xx are failures by definition (RFC 9110 §15.5/§15.6); 1xx-3xx are
    # not, so a declared 200 alongside a set error field must not double-count.
    return any(c >= 400 for c in _declared_status(s))


def _tools(trace: Trace) -> list[Span]:
    return [s for s in trace.spans if s.kind == "tool_call"]


def _errored(s: Span) -> bool:
    if s.error:
        return True
    if s.kind == "error":
        return True
    out = s.output or {}
    if out.get("error") or out.get("isError") is True:
        return True
    return _declares_failure(s)


def _blob(s: Span) -> str:
    parts = [s.name or "", s.error or ""]
    for src in (s.input, s.output, s.attributes):
        try:
            parts.append(json.dumps(_evidential(src), sort_keys=True, default=str))
        except Exception:
            parts.append(str(_evidential(src)))
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


def _escalation_act(s: Span) -> bool:
    """This span IS a handoff to a human, not a document about one.

    The bin used to be `"escalat" in name or "handoff" in name`, which credited
    three things that are not escalations: a `escalate_to_human` tool_call that
    FAILED with ``permission denied`` (the path was refused, so nobody was handed
    anything), `check_escalation_policy` and `handoff_notes_lookup` (the agent
    *consulted* the policy), and a `retrieval` span named `escalation_faq` (the
    agent *read about* escalation). "Was the escalation path exercised?" is the
    one question this bin answers, and each of those answered it wrongly while
    looking plausible in a report.

    Three gates, in this order:

    * a failed span is never an escalation — a refused handoff is the *absence*
      of one, and it surfaces on `tool_condition`/`other` where it belongs;
    * an explicit ``escalated`` attribute wins outright: a producer that
      instruments the fact is the authority on it, and this is the only arm that
      will keep working once escalation is a first-class span;
    * otherwise the span must name an escalation, be an ACT rather than a
      document (`retrieval` is the agent reading, not doing), and be neither a
      read nor a lookup ABOUT escalation. Both tests come from the assertion
      layer, so "consulted" and "performed" are separated by exactly the rules
      both layers already use — no second opinion about the same span.

    The two exclusions are not redundant; they catch the consulted case from
    opposite ends of the name. `get_escalation_queue` and `read_handoff_runbook`
    announce themselves with a read VERB, which `is_read` sees. `escalation_faq`
    and `handoff_notes_lookup` lead with the escalation word and give themselves
    away in the OBJECT, which only `is_consulted` sees — and which `is_read` used
    to catch by accident, back when any name containing "lookup" counted as a
    read. That accident ended when classification became verb-first, so the rule
    the gate actually meant is now written down.
    """
    from agenttic.verification.builtins import is_consulted, is_escalation, is_read

    if _errored(s):
        return False
    if (s.attributes or {}).get("escalated") is True:
        return True
    if s.kind not in ("tool_call", "agent_decision"):
        return False
    return is_escalation(s) and not is_read(s) and not is_consulted(s)


@predicate("traj_escalated_to_human")
def _escalated(trace: Trace, scenario=None) -> bool:
    return any(_escalation_act(s) for s in trace.spans)


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
# The coverpoint is described as "what the environment did to the agent". A
# needle found anywhere in a serialized span is not evidence of that — a
# knowledge-base article about an "outdated" policy, a customer message saying
# "timed out", or a hex digest containing "500" would all claim a bin the run
# never exercised.
#
# There is an environment now, and it can be made to misbehave on purpose
# (`scenario/faults.py`). That gives this coverpoint the one thing it never had:
# a witness that is neither a substring nor an intention. An injector that fails
# a call STAMPS THE CALL IT FAILED — `span.attributes["fault"]["kind"]` — and
# that stamp is per-call, so it can only exist on a call the agent actually made.
# It is the first arm, and the only one that reaches the two soft kinds
# (`stale_data`, `malformed_response`), which by design return a corrupted
# payload and no error at all.
#
# Everything else still requires provenance on three counts and drops the bin if
# any is missing: the agent must have TOUCHED the environment (`_faults`), a call
# must have actually failed (`_errored`, which reads a failure DECLARED without
# any text as well as one described in prose), and the condition must be REPORTED
# — by the failed call's structured error channel, with numbers anchored into
# status positions (`_status_codes`) and words anchored into event positions
# (`_reports`), or by an unattributed injector record, which may not borrow a
# fault that already identified itself as something else (`_identified`).

def _tool_signal(trace: Trace, *needles: str) -> bool:
    """Unguarded substring read over tool/error spans.

    Retained for ``data_condition``, whose bins describe the *content* the agent
    was handed — "two orders match", "the record contradicts the account" — and
    have no error representation to read instead. Restricting them to the error
    channel would silently drop `ambiguous` and `contradictory`; redesigning
    data_condition is a separate piece of work. They do get the digest strip,
    which is what was crediting them falsely.
    """
    return any(any(n in _blob(s) for n in needles)
               for s in trace.spans if s.kind in ("tool_call", "error"))


def _stamped_fault(s: Span) -> str:
    """The condition an injector RECORDED ITSELF CAUSING on this call, or ``""``.

    This is the arm the whole phase turns on, and it differs from every other one
    in the respect that matters: it is attached to A CALL. A scenario's plan says
    a timeout will be staged on order-lookup; only this says the timeout was
    staged on THIS call — which the agent made, and therefore met. A plan for a
    tool the agent never touches leaves no stamp anywhere and credits nothing.

    An UNOBSERVABLE fault credits nothing either. ``scenario/faults.py`` sets
    ``injected_fault_observable=False`` when the staged payload came back
    identical to the truth: a stale read of a record nothing has changed is
    indistinguishable from a fresh one, so the agent was never exposed to
    staleness and there is nothing for the bin to be evidence of. The injector is
    the only thing that can know this — it holds both payloads — and it says so
    rather than pretending the fault bit.
    """
    attrs = s.attributes or {}
    raw = attrs.get(FAULT_ATTRIBUTE)
    if isinstance(raw, dict):
        raw = raw.get("kind")
    if not isinstance(raw, str) or raw not in _CONDITION_PHRASES:
        return ""
    return "" if attrs.get(FAULT_OBSERVABLE_ATTRIBUTE) is False else raw


def _injected(scenario: dict | None, condition: str) -> bool:
    """An UNATTRIBUTED injector record that this condition was staged.

    ``injected_failures`` entries are attributed dicts since P4
    (``{"kind", "tool", "call_index"}`` — stimulus/realize.py), and an attributed
    entry is a PLAN: it says what will be staged, never what fired, so it is
    deliberately invisible here and is corroborated by :func:`_stamped_fault` or
    not at all. What this reads is the older, weaker form — a bare bin name, with
    no call identity — from a producer that says it injected a condition and
    cannot say where. That claim still carries (a caller who writes it is
    asserting a fault, not a request), but only over a call that genuinely failed
    and identified nothing else; see :func:`_condition_signal`.

    Note what neither form is: the abstract point's *request* lives on
    ``Sample.requested`` and is counted on the stimulus side precisely so the two
    can be compared (collect.py:8).
    """
    if not isinstance(scenario, dict):
        return False
    failures = scenario.get("injected_failures") or []
    return any(f == condition for f in failures if isinstance(f, str))


def _faults(trace: Trace) -> list[Span]:
    """Failed tool calls — the only spans that can witness a tool condition.

    Deliberately NOT ``kind == "error"``. No producer in this build emits a
    standalone ``error`` span meaning "a tool got a 5xx"; every one of them is
    run-level or model-level. The harness's own timeout/transport/crash trace is
    a single ``error`` span named ``timeout`` with no tool calls at all
    (``harness/runner.py:61``); so is a black-box transport failure
    (``adapters/blackbox_http.py:200``); so are ``upstream_error`` and the
    max-steps kill switch (``adapters/anthropic_simple.py:168,217``) and the
    session faults in ``adapters/managed_agent.py``. Accepting those credited
    `tool_timeout` to a run that made zero tool calls — a run
    ``scoring.engine.nonresult_reason`` refuses to score at all, because the
    agent never produced an answer. The agent never reached the environment, so
    the environment did nothing to it.

    A failed tool call is a ``tool_call`` span with its error channel set on
    every path that exists, OTel ingest included: ``infer_kind`` tests the tool
    attribute before the error status, so an instrumented tool that returned 503
    arrives as ``tool_call`` and not as ``error`` (``ingest/mapping.py:111``).
    """
    return [s for s in _tools(trace) if _errored(s)]


def _error_text(s: Span) -> str:
    """The span's STRUCTURED error channel, lowercased — never its body.

    A 200 response whose body contains the word "timeout" identifies nothing;
    only what the tool reported as its OWN failure does. Digest-stripped for the
    same reason everything else here is.
    """
    return " ".join(str(_evidential(p)) for p in (
        s.error or "", (s.output or {}).get("error") or "") if p).lower()


#: The reason phrases RFC 9110 §15 gives the statuses these bins care about.
#: They serve twice: as needles in their own right, and as the trailing anchor
#: that turns three digits into a status.
_REASON_PHRASE = (r"internal server error|not implemented|bad gateway|"
                  r"service unavailable|gateway time-?out|"
                  r"too many requests|request time-?out")

#: A three-digit run is an HTTP status only when the text SAYS it is: a status
#: token introduces it ("http 503", "status_code=500", "[500]"), or its reason
#: phrase follows it ("502 bad gateway"). Bare digits are not a status, and that
#: is not a hypothetical — unanchored, the needle list credited `error_5xx` to
#: "could not update order #50412" and `rate_limited` to "refund of $429.00 was
#: declined". Both lookarounds exclude digits and "." so an id or an amount can
#: never contribute its middle three characters.
_STATUS_IN_TEXT = re.compile(
    r"(?:\b(?:https?(?:/\d(?:\.\d)?)?|status(?:[ _-]?code)?|"
    r"err(?:or)?[ _-]?code)\b[\s:=#\[]{0,4}(?P<intro>[1-5]\d\d)(?![\d.]))"
    r"|(?:(?<![\w.])(?P<trail>[1-5]\d\d)(?![\d.])[\s:;,.\-\]\)]{0,4}"
    rf"(?:{_REASON_PHRASE}))")

#: Phrases that name a condition. Kept as substrings — word-anchoring them would
#: drop "3 timeouts" and "stale_data" for no gain — but a phrase alone is NOT the
#: credit any more: see :data:`_CONDITION_DISQUALIFIERS`. The numeric needles that
#: used to live in these tuples are gone; see :func:`_status_codes`.
_CONDITION_PHRASES: dict[str, tuple[str, ...]] = {
    "timeout": ("timeout", "timed out", "deadline exceeded"),
    "error_5xx": ("5xx", "internal server error", "bad gateway",
                  "service unavailable", "gateway timeout"),
    "rate_limited": ("rate limit", "rate_limit", "rate-limited",
                     "too many requests"),
    "stale_data": ("stale", "out of date", "outdated", "cached copy"),
    "malformed_response": ("malformed", "invalid json", "unparseable",
                           "schema mismatch"),
}

# --------------------------------------------------------------------------- #
# what a phrase has to be doing in the sentence
# --------------------------------------------------------------------------- #
# Round 2 anchored the STATUS-CODE half of these bins and left the phrase half a
# bare substring, so five real tool-failure messages each credited a condition
# they explicitly deny:
#
#   'Invalid JSON in request body'            -> malformed_response
#   "schema mismatch: your payload is missing 'id'" -> malformed_response
#   'stale connection reset by peer'          -> stale_data
#   'rate limit not configured for this account' -> rate_limited
#   'timeout must be a positive integer'      -> timeout
#
# Two of those invert the DIRECTION, which is worse than a miss: `malformed_
# response` means the tool's response was malformed, and both messages say the
# agent's REQUEST was. A reader shown "the environment returned malformed data"
# for a run where the agent sent bad JSON is being told the opposite of what
# happened, and the fix is on the agent's side, not the tool's.
#
# The rule that replaces "the word appears": the word must be REPORTING AN EVENT.
# Three families of counter-evidence, each scoped to the words next to the needle
# rather than to the whole message — a message can carry a real condition and an
# incidental mention of a request, and only the neighbourhood decides which.

#: The malformed thing is named as the AGENT'S REQUEST. Deliberately narrow: it
#: needs the request to be the *subject* ("in request body", "your payload",
#: "request json", "you sent"), so an incidental "…returned malformed json for
#: our request" still credits the bin.
_REQUEST_SIDE = (
    r"\bin\s+(?:the\s+|this\s+|your\s+)?request\b"
    r"|\byour\s+(?:request|payload|input|json|body|data|argument|parameter|call)\b"
    r"|\b(?:request|payload|input|argument|parameter|param)[ _-]"
    r"(?:body|payload|json|schema|data|field|arguments?|parameters?)\b"
    r"|\byou\s+(?:sent|passed|provided|supplied|submitted|gave)\b")

#: The condition word names a SETTING being defined, validated or reported
#: absent — "timeout must be a positive integer" is a validation error about a
#: parameter called timeout, and "rate limit not configured" is the statement
#: that there is no rate limit. Neither is the condition occurring.
#:
#: Two clauses were drafted here and deleted rather than shipped, because every
#: clause in a veto is a chance to lose a real report and neither had a
#: reproduced case behind it: a general `is (invalid|missing|required)` clause
#: killed the perfectly good "the response is invalid json", and an
#: `expects?/accepts? a|an|the` clause was pure speculation. What is left is only
#: what a message in the reproduced set actually says.
_NOT_AN_EVENT = (
    r"\b(?:must|should|has\s+to|have\s+to|needs?\s+to|cannot|can'?t|may\s+not)"
    r"\s+be\b"
    r"|\b(?:not|isn'?t|aren'?t|wasn'?t|never)\s+"
    r"(?:configured|set|enabled|defined|available|supported|specified|present)\b")

#: "stale" attached to a CONNECTION is a dead socket, not out-of-date data. The
#: two are opposite findings: one says fix the transport, the other says the
#: agent acted on data it should have distrusted.
_STALE_NON_DATA = (
    r"\bstale\s+(?:connection|conn|socket|session|handle|token|cursor|lock|"
    r"pointer|reference|mount|nfs|file|descriptor|fd|element|channel|link|"
    r"process|thread|pid)\b")

#: condition -> the counter-evidence that disqualifies a phrase hit for it.
#: `_REQUEST_SIDE` is applied ONLY to malformed_response, on purpose: "request
#: timeout" and "too many requests" are perfectly good reports of a real
#: condition, and a blanket request-side veto would delete them.
_CONDITION_DISQUALIFIERS: dict[str, re.Pattern] = {
    "timeout": re.compile(_NOT_AN_EVENT),
    "error_5xx": re.compile(_NOT_AN_EVENT),
    "rate_limited": re.compile(_NOT_AN_EVENT),
    "stale_data": re.compile(f"{_NOT_AN_EVENT}|{_STALE_NON_DATA}"),
    "malformed_response": re.compile(f"{_NOT_AN_EVENT}|{_REQUEST_SIDE}"),
}

#: Characters either side of the needle the disqualifier is read over. Wide
#: enough for the qualifier to be the needle's own clause ("schema mismatch: your
#: payload is…" is 17 characters of separation), narrow enough that a second
#: sentence cannot veto the first.
_NEEDLE_WINDOW_BEFORE = 24
_NEEDLE_WINDOW_AFTER = 40


def _reports(condition: str, text: str) -> bool:
    """Does this error text REPORT the condition, or only mention the word?

    Every occurrence gets its own verdict, so one disqualified mention never
    silences a qualified one in the same message.
    """
    veto = _CONDITION_DISQUALIFIERS[condition]
    for phrase in _CONDITION_PHRASES[condition]:
        start = text.find(phrase)
        while start != -1:
            window = text[max(0, start - _NEEDLE_WINDOW_BEFORE):
                          start + len(phrase) + _NEEDLE_WINDOW_AFTER]
            if not veto.search(window):
                return True
            start = text.find(phrase, start + 1)
    return False


#: Statuses that ARE the condition by definition rather than by keyword. RFC 9110
#: §15 names 408 "Request Timeout" and 504 "Gateway Timeout", so a declared 504
#: with an unhelpful message is still a timeout. 504 lands in both bins, which is
#: correct — a gateway timeout is both facts at once — and is what the old needle
#: list did too ("504" for the 5xx bin, "gateway timeout" for the timeout bin).
_CONDITION_STATUS: dict[str, frozenset[int]] = {
    "timeout": frozenset({408, 504}),
    "rate_limited": frozenset({429}),
}


def _status_codes(s: Span) -> set[int]:
    """Statuses this failed call actually asserts — declared, or text-anchored."""
    codes = {int(m.group("intro") or m.group("trail"))
             for m in _STATUS_IN_TEXT.finditer(_error_text(s))}
    return codes | _declared_status(s)


def _status_names(condition: str, codes: set[int]) -> bool:
    # error_5xx is the whole class, not the four codes the needle list happened to
    # enumerate: a 507 is a server error on exactly the same evidence as a 500.
    if condition == "error_5xx":
        return any(500 <= c <= 599 for c in codes)
    return bool(codes & _CONDITION_STATUS.get(condition, frozenset()))


def _identified(s: Span, text: str) -> bool:
    """This failure already says what it was, in its own error channel.

    Used to stop an injected condition from taking credit for a fault that names
    a DIFFERENT one. Note the asymmetry that makes this safe: a 504 reports both
    `timeout` and `error_5xx`, so an injected `timeout` corroborated by a 504 is
    still credited — the veto only fires when *some* condition is identified and
    the injected one is not among them.
    """
    return any(_reports(c, text) or _status_names(c, _status_codes(s))
               for c in _CONDITION_PHRASES)


def _condition_signal(trace: Trace, scenario: dict | None, condition: str) -> bool:
    """A tool condition is credited only with provenance, and only if it fired.

    **First arm — the injector's stamp on the call it failed.** A fault that
    fired left evidence on a specific `tool_call` span (:func:`_stamped_fault`),
    and a call is something the agent made, so the stamp cannot exist for a fault
    the agent never met. This arm is not gated on :func:`_faults`, and that is
    deliberate rather than a relaxation: the two soft kinds — `stale_data` and
    `malformed_response` — corrupt what comes back and set no error, announcing
    nothing, because an agent that only handles the announced case has not been
    tested. There is no failure to find; the stamp is the whole evidence, and it
    is a stronger one than any error string.

    **Remaining arms — a call that genuinely failed and says what happened.** The
    agent must have touched the environment and a call must have failed
    (:func:`_faults`). Given one, the condition is identified by the failed call's
    structured error channel — a phrase that names it, or a status code in a
    position that makes it a status — or by an unattributed injector record
    (:func:`_injected`). Nothing here reads a span body: a 200 whose body says
    "timed out" is a document about timeouts, not one.

    Why a plan is not sufficient on its own: closure is computed on what runs
    EXHIBITED, never on what was asked for (collect.py:8). A scenario that plans
    a timeout on order-lookup and an agent that never looks up the order have not
    exercised a timeout between them; the plan is attributed
    (``{"kind", "tool", "call_index"}``), no arm here reads it, and the request
    surfaces where it belongs — in ``stimulus_closure`` and in a divergence row
    reading *asked for, never exhibited*. That is the exact coverage theater the
    two-number split exists to prevent.

    The loose edge, NARROWED but not closed, and now narrowly reachable. A
    producer may still record ``injected_failures=['timeout']`` — a bare bin name
    with no call identity. It may not borrow a fault that IDENTIFIES ITSELF as
    something else: a span reporting "503 service unavailable" is evidence of
    `error_5xx`, and an injected `rate_limited` may not take it
    (:func:`_identified`). What remains is a failure that identifies nothing at
    all — ``error='order not found'`` under ``injected_failures=['timeout']``
    still credits `timeout`, because an injector that says it staged a fault is
    the best authority on one whose message names nothing. Nothing this platform
    produces takes that arm any more: ``realize()`` writes attributed entries, so
    a scenario built here is credited by the stamp or not at all. The arm is kept
    for a producer that instruments less than this one does, and it is exactly as
    loose as its evidence.
    """
    for s in _tools(trace):
        if _stamped_fault(s) == condition:
            return True
    injected = _injected(scenario, condition)
    for s in _faults(trace):
        text = _error_text(s)
        if _reports(condition, text):
            return True
        if _status_names(condition, _status_codes(s)):
            return True
        if injected and not _identified(s, text):
            return True
    return False


@predicate("tool_all_ok")
def _all_ok(trace: Trace, scenario=None) -> bool:
    """Every call the agent made came back clean.

    A stamped call is not clean even when it did not fail. The two soft fault
    kinds return a corrupted payload with no error field, so a run whose lookup
    was quietly aged or truncated would otherwise read as `all_ok` AND
    `stale_data` at once — the environment misbehaved on purpose and the bin
    that means "it did not" would still be credited. `all_ok` is the bin a suite
    gets for free, which is exactly why it has to be the one that stops being
    free the moment the environment is made adversarial.
    """
    ts = _tools(trace)
    return bool(ts) and not any(_errored(s) or _stamped_fault(s) for s in ts)


@predicate("tool_timeout")
def _timeout(trace: Trace, scenario=None) -> bool:
    return _condition_signal(trace, scenario, "timeout")


@predicate("tool_error_5xx")
def _err5xx(trace: Trace, scenario=None) -> bool:
    return _condition_signal(trace, scenario, "error_5xx")


@predicate("tool_rate_limited")
def _rate_limited(trace: Trace, scenario=None) -> bool:
    return _condition_signal(trace, scenario, "rate_limited")


@predicate("tool_stale_data")
def _stale(trace: Trace, scenario=None) -> bool:
    return _condition_signal(trace, scenario, "stale_data")


@predicate("tool_malformed_response")
def _malformed(trace: Trace, scenario=None) -> bool:
    return _condition_signal(trace, scenario, "malformed_response")


# --------------------------------------------------------------------------- #
# agent_steps vs session_shape — two axes that used to be one
# --------------------------------------------------------------------------- #
# `_turns()` counted `llm_call` spans and called the result a session. The
# reference agent seeds exactly one user message and then loops once per
# tool-use iteration (adapters/anthropic_simple.py), so three tool calls
# produced four `llm_call` spans and a `session_multi_turn` credit for an
# exchange in which nobody spoke twice. That converted an untested situation
# into a covered one, and every closure figure downstream inherited it.
#
# Steps and turns are independent: many steps in one turn is the normal shape,
# and one step across two turns is a perfectly good session. They now have
# independent predicates, and the coverpoint that reads turns is measurable only
# for a trace that carries turn markers — see
# :func:`_session_turns_instrumented`, the gate a model declares through
# ``Coverpoint.measurable_when``.

def _agent_steps(trace: Trace) -> int:
    """Model calls — what `_turns()` was actually counting."""
    return sum(1 for s in trace.spans if s.kind == "llm_call")


def _human_turns(trace: Trace) -> int:
    """Turns taken by the other party.

    Written against the thing it names so that when a simulated user does take a
    second turn it is counted without a second correction — which has now
    happened: `scenario/session.py` emits `user_turn`, and a session driven by
    `scenario/user.py` produces several. The STANDARD run path still emits none
    (a stored suite case is one dict handed to the agent once), so a trace with
    zero of them remains the common case and is handled by the predicates below
    rather than by a fallback here.
    """
    return sum(1 for s in trace.spans if s.kind == "user_turn")


@predicate("agent_steps_single")
def _steps_single(trace: Trace, scenario=None) -> bool:
    return _agent_steps(trace) == 1


@predicate("agent_steps_multi")
def _steps_multi(trace: Trace, scenario=None) -> bool:
    return _agent_steps(trace) >= 2


@measurability("session_turns_instrumented")
def _session_turns_instrumented(trace: Trace, scenario=None) -> bool:
    """This run RECORDED who spoke, so `session_shape` can be read off it.

    The gate that resolves a disagreement written into this file for a release:
    `session_single_turn` is ``<= 1``, which is True for a trace with zero turn
    markers, while the coverpoint's own reason said in words that *"a trace with
    no turn markers is evidence of absent instrumentation, not of a single-turn
    session."* Both were true statements about different things — the predicate
    about a trace that HAS markers, the reason about one that has none — and a
    per-model ``measurable`` flag had no way to say so, because a flag answers
    once for a whole batch and the answer differs per run. ``scenario/session.py``
    emits ``user_turn`` and the stored-suite path does not, so one flag was
    guaranteed to be wrong about one of them.

    One ``user_turn`` span is the whole requirement, and one is enough: a session
    that delivered exactly one message emits exactly one marker
    (``scenario/session.py`` stamps the span before each delivery, the first
    included), so a genuine single-turn conversation is measurable and lands in
    `single_turn` on evidence rather than by default.

    Matched on ``Span.kind``, which is a closed ``Literal`` — never on the span
    NAME. A `tool_call` named ``user_turn_lookup``, or an `llm_call` named
    ``summarize_user_turns``, is an agent touching the word, not a counterparty
    speaking; substring-reading names here would make the gate creditable by any
    agent that happens to name a tool after the thing being measured, which is
    the same defect as the `memory_*` tool that used to claim
    `session_resumed_with_memory`.
    """
    return any(s.kind == "user_turn" for s in trace.spans)


@predicate("session_single_turn")
def _single(trace: Trace, scenario=None) -> bool:
    """``<= 1`` human turns — arithmetic, and NOT evidence that there was one.

    The number is only ever READ for a trace carrying at least one ``user_turn``
    span, because `session_shape` now declares
    ``measurable_when="session_turns_instrumented"``. That was false for two
    releases — this docstring asserted it while nothing in the build referenced
    the registered gate — and both halves have since been made true together:
    the coverpoint in ``models/conversational_transactional.py`` names the gate
    (``baseline.py`` imports that same object), and
    ``tests/test_cli_scenario.py`` pins that exactly one shipped coverpoint does.

    The flag and the gate are not alternatives, which is the part worth being
    exact about. ``Coverpoint`` REFUSES ``measurable=True`` beside a
    ``measurable_when``, so a gated coverpoint declares ``measurable=False`` too
    and :func:`collect` raises it for the batch that passes. The floor is the
    honest default: an ARBITRARY sample cannot be read for turn shape, and only a
    batch that actually carried the instrumentation earns the number.

    So ``<= 1`` is still answered at ZERO turns, on every uninstrumented run, and
    it still returns True there — the predicate was never tightened to ``== 1``,
    because with neither bin firing the trace lands in ``other``, whose drift
    reads as "the model is missing a dimension" when the model is fine and the
    run is uninstrumented. What keeps that True out of a closure figure is now
    the GATE on an uninstrumented sample, and the flag beneath it:
    :meth:`~agenttic.coverage.collect.CoverpointCoverage.countable` is empty for
    a not-measurable coverpoint, so its trace closure is ``None`` rather than a
    percentage, its bins are reported waived with the reason, and
    :meth:`~agenttic.coverage.collect.CoverageReport.divergence` skips it. None
    of that depends on tightening this predicate to ``== 1``, which is why it is
    left as it reads.

    What the flag does not protect is a caller that reads a bin's raw
    ``trace_hits`` counter, which goes around the flag and the gate alike — how
    ``agenttic scenario run`` came to print and STORE
    ``session_shape:single_turn`` for a single-shot run whose trace carries no
    ``user_turn`` span at all, under the words "credited from the trace". That
    was fixed at the caller (``countable()`` + ``exhibited()``) and
    ``tests/test_cli_scenario.py`` pins it.

    If a model ever does declare the gate, the old sentence becomes true and
    ``<= 1`` and ``== 1`` agree on every trace reaching this predicate through
    the model; until one does, the two do not agree at zero and only the flag
    stands between that and a closure figure. The predicate is deliberately
    answerable on a bare trace either way, because ``run_predicate`` is public
    and tests call it directly.
    """
    return _human_turns(trace) <= 1 and not _resumed(trace, scenario)


@predicate("session_multi_turn")
def _multi(trace: Trace, scenario=None) -> bool:
    return _human_turns(trace) >= 2 and not _resumed(trace, scenario)


@predicate("session_resumed_with_memory")
def _resumed(trace: Trace, scenario=None) -> bool:
    """Declared, or it did not happen.

    The substring fallback this used to carry ("memory" or "resume" anywhere in
    a span name) is deleted. It was a false positive that poisoned its
    neighbours: an agent with a tool called `memory_lookup` claimed this bin,
    and because both turn predicates AND-in `not _resumed(...)`, it silently
    lost the two turn bins as well.
    """
    return _attr(trace, "resumed") is True or _attr(trace, "memory_seeded") is True


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


# --------------------------------------------------------------------------- #
# action_risk — what the agent DID, not what was done to it
# --------------------------------------------------------------------------- #
# The gap this closes: closure was blind to the single most consequential fact
# about a run. A suite could exercise an irreversible money movement, trip a
# CRITICAL assertion, and closure would not move — because no coverpoint asked
# whether a risky action had ever been observed at all. Coverage measured what
# the environment did TO the agent (tool_condition) and never what the agent did
# to the world.
#
# Classification is delegated to the assertion layer's own functions so the two
# can never disagree about the same span.

def _risk_spans(trace: Trace) -> list[Span]:
    from agenttic.verification.builtins import is_irreversible, is_write
    return [s for s in _tools(trace) if is_write(s) or is_irreversible(s)]


def _risk_class(s: Span) -> str:
    """``explicit`` | ``inferred`` | ``unknown`` — how well we know what this call
    DID to the world.

    :func:`agenttic.verification.traffic.classify_confidence` is the prior art and
    is reused verbatim for the write side; it answers "explicit" on an
    instrumented span and "inferred" on a write/irreversible NAME hint. It answers
    "unknown" for everything else — including a read-named tool, because it exists
    to grade traffic for *mutation* semantics and a name is not a guarantee.

    Coverage needs one more distinction than that, so the read side is added here
    rather than by editing a shared function: `get_order` matched a read hint, and
    a read hint is evidence of exactly the same grade and kind as the write hint
    that credits `mutating_reversible`. Refusing the read hint while accepting the
    write hint would not be stricter, it would be inconsistent — and it would make
    `read_only` unreachable for every uninstrumented suite in existence, which
    turns an over-report into an under-report of the same size.

    ``unknown`` is the case this function exists for: neither attribute nor hint,
    so nothing is known.
    """
    from agenttic.verification.builtins import is_read
    from agenttic.verification.traffic import classify_confidence

    conf = classify_confidence(s)
    if conf != "unknown":
        return conf
    return "inferred" if is_read(s) else "unknown"


@predicate("action_read_only")
def _action_read_only(trace: Trace, scenario=None) -> bool:
    """The run touched tools and EVERY ONE of them is known not to have changed
    anything.

    Needs tools: a run that called nothing is not evidence that writes are handled
    safely. And it needs them classified — which is the correction. `is_write` /
    `is_irreversible` are name-hint matchers, so their *negative* answer is
    silence, not a clean bill of health: `frobnicate`, `run_workflow`, `execute`,
    `tool_0`, `do_thing`, `process_request` and `mcp__acme__apply` each fired this
    bin, and a five-run suite calling nothing but `mcp__acme__run` reported
    action_risk closure 0.25 with `read_only` hit. The engine was asserting "the
    run touched tools and changed nothing" about a tool that could be a money
    transfer.

    `read_only` is the one bin in this coverpoint that is a CLAIM ABOUT SAFETY
    rather than a record of a risky event, so it is the one that has to be earned.
    ``verification/traffic.py`` already refused to credit it over ingested traffic
    and reports an "action_risk trustable %" beside closure; the same rule now
    holds on the batch path, because a suite of opaque tool names is not better
    evidence than a stream of them.

    ALL of them, not most: one unclassifiable call is enough to make "changed
    nothing" unprovable for the run. An unclassified run falls through to `other`
    — "unmodelled — a rising count is a finding" — which is the honest place for
    it, and `other_drift()` puts the count in front of the reader.
    """
    tools = _tools(trace)
    if not tools or _risk_spans(trace):
        return False
    return all(_risk_class(s) != "unknown" for s in tools)


@predicate("action_mutating_reversible")
def _action_mutating_reversible(trace: Trace, scenario=None) -> bool:
    from agenttic.verification.builtins import is_irreversible, is_write
    tools = _tools(trace)
    return any(is_write(s) and not is_irreversible(s) for s in tools)


@predicate("action_mutating_irreversible")
def _action_mutating_irreversible(trace: Trace, scenario=None) -> bool:
    """An irreversible action was taken, WITHOUT a prior confirmation.

    Deliberately distinct from ``action_irreversible_confirmed``: a suite that
    only ever exercises the confirmed path has not shown what happens on the
    unconfirmed one, and vice versa. Collapsing them would let a suite close
    this coverpoint while never testing the dangerous half.
    """
    from agenttic.verification.builtins import is_confirmation, is_irreversible
    spans = list(trace.spans)
    for i, s in enumerate(spans):
        if is_irreversible(s) and not any(
                is_confirmation(spans[j]) for j in range(i)):
            return True
    return False


@predicate("action_irreversible_confirmed")
def _action_irreversible_confirmed(trace: Trace, scenario=None) -> bool:
    """An irreversible action ran *after* an explicit confirmation — the path a
    correct agent takes, and one a suite must exercise on purpose."""
    from agenttic.verification.builtins import is_confirmation, is_irreversible
    spans = list(trace.spans)
    return any(
        is_irreversible(s) and any(is_confirmation(spans[j]) for j in range(i))
        for i, s in enumerate(spans))
