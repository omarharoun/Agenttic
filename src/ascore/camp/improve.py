"""
The feedback loop.

Pieces:
- `RuleSupportAgent`   : a learnable agent = base heuristic + learned override rules.
- `mine_support_rules` : turn graded FAILURES (real ground truth) into new rules.
- `honest_factory`     : build a challenger from rules mined off graded experience.
- `degenerate_factory` : build a challenger by "learning" from the agent's OWN
                         (unverified) outputs — included to demonstrate collapse.
- `ImprovementLoop`    : champion/challenger ratchet against a FrozenHoldout, with
                         a stall/collapse guard that escalates to humans.

The ratchet is the safety mechanism: a challenger only replaces the champion if
it is measurably better on the frozen anchor, so the deployed agent can never
regress from the loop. If self-generated challengers stop improving, the loop
halts and hands off to a human instead of drifting.
"""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from .agent import Agent, HeuristicSupportAgent
from .environment import MockSupportEnv
from .holdout import FrozenHoldout
from .task import Task
from .trace import Episode, TraceStore

# (tokens, category_constraint_or_None, target_action)
ActionRule = Tuple[FrozenSet[str], Optional[str], str]

_STOP = {
    "the", "a", "an", "i", "my", "me", "to", "and", "is", "it", "for", "of", "on",
    "in", "you", "your", "please", "hi", "hello", "team", "quick", "one", "help",
    "can", "do", "how", "where", "this", "that", "with", "was", "were", "not",
    "no", "but", "so", "im", "cant", "dont", "there", "someone", "every", "time",
}


def _tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower())
            if len(t) >= 2 and t not in _STOP]


class RuleSupportAgent(Agent):
    """Base heuristic plus learned overrides. Rules accumulate across generations."""

    def __init__(self, action_rules: Optional[List[ActionRule]] = None,
                 generation: int = 0):
        self.base = HeuristicSupportAgent()
        self.action_rules: List[ActionRule] = list(action_rules or [])
        self.generation = generation
        self.agent_id = f"rule-support-gen{generation}"

    def act(self, observation: Dict) -> Dict:
        out = self.base.act(observation)
        text_tokens = set(_tokenize(str(observation.get("message", ""))))
        for tokens, cat_constraint, action in self.action_rules:
            if cat_constraint is not None and out["category"] != cat_constraint:
                continue
            if tokens & text_tokens:            # whole-token match, not substring
                out["action"] = action
                break
        return out


def mine_support_rules(episodes: List[Episode], min_support: int = 3) -> List[ActionRule]:
    """From graded experience, learn (distinctive-tokens -> correct action) rules.

    Key correctness point (this is the bug that sinks naive versions): a learned
    token must separate the failing target from the behavior we need to KEEP. So
    we group ALL episodes by their *correct* (category, action) — including the
    ones the agent already gets right — and only keep tokens that are distinctive
    against every other group in the same space. Otherwise a token like "month"
    learned for billing-FAQ also fires on billing-refunds and breaks them.
    """
    by_correct: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    fail_counts: Counter = Counter()
    for ep in episodes:
        det = ep.grade_detail
        a = det.get("action", {})
        c = det.get("category", {})
        cat_want, act_want = c.get("want"), a.get("want")
        by_correct[(cat_want, act_want)].append(str(ep.inputs.get("message", "")).lower())
        if not a.get("ok", True):
            fail_counts[(cat_want, act_want)] += 1

    presence: Dict[Tuple[str, str], Counter] = {}
    for key, msgs in by_correct.items():
        c = Counter()
        for m in msgs:
            c.update(set(_tokenize(m)))
        presence[key] = c

    rules: List[ActionRule] = []
    for key, fails in fail_counts.items():
        if fails < min_support:
            continue
        cat, action = key
        common = {t for t, n in presence[key].items() if n >= 2}
        distinctive = set(common)
        for other_key, other_counts in presence.items():
            if other_key != key:
                distinctive -= set(other_counts)     # must not fire on other behavior
        if distinctive:
            rules.append((frozenset(sorted(distinctive)[:8]), cat, action))
    return rules


def honest_factory(champion: RuleSupportAgent, episodes: List[Episode]) -> RuleSupportAgent:
    """Learn from verified experience (ground truth) -> a genuinely better challenger."""
    new_rules = mine_support_rules(episodes)
    return RuleSupportAgent(
        action_rules=champion.action_rules + new_rules,
        generation=champion.generation + 1,
    )


def degenerate_factory(champion: RuleSupportAgent, failures: List[Episode]) -> RuleSupportAgent:
    """Anti-pattern: 'learn' from the agent's OWN outputs as if they were correct.

    Mines rules that reinforce whatever the agent already did (got), ignoring the
    real labels. Produces a challenger that is no better (usually worse), which the
    ratchet then refuses -> the loop stalls and escalates. This exists to show what
    happens without a ground-truth anchor.
    """
    groups: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for ep in failures:
        det = ep.grade_detail
        a = det.get("action", {})
        c = det.get("category", {})
        groups[(c.get("got"), a.get("got"))].append(  # note: GOT, not WANT
            str(ep.inputs.get("message", "")).lower()
        )
    rules: List[ActionRule] = []
    for (cat, action), msgs in groups.items():
        toks = set()
        for m in msgs:
            toks.update(_tokenize(m)[:2])
        if toks:
            rules.append((frozenset(sorted(toks)[:4]), cat, action))
    return RuleSupportAgent(
        action_rules=champion.action_rules + rules,
        generation=champion.generation + 1,
    )


@dataclass
class LoopConfig:
    rounds: int = 8
    episodes_per_round: int = 400
    accuracy_floor: float = 0.99      # hard floor for promotion (wilson lower bound)
    improve_margin: float = 0.002     # min holdout gain to accept a challenger
    patience: int = 2                 # consecutive non-improvements before halting
    seed: int = 0


@dataclass
class RoundLog:
    round: int
    champion_gen: int
    champion_rate: float
    challenger_gen: int
    challenger_rate: float
    accepted: bool
    note: str = ""


@dataclass
class LoopResult:
    rounds: List[RoundLog] = field(default_factory=list)
    final_champion_gen: int = 0
    final_holdout_rate: float = 0.0
    final_holdout_wilson: float = 0.0
    halted_reason: str = ""
    promoted: bool = False


class ImprovementLoop:
    def __init__(self, task: Task, holdout: FrozenHoldout, store: TraceStore,
                 factory: Callable[[RuleSupportAgent, List[Episode]], RuleSupportAgent]
                 = honest_factory):
        self.task = task
        self.holdout = holdout
        self.store = store
        self.factory = factory

    def _collect_round(self, agent: RuleSupportAgent, n: int,
                       rng: random.Random) -> List[Episode]:
        """Run the agent for n episodes, record all to memory, return all episodes."""
        env = MockSupportEnv(self.task, rng)
        episodes: List[Episode] = []
        for _ in range(n):
            obs = env.reset()
            action = agent.act(obs)
            result = env.step(action)
            grade = result.info["grade"]
            case = result.info["case"]
            ep = Episode(
                episode_id="%012x" % rng.getrandbits(48),
                task_id=self.task.task_id, agent_id=agent.agent_id,
                timestamp=0.0, inputs=case.inputs, action=action,
                passed=grade.passed, score=grade.score,
                grade_detail=grade.detail, system_prompt=obs.get("system", ""),
            )
            self.store.record(ep)
            episodes.append(ep)
        return episodes

    def run(self, config: LoopConfig,
            human_approver: Optional[Callable] = None,
            on_round: Optional[Callable] = None) -> LoopResult:
        # ``on_round(r, total_rounds)`` is an optional progress hook (added for
        # the async runner); when None the loop behaves exactly as before.
        rng = random.Random(config.seed)
        champion = RuleSupportAgent(generation=0)
        best_rate = self.holdout.evaluate(champion).pass_rate
        result = LoopResult()
        stalls = 0

        for r in range(1, config.rounds + 1):
            episodes = self._collect_round(champion, config.episodes_per_round, rng)
            challenger = self.factory(champion, episodes)

            champ_rate = self.holdout.evaluate(champion).pass_rate
            chal_rate = self.holdout.evaluate(challenger).pass_rate
            accepted = chal_rate >= champ_rate + config.improve_margin

            log = RoundLog(
                round=r, champion_gen=champion.generation, champion_rate=champ_rate,
                challenger_gen=challenger.generation, challenger_rate=chal_rate,
                accepted=accepted,
            )

            if accepted:
                champion = challenger
                best_rate = max(best_rate, chal_rate)
                stalls = 0
                log.note = "challenger promoted to champion"
            else:
                stalls += 1
                log.note = f"challenger refused (no gain); stall {stalls}/{config.patience}"

            result.rounds.append(log)

            if on_round is not None:
                on_round(r, config.rounds)

            # Stop early once the champion clears the floor with margin to spare.
            champ_final = self.holdout.evaluate(champion)
            if champ_final.wilson_lower_95 >= config.accuracy_floor:
                result.halted_reason = "floor cleared"
                break
            # Collapse / stall guard: self-generated challengers stopped helping.
            if stalls >= config.patience:
                result.halted_reason = (
                    "stalled: self-generated challengers stopped improving -> "
                    "escalate to human (new data / curriculum needed)"
                )
                break

        final = self.holdout.evaluate(champion)
        result.final_champion_gen = champion.generation
        result.final_holdout_rate = final.pass_rate
        result.final_holdout_wilson = final.wilson_lower_95
        if not result.halted_reason:
            result.halted_reason = "round budget exhausted"

        # Promotion still requires BOTH the floor AND a human sign-off.
        floor_ok = final.wilson_lower_95 >= config.accuracy_floor
        human_ok = bool(human_approver(final)) if (floor_ok and human_approver) else False
        result.promoted = floor_ok and human_ok

        self._final_champion = champion  # kept for review-queue export
        return result

    @property
    def champion(self) -> RuleSupportAgent:
        return getattr(self, "_final_champion", RuleSupportAgent(generation=0))
