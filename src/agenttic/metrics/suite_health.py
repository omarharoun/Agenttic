"""What the suite learned about ITSELF from the agents that ran against it.

Every number the harness reports is a statement about an agent. None of them is
a statement about the benchmark, and the benchmark is the instrument. Two
failure modes are invisible to per-agent scoring and visible the moment runs are
read across agents:

* **A case no agent has ever passed.** It reads, forever, as a run of weak
  agents. It is equally consistent with a broken case — an impossible task, a
  criterion whose evidence the harness never supplies, an oracle that is simply
  wrong. Nothing in a single run can tell those apart, and nothing was looking.
* **A case every agent always passes.** It costs a model call per trial per
  agent and moves no verdict. It is not wrong, it is *inert*: the suite is
  paying to re-learn something it already knows.

Neither is decidable from one run, which is why this reads the whole canonical
history. And neither verdict is an accusation: this module NAMES cases and
states the competing explanations. Calling a case broken because agents fail it
is the same error as calling an agent weak because a case is broken — the
direction of the inference is exactly what is unknown.

The evidence bar is explicit and enforced (`MIN_AGENTS`). One agent failing a
case is not evidence about the case; it is one agent. A diagnostic that ignored
that would manufacture "broken cases" from a single bad run, which is worse than
not looking, because it comes with a number attached.

Deterministic, offline, zero model calls: it reads runs that already exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Distinct agents required before a case-level verdict is reported at all.
#: Below this the finding is `insufficient_evidence` — never `unpassed`. Two is
#: the minimum that can distinguish "this agent" from "this case", and it is a
#: floor rather than a target: two agents that share a base model are close to
#: one observation, which `agents` in the finding lets a reader judge.
MIN_AGENTS = 2


@dataclass
class CaseFinding:
    test_id: str
    verdict: str                 # unpassed | inert | discriminating | insufficient_evidence
    agents: list[str] = field(default_factory=list)
    trials: int = 0
    passes: int = 0
    note: str = ""

    @property
    def pass_rate(self) -> float:
        return (self.passes / self.trials) if self.trials else 0.0


def case_evidence(runs: list[dict]) -> dict[str, dict]:
    """test_id -> {agents, trials, passes, agent_pass} across canonical runs.

    ``agent_pass`` is per agent, because "no agent ever passed it" is a claim
    about agents, not about trials: one agent that ran ten times and failed all
    ten is still one agent.
    """
    ev: dict[str, dict] = {}
    for run in runs:
        per_case = run.get("per_case") or {}
        agent = str(run.get("agent_id") or "?")
        if not isinstance(per_case, dict):
            continue
        for test_id, results in per_case.items():
            if not isinstance(results, list):
                continue
            oks = [bool(x) for x in results]
            rec = ev.setdefault(test_id, {"agents": set(), "trials": 0,
                                          "passes": 0, "agent_pass": {}})
            rec["agents"].add(agent)
            rec["trials"] += len(oks)
            rec["passes"] += sum(oks)
            # any() per agent: an agent that passed once has passed it, which is
            # what makes the case not-impossible. Reliability is a separate
            # question, measured by flaky_rate.
            rec["agent_pass"][agent] = rec["agent_pass"].get(agent, False) or any(oks)
    for rec in ev.values():
        rec["agents"] = sorted(rec["agents"])
    return ev


def suite_health(runs: list[dict], *, min_agents: int = MIN_AGENTS) -> dict:
    """Diagnose the SUITE from its canonical run history.

    Returns findings plus the evidence they rest on. `runs_without_per_case` is
    reported rather than ignored: runs persisted before per-case results were
    kept carry no case-level evidence, and a diagnosis that silently read fewer
    runs than the operator thinks it did is the failure this module exists to
    catch, committed by the module itself.
    """
    usable = [r for r in runs if isinstance(r.get("per_case"), dict) and r["per_case"]]
    blind = len(runs) - len(usable)
    ev = case_evidence(usable)

    findings: list[CaseFinding] = []
    for test_id, rec in sorted(ev.items()):
        n_agents = len(rec["agents"])
        passed_by = [a for a, ok in rec["agent_pass"].items() if ok]
        f = CaseFinding(test_id=test_id, agents=list(rec["agents"]),
                        trials=rec["trials"], passes=rec["passes"],
                        verdict="discriminating")
        if n_agents < min_agents:
            f.verdict = "insufficient_evidence"
            f.note = (f"{n_agents} agent(s) — below the {min_agents} needed to "
                      "say anything about the CASE rather than the agent")
        elif not passed_by:
            f.verdict = "unpassed"
            f.note = (f"no agent has ever passed this ({n_agents} agents, "
                      f"{rec['trials']} trials). Either the hardest case in the "
                      "suite or a broken one — this cannot tell you which. Read "
                      "the case: does the harness supply the evidence its "
                      "criteria demand, and is the expected answer right?")
        elif rec["passes"] == rec["trials"]:
            f.verdict = "inert"
            f.note = (f"every agent passed every trial ({n_agents} agents, "
                      f"{rec['trials']} trials) — costs a model call per trial "
                      "and separates nobody")
        findings.append(f)

    by_verdict: dict[str, list[CaseFinding]] = {}
    for f in findings:
        by_verdict.setdefault(f.verdict, []).append(f)

    return {
        "runs_read": len(usable),
        "runs_without_per_case": blind,
        "agents": sorted({str(r.get("agent_id") or "?") for r in usable}),
        "cases": len(findings),
        "unpassed": [f.test_id for f in by_verdict.get("unpassed", [])],
        "inert": [f.test_id for f in by_verdict.get("inert", [])],
        "insufficient_evidence": [
            f.test_id for f in by_verdict.get("insufficient_evidence", [])],
        "discriminating": len(by_verdict.get("discriminating", [])),
        "findings": [f.__dict__ | {"pass_rate": round(f.pass_rate, 4)}
                     for f in findings if f.verdict != "discriminating"],
        "note": ("a case no agent passes is EITHER the hardest case or a broken "
                 "one, and this cannot decide between them — it names the case "
                 "so a human reads it. Nothing here changes a score."),
    }


def blocked_reason(health: dict, *, min_agents: int = MIN_AGENTS) -> str | None:
    """Why the diagnosis can conclude nothing yet, or None if it can."""
    if health["runs_read"] == 0:
        return ("no canonical run carries per-case results — run "
                "`agenttic standard run` again; runs persisted before "
                "2026-08-04 did not keep them")
    if len(health["agents"]) < min_agents:
        return (f"{len(health['agents'])} agent(s) in the history; "
                f"{min_agents} are needed before a case-level verdict means "
                "anything")
    return None
