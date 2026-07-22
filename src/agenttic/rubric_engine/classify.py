"""Automatic archetype classification (SPEC-9 Step 40).

Input is whatever the client provides — a business doc, an agent description,
and/or a sample of the agent's own traces. Output is a ranked list of
:class:`ArchetypeMatch`. Two evidence sources combine:

  (a) a *semantic* read of the description against each archetype's ``signals``.
      With an Anthropic ``client`` this is an LLM classifier; with none it
      degrades to a deterministic keyword classifier (same signals), so the
      pipeline — and its acceptance tests — run fully offline.
  (b) *trace-shape* evidence when traces exist: objective features (tool
      read/write ratio, turn count, state mutation, retrieval calls) that
      confirm or override the semantic read.

Multi-archetype agents are normal ("a research assistant that books travel"):
every archetype above ``threshold`` is returned, so synthesis (Step 41) can
compose their cores. If nothing clears the threshold the agent routes to
``custom`` — the Step-8 generator from a blank base (a candidate new archetype,
Step 43).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agenttic.rubric_engine.cores import SEED_ARCHETYPES
from agenttic.schema.archetype import Archetype
from agenttic.schema.trace import Trace

CUSTOM = "custom"

# Tool-name heuristics for the read/write split (objective, name-based). A write
# is any tool whose name implies a state change; everything else reads.
_WRITE_HINTS = ("create", "update", "delete", "write", "send", "book", "cancel",
                "refund", "charge", "pay", "insert", "remove", "patch", "commit",
                "push", "post", "put", "set", "modify", "issue", "submit",
                "provision", "deploy", "execute", "run")
_READ_HINTS = ("get", "list", "search", "lookup", "read", "fetch", "find",
               "query", "retrieve", "view", "describe", "check", "load")
_CODE_HINTS = ("git", "patch", "test", "compile", "lint", "build", "pytest",
               "diff", "repo", "apply_patch", "edit_file")


@dataclass
class ClassifyInputs:
    """Whatever the client can provide about the agent under test."""

    business_doc: str = ""
    agent_description: str = ""
    traces: list[Trace] = field(default_factory=list)

    def text(self) -> str:
        return f"{self.agent_description}\n{self.business_doc}".strip()


@dataclass
class ArchetypeMatch:
    """A ranked archetype hypothesis with its confidence and evidence source."""

    archetype_id: str
    confidence: float
    rationale: str
    source: Literal["llm", "keyword", "llm+trace", "keyword+trace", "trace", "none"]


# --------------------------------------------------------------------------- #
# (b) trace-shape features — objective, no LLM
# --------------------------------------------------------------------------- #

def _tool_calls(traces: list[Trace]) -> list[str]:
    return [s.name.lower() for t in traces for s in t.spans if s.kind == "tool_call"]


def trace_shape_features(traces: list[Trace]) -> dict:
    """Objective features of a trace sample. Empty/no-trace → all-zero features."""
    if not traces:
        return {"n_traces": 0, "tool_calls": 0, "reads": 0, "writes": 0,
                "read_write_ratio": None, "state_mutation": False,
                "retrieval_calls": 0, "avg_turns": 0.0, "code_ops": 0,
                "has_traces": False}
    names = _tool_calls(traces)
    reads = sum(1 for n in names if any(h in n for h in _READ_HINTS))
    writes = sum(1 for n in names if any(h in n for h in _WRITE_HINTS))
    code_ops = sum(1 for n in names if any(h in n for h in _CODE_HINTS))
    retrieval = sum(1 for t in traces for s in t.spans if s.kind == "retrieval")
    retrieval += sum(1 for n in names if any(h in n for h in ("search", "retrieve", "lookup_kb", "vector")))
    turns = [sum(1 for s in t.spans if s.kind == "llm_call") for t in traces]
    avg_turns = sum(turns) / len(turns) if turns else 0.0
    ratio = (reads / writes) if writes else (None if reads == 0 else float("inf"))
    return {
        "n_traces": len(traces), "tool_calls": len(names),
        "reads": reads, "writes": writes, "read_write_ratio": ratio,
        "state_mutation": writes > 0, "retrieval_calls": retrieval,
        "avg_turns": avg_turns, "code_ops": code_ops, "has_traces": True,
    }


def trace_affinity(feats: dict, archetypes: dict[str, Archetype]) -> dict[str, float]:
    """Signed, bounded per-archetype adjustment from trace shape (~[-0.15, +0.3])."""
    aff = {aid: 0.0 for aid in archetypes}
    if not feats.get("has_traces"):
        return aff
    writes, retrieval = feats["writes"], feats["retrieval_calls"]
    multi_turn = feats["avg_turns"] >= 2
    code_ops = feats["code_ops"]

    def bump(aid: str, v: float) -> None:
        if aid in aff:
            aff[aid] = max(-0.15, min(0.3, aff[aid] + v))

    if writes:                                   # state mutation
        bump("conversational_transactional", 0.15)
        bump("workflow_automation", 0.2)
    if multi_turn:
        bump("conversational_transactional", 0.1)
        bump("workflow_automation", 0.1)
    if retrieval:
        bump("retrieval_qa", 0.2)
        bump("research_analysis", 0.15)
    if code_ops:
        bump("coding", 0.25)
    if not writes and not retrieval and not code_ops:
        # a pure-reasoning trace nudges decision_support
        bump("decision_support", 0.1)
    return aff


# --------------------------------------------------------------------------- #
# (a) semantic read — keyword fallback + optional LLM
# --------------------------------------------------------------------------- #

def _signal_matches(text: str, signals: list[str]) -> int:
    low = text.lower()
    n = 0
    for sig in signals:
        s = sig.lower()
        if s in low:
            n += 1
        else:
            words = [w for w in s.split() if len(w) > 2]
            if words and all(w in low for w in words):
                n += 1
    return n


def _keyword_classify(text: str, archetypes: dict[str, Archetype]) -> dict[str, tuple[float, str]]:
    """Saturating confidence from matched signals: 1 - 0.6**matches."""
    out: dict[str, tuple[float, str]] = {}
    for aid, arch in archetypes.items():
        m = _signal_matches(text, arch.signals)
        conf = 1.0 - 0.6 ** m if m else 0.0
        out[aid] = (round(conf, 4), f"{m} signal(s) matched")
    return out


_LLM_SYSTEM = (
    "You classify AI agents into archetypes. Respond with ONLY JSON, no prose.")


def _llm_classify(text: str, archetypes: dict[str, Archetype],
                  client) -> dict[str, tuple[float, str]]:
    import json
    catalogue = "\n".join(
        f"- {aid}: {a.description} signals: {', '.join(a.signals[:6])}"
        for aid, a in archetypes.items())
    prompt = (
        "Given the agent description, score how strongly it matches EACH "
        "archetype from 0.0 to 1.0 (multiple can be high). Return "
        '{"scores": {"<archetype_id>": {"confidence": <0..1>, "why": "..."}}}\n\n'
        f"ARCHETYPES:\n{catalogue}\n\nAGENT DESCRIPTION:\n{text}")
    resp = client.messages.create(
        model=getattr(client, "_classify_model", "claude-sonnet-5"),
        max_tokens=1500, system=_LLM_SYSTEM,
        messages=[{"role": "user", "content": prompt}])
    raw = resp.content[0].text if hasattr(resp.content[0], "text") else resp.content[0]["text"]
    data = json.loads(raw)
    out: dict[str, tuple[float, str]] = {}
    for aid in archetypes:
        entry = data.get("scores", {}).get(aid, {})
        out[aid] = (float(entry.get("confidence", 0.0)), str(entry.get("why", "")))
    return out


# --------------------------------------------------------------------------- #
# combine
# --------------------------------------------------------------------------- #

def classify(inputs: ClassifyInputs, *, client=None, threshold: float = 0.5,
             archetypes: dict[str, Archetype] | None = None) -> list[ArchetypeMatch]:
    """Rank archetypes for the agent; route to ``custom`` if none clear
    ``threshold``. Semantic evidence + trace-shape evidence combine additively,
    clamped to [0, 1]."""
    archetypes = archetypes or SEED_ARCHETYPES
    text = inputs.text()
    if client is not None and text:
        semantic = _llm_classify(text, archetypes, client)
        base_src = "llm"
    else:
        semantic = _keyword_classify(text, archetypes)
        base_src = "keyword"

    feats = trace_shape_features(inputs.traces)
    aff = trace_affinity(feats, archetypes)

    matches: list[ArchetypeMatch] = []
    for aid, arch in archetypes.items():
        s_conf, why = semantic.get(aid, (0.0, ""))
        delta = aff.get(aid, 0.0)
        conf = max(0.0, min(1.0, s_conf + delta))
        if feats.get("has_traces") and abs(delta) > 1e-9:
            source = f"{base_src}+trace" if s_conf > 0 else "trace"
        else:
            source = base_src if s_conf > 0 else "none"
        if conf >= threshold:
            note = why + (f"; trace-shape {'+' if delta >= 0 else ''}{round(delta, 2)}"
                          if abs(delta) > 1e-9 else "")
            matches.append(ArchetypeMatch(aid, round(conf, 4), note, source))  # type: ignore[arg-type]

    matches.sort(key=lambda m: m.confidence, reverse=True)
    if not matches:
        best = max(
            ((aid, max(0.0, min(1.0, semantic.get(aid, (0.0, ""))[0] + aff.get(aid, 0.0))))
             for aid in archetypes),
            key=lambda kv: kv[1], default=(CUSTOM, 0.0))
        return [ArchetypeMatch(
            CUSTOM, round(best[1], 4),
            f"no archetype cleared threshold {threshold}; best was "
            f"{best[0]} at {round(best[1], 3)} — route to custom generation",
            "keyword" if client is None else "llm")]
    return matches
