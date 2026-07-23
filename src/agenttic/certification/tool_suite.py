"""Tool certification — the component tier (SPEC-12 Step 56).

Test tools in ISOLATION, not just whole agents. Framework-agnostic: a tool is a
name, a description, a declared input schema and something callable — whether it
came from an MCP server, native function-calling, or a plain Python callable.

Reuses the Step 55 battery minus the protocol specifics (contract, fuzzing, error
taxonomy, idempotency, side-effect disclosure) and adds the component-tier checks
IBM names:

* **description quality (diagnostic)** — give N models the toolset and a set of
  tasks and measure tool-SELECTION accuracy. A poorly described tool produces
  agent failures that *look like* model failures; this converts a mystery into a
  root cause with a concrete fix (rewrite the description).
* **parameter-construction accuracy** — right tool chosen AND right arguments.
* **failure-mode handling** — rate limits, timeouts, upstream 5xx: does the tool
  surface a TYPED error the agent can act on, or does it just blow up?

Selection is measured through a ``selector`` callable so it works with real
models; the default is a deterministic description-matching selector, so the
diagnostic runs offline and in CI (the same injectable-client convention the rest
of the platform uses).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from agenttic.certification.mcp_suite import (
    CheckOutcome, detect_leak, valid_args)

_STOP = frozenset(
    "a an the of to for and or with from this that it its is are be by on in at "
    "please can you should would need want get set make do handle thing things".split())


@dataclass
class ToolSpec:
    """One tool under test, from any source."""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    #: call(arguments) -> (ok, text). Raising is a failure mode we measure.
    call: Callable[[dict], tuple[bool, str]] | None = None
    mutating: bool = False          # operator ground truth
    declares_mutating: bool | None = None   # what the tool itself discloses
    version: str = ""
    source: str = "native"          # "native" | "mcp"

    def discloses(self) -> bool:
        if self.declares_mutating is not None:
            return self.declares_mutating
        text = self.description.lower()
        return any(w in text for w in
                   ("writes", "modifies", "mutates", "deletes", "creates",
                    "side effect", "side-effect", "issues", "sends", "refund"))


# ---- adapters -------------------------------------------------------------- #

def from_native(specs: list[dict]) -> list[ToolSpec]:
    """Adapt plain callables: {name, description, input_schema, fn, mutating}."""
    out = []
    for s in specs:
        fn = s.get("fn")

        def _call(args, _fn=fn):
            try:
                return True, str(_fn(**args) if _fn else "")
            except Exception as e:                    # a raw raise IS the finding
                return False, f"{type(e).__name__}: {e}"
        out.append(ToolSpec(
            name=s["name"], description=s.get("description", ""),
            input_schema=s.get("input_schema", {}), call=_call,
            mutating=bool(s.get("mutating")), version=str(s.get("version", "")),
            source="native"))
    return out


def from_mcp(client) -> list[ToolSpec]:
    """Adapt every tool an MCP server exposes into the same component tier."""
    out = []
    for t in client.list_tools():
        def _call(args, _n=t.name):
            r = client.call_tool(_n, args)
            return (r.ok, r.text if r.ok else (r.error_message or r.text))
        out.append(ToolSpec(
            name=t.name, description=t.description, input_schema=t.input_schema,
            call=_call, declares_mutating=t.declares_mutating,
            version=str(client.server_info.get("version", "")), source="mcp"))
    return out


# ---- description quality / selection accuracy ------------------------------ #

def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower())
            if len(w) > 2 and w not in _STOP}


def description_selector(model: str, task: str, tools: list[ToolSpec]) -> tuple[str, dict]:
    """The offline default 'model': pick the tool whose DESCRIPTION best overlaps
    the task. Deterministic, and it depends only on description quality — which
    is exactly the property under test. A real LLM client can be substituted."""
    task_tokens = _tokens(task)
    best, best_score = "", 0.0
    for t in tools:
        # a tiny per-model tie-break so N "models" aren't perfectly identical
        bias = (len(model) + len(t.name)) % 3 * 0.01
        overlap = len(task_tokens & _tokens(f"{t.description}")) + bias
        if overlap > best_score:
            best, best_score = t.name, overlap
    if not best:
        return "", {}
    tool = next(t for t in tools if t.name == best)
    return best, valid_args(_as_mcp(tool))


class _ShimTool:
    def __init__(self, spec: ToolSpec):
        self.input_schema = spec.input_schema


def _as_mcp(spec: ToolSpec):
    return _ShimTool(spec)


@dataclass
class SelectionReport:
    per_model: dict[str, float] = field(default_factory=dict)
    per_tool: dict[str, float] = field(default_factory=dict)
    n_tasks: int = 0

    @property
    def accuracy(self) -> float:
        v = list(self.per_model.values())
        return sum(v) / len(v) if v else 0.0


def selection_accuracy(
    tools: list[ToolSpec], tasks: list[tuple[str, str]], *,
    models: list[str] | None = None,
    selector: Callable[[str, str, list[ToolSpec]], tuple[str, dict]] | None = None,
) -> SelectionReport:
    """Cross-model tool-selection accuracy. ``tasks`` is [(task_text, expected_tool)].

    This is the diagnostic that turns "the agent keeps failing" into "this tool's
    description is too vague for a model to pick it"."""
    models = models or ["model-a", "model-b"]
    pick = selector or description_selector
    rep = SelectionReport(n_tasks=len(tasks))
    hits_per_tool: dict[str, list[int]] = {}
    for m in models:
        hits = 0
        for task, expected in tasks:
            chosen, _args = pick(m, task, tools)
            ok = int(chosen == expected)
            hits += ok
            hits_per_tool.setdefault(expected, []).append(ok)
        rep.per_model[m] = hits / len(tasks) if tasks else 0.0
    rep.per_tool = {k: sum(v) / len(v) for k, v in hits_per_tool.items() if v}
    return rep


# ---- component-tier checks -------------------------------------------------- #

def check_tool_contract(tools: list[ToolSpec]) -> CheckOutcome:
    bad = [t.name for t in tools
           if (t.input_schema or {}).get("type") != "object"
           or "properties" not in (t.input_schema or {})]
    if bad:
        return CheckOutcome("contract_schema", 0.0,
                            "no object input schema: " + ", ".join(bad), critical=True)
    return CheckOutcome("contract_schema", 1.0,
                        f"{len(tools)} tool(s) declare object input schemas")


def check_tool_fuzzing(tools: list[ToolSpec]) -> CheckOutcome:
    problems = []
    for t in tools:
        if t.call is None:
            continue
        for label, args in (("missing required", {}),
                            ("wrong type", {k: [] for k in
                                            (t.input_schema.get("properties") or {"x": {}})}),
                            ("oversized", {next(iter(t.input_schema.get("properties")
                                                     or {"x": {}})): "A" * 50_000})):
            try:
                ok, text = t.call(args)
            except Exception as e:
                problems.append(f"{t.name}: RAISED on {label} ({type(e).__name__})")
                continue
            if ok:
                problems.append(f"{t.name}: ACCEPTED {label} input")
    if problems:
        return CheckOutcome("input_fuzzing", 0.0, "; ".join(problems[:4]), critical=True)
    return CheckOutcome("input_fuzzing", 1.0,
                        "malformed inputs produce typed errors, never raises")


def check_tool_error_taxonomy(tools: list[ToolSpec]) -> CheckOutcome:
    for t in tools:
        if t.call is None:
            continue
        try:
            _ok, text = t.call({"__force_error__": True})
        except Exception as e:
            text = f"{type(e).__name__}: {e}"
        leak = detect_leak(text)
        if leak:
            return CheckOutcome("error_taxonomy", 0.0,
                                f"{t.name} leaked {leak}", critical=True)
    return CheckOutcome("error_taxonomy", 1.0, "no internals leaked in tool errors")


def check_failure_modes(tools: list[ToolSpec],
                        failures: dict[str, list[str]] | None = None) -> CheckOutcome:
    """Rate limits / timeouts / upstream 5xx must surface as a TYPED error the
    agent can act on."""
    failures = failures or {}
    if not failures:
        return CheckOutcome("failure_mode_handling", 0.0,
                            "no failure modes declared", skipped=True)
    problems = []
    for name, modes in failures.items():
        tool = next((t for t in tools if t.name == name), None)
        if tool is None or tool.call is None:
            continue
        for mode in modes:
            try:
                ok, text = tool.call({"__simulate__": mode})
            except Exception as e:
                problems.append(f"{name}/{mode}: RAISED {type(e).__name__}")
                continue
            if ok or not text.strip():
                problems.append(f"{name}/{mode}: no typed error surfaced")
    if problems:
        return CheckOutcome("failure_mode_handling", 0.0, "; ".join(problems),
                            critical=True)
    return CheckOutcome("failure_mode_handling", 1.0,
                        "rate-limit / timeout / 5xx each surface a typed error")


def check_description_quality(rep: SelectionReport, *,
                              floor: float = 0.8) -> CheckOutcome:
    weak = sorted(k for k, v in rep.per_tool.items() if v < floor)
    if weak:
        return CheckOutcome(
            "description_quality", 0.0,
            f"cross-model selection accuracy {rep.accuracy:.2f}; tools models "
            f"cannot reliably pick: {', '.join(weak)} — rewrite the description",
        )
    return CheckOutcome("description_quality", 1.0,
                        f"cross-model selection accuracy {rep.accuracy:.2f}")


def check_side_effects(tools: list[ToolSpec]) -> CheckOutcome:
    undisclosed = [t.name for t in tools if t.mutating and not t.discloses()]
    if undisclosed:
        return CheckOutcome("side_effect_disclosure", 0.0,
                            "mutating tools that don't disclose: "
                            + ", ".join(undisclosed), critical=True)
    declared = [t for t in tools if t.mutating]
    if not declared:
        return CheckOutcome("side_effect_disclosure", 0.0,
                            "no mutating tools declared", skipped=True)
    return CheckOutcome("side_effect_disclosure", 1.0,
                        f"{len(declared)} mutating tool(s) disclose their effects")


# ---- report ----------------------------------------------------------------- #

@dataclass
class ToolsetReport:
    tools: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    outcomes: list[CheckOutcome] = field(default_factory=list)
    selection: SelectionReport | None = None
    versions: dict[str, str] = field(default_factory=dict)

    @property
    def scored(self) -> list[CheckOutcome]:
        return [o for o in self.outcomes if not o.skipped]

    @property
    def score(self) -> float:
        s = self.scored
        return sum(o.score for o in s) / len(s) if s else 0.0

    @property
    def failed(self) -> list[str]:
        return [o.check_id for o in self.scored if not o.passed]

    @property
    def passed(self) -> bool:
        return not self.failed

    def weak_tools(self, floor: float = 0.8) -> list[str]:
        """Tools models struggle to select — the root cause an agent-level report
        should cite when the agent used one of them."""
        if not self.selection:
            return []
        return sorted(k for k, v in self.selection.per_tool.items() if v < floor)

    def as_dict(self) -> dict:
        return {
            "tools": self.tools, "sources": sorted(set(self.sources)),
            "versions": self.versions,
            "score": round(self.score, 4), "passed": self.passed,
            "failed": self.failed, "weak_tools": self.weak_tools(),
            "selection_accuracy": (round(self.selection.accuracy, 4)
                                    if self.selection else None),
            "per_tool_selection": (self.selection.per_tool if self.selection else {}),
            "checks": [{"check_id": o.check_id, "score": o.score,
                        "detail": o.detail, "skipped": o.skipped}
                       for o in self.outcomes],
        }


def certify_toolset(
    tools: list[ToolSpec], *,
    tasks: list[tuple[str, str]] | None = None,
    models: list[str] | None = None,
    selector: Callable[..., tuple[str, dict]] | None = None,
    failure_modes: dict[str, list[str]] | None = None,
    selection_floor: float = 0.8,
) -> ToolsetReport:
    rep = ToolsetReport(
        tools=[t.name for t in tools], sources=[t.source for t in tools],
        versions={t.name: t.version for t in tools if t.version})
    rep.outcomes.append(check_tool_contract(tools))
    rep.outcomes.append(check_tool_fuzzing(tools))
    rep.outcomes.append(check_tool_error_taxonomy(tools))
    rep.outcomes.append(check_side_effects(tools))
    rep.outcomes.append(check_failure_modes(tools, failure_modes))
    if tasks:
        rep.selection = selection_accuracy(tools, tasks, models=models,
                                           selector=selector)
        rep.outcomes.append(check_description_quality(rep.selection,
                                                      floor=selection_floor))
    return rep


def link_to_agent_scorecard(scorecard: dict, toolset: ToolsetReport,
                            *, used_tools: list[str]) -> dict:
    """Feed component results into the agent-level failure catalogue: when an
    agent fails, the report can say whether a tool it used was already
    known-weak (Step 56 acceptance)."""
    weak = set(toolset.weak_tools())
    failed_checks = set(toolset.failed)
    linked = {
        "toolset_score": round(toolset.score, 4),
        "toolset_failed_checks": sorted(failed_checks),
        "tool_versions": {t: toolset.versions.get(t, "")
                          for t in used_tools if t in toolset.versions},
        "known_weak_tools_used": sorted(w for w in used_tools if w in weak),
    }
    out = dict(scorecard)
    out["component_evidence"] = linked
    return out
