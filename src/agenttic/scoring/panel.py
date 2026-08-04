"""A panel of judges — and an honest account of what a panel actually buys.

The published result is that several small judges from DIFFERENT model families
track human judgement better than one large judge, at a fraction of the cost.
The mechanism is not "more opinions". It is that independent models make
independent mistakes, so averaging cancels error that is uncorrelated — plus the
removal of intra-model bias, a judge's preference for output from its own
family.

Both halves of that mechanism depend on the families differing. Three judges
from one family share pre-training data, post-training and refusal behaviour;
they are close to one judge sampled three times. Averaging them still cuts
sampling variance, which is worth something, but it cannot cancel a bias all
three hold, and it does nothing about self-preference. So this module measures
family diversity and refuses to describe a single-family panel as decorrelated
(`panel_independence`). That distinction is the module: the machinery is easy,
and claiming the bias-reduction property without the diversity that produces it
is how a panel becomes a credibility costume.

Two structural constraints, both from the schema rather than taste:

* **The aggregate is a MEDIAN, not a mean.** ``CriterionScore.score`` must be in
  {0, 0.5, 1} (Hard Rule 3). mean(1, 1, 0) = 0.667 does not exist on that scale
  and the model validator rejects it. A median of an odd panel is always a value
  some judge actually returned.
* **Nothing about the panel may reach ``CriterionScore``.** It is embedded in
  ``RunScore`` -> ``Scorecard``, and ``verify_manifest`` recomputes
  ``content_hash(scorecard)`` — one new field there invalidates every
  certificate ever issued. So ``score_criterion`` returns the ordinary
  CriterionScore and the ``PanelVerdict`` SEPARATELY, and the caller decides
  where to put the detail.

This promotes no criterion. `demonstrated_calibrated_judge()` is untouched: a
panel that agrees with itself has demonstrated agreement with itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agenttic.schema.scorecard import CriterionScore

#: model-id prefix -> family. Family is the unit that matters: two models from
#: one lab share the pre-training corpus and post-training that produce
#: correlated errors, whatever their size.
_FAMILY_PREFIXES = (
    ("claude", "anthropic"),
    ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
    ("gemini", "google"), ("gemma", "google"),
    ("llama", "meta"),
    ("mistral", "mistral"), ("mixtral", "mistral"),
    ("command", "cohere"),
    ("qwen", "qwen"),
    ("deepseek", "deepseek"),
    ("grok", "xai"),
)


def model_family(model: str) -> str:
    """Family of a model id, or ``unknown`` — never a guess.

    ``unknown`` is load-bearing: an unrecognised id counted as its own family
    would let a typo manufacture the diversity this module exists to verify.
    """
    m = (model or "").strip().lower().lstrip("/")
    for prefix, fam in _FAMILY_PREFIXES:
        if m.startswith(prefix) or f"/{prefix}" in m:
            return fam
    return "unknown"


@dataclass
class PanelVote:
    judge_model: str
    family: str
    score: float | None = None      # None => this judge did not vote
    rationale: str | None = None
    cost_usd: float = 0.0
    error: str | None = None


@dataclass
class PanelVerdict:
    criterion_id: str
    score: float | None
    votes: list[PanelVote] = field(default_factory=list)
    note: str = ""

    @property
    def cast(self) -> list[PanelVote]:
        return [v for v in self.votes if v.score is not None]

    @property
    def unanimous(self) -> bool:
        scores = {v.score for v in self.cast}
        return len(scores) == 1 and len(self.cast) > 1

    @property
    def dispersion(self) -> float:
        """max - min across cast votes. 1.0 on this scale is total disagreement:
        the median is then a tie-break dressed as a measurement."""
        scores = [v.score for v in self.cast]
        return (max(scores) - min(scores)) if len(scores) > 1 else 0.0

    @property
    def contested(self) -> bool:
        return self.dispersion >= 1.0

    @property
    def families(self) -> list[str]:
        return sorted({v.family for v in self.cast})

    def to_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "score": self.score,
            "n_voted": len(self.cast),
            "n_judges": len(self.votes),
            "unanimous": self.unanimous,
            "contested": self.contested,
            "dispersion": round(self.dispersion, 4),
            "families": self.families,
            "votes": [v.__dict__ for v in self.votes],
            "cost_usd": round(sum(v.cost_usd for v in self.votes), 6),
            "note": self.note,
        }


def aggregate(votes: list[PanelVote], *, criterion_id: str,
              min_votes: int = 2) -> PanelVerdict:
    """Median of the cast votes, on the {0, 0.5, 1} scale.

    A panel that quietly degrades to one voter is a single judge wearing a
    panel's credibility, so below ``min_votes`` there is no verdict — the caller
    gets ``score=None`` and must record a scoring error rather than a number.
    An even split takes the LOWER value: the codebase's standing rule is that
    unproven is not passed, and a tie is by definition unproven.
    """
    cast = sorted(v.score for v in votes if v.score is not None)
    verdict = PanelVerdict(criterion_id=criterion_id, score=None, votes=list(votes))
    if len(cast) < min_votes:
        verdict.note = (f"only {len(cast)} of {len(votes)} judges returned a "
                        f"score; {min_votes} needed. NO verdict — a panel that "
                        "degrades to one voter is one judge with a panel's name")
        return verdict
    mid = len(cast) // 2
    # Even panel: lower of the two middles. Odd: the true median.
    verdict.score = cast[mid] if len(cast) % 2 else min(cast[mid - 1], cast[mid])
    if verdict.contested:
        verdict.note = ("judges spanned the full scale — the median here is a "
                        "tie-break, not agreement; treat as unresolved")
    elif verdict.unanimous:
        verdict.note = f"unanimous across {len(cast)} judges"
    else:
        verdict.note = f"median of {len(cast)} judges (split: {cast})"
    return verdict


def panel_independence(judge_models: list[str], agent_model: str) -> dict:
    """Does this panel have the property the published result depends on?

    Reports rather than refuses, because a single-family panel is still worth
    running — it cuts sampling variance. What it must not do is CLAIM the
    decorrelated-error and bias-reduction benefits it does not have.
    """
    fams = [model_family(m) for m in judge_models]
    distinct = sorted(set(fams))
    agent_fam = model_family(agent_model)
    shares_agent = [m for m, f in zip(judge_models, fams) if f == agent_fam]
    blockers = []
    if len(distinct) < 2:
        blockers.append(
            f"all {len(judge_models)} judges are {distinct[0] if distinct else 'unknown'} "
            "— one family makes independent errors correlated, so averaging cuts "
            "sampling variance but CANNOT cancel a bias they share")
    if "unknown" in distinct:
        blockers.append("a judge model's family could not be identified, so "
                        "diversity here is unverified rather than absent")
    if shares_agent and agent_fam != "unknown":
        blockers.append(
            f"{len(shares_agent)} judge(s) share the agent's family ({agent_fam}) "
            "— self-preference is exactly the bias a panel is meant to remove")
    if any(m == agent_model for m in judge_models):
        blockers.append(f"a judge IS the agent model ({agent_model}) — Hard Rule 4")
    return {
        "judges": list(judge_models),
        "families": distinct,
        "n_families": len(distinct),
        "agent_family": agent_fam,
        "decorrelated": not blockers,
        "blockers": blockers,
        "note": ("a panel's advantage comes from families that fail differently; "
                 "same-family judges reduce variance, not bias"
                 if blockers else
                 "distinct families, none shared with the agent"),
    }


class JudgePanel:
    """Several judges scoring one criterion, aggregated by median.

    Sequential by construction: judge calls are already retried and rate-limited
    downstream, and a panel that fans out concurrently multiplies burst load on
    the same key for a saving that is irrelevant next to the agent run itself.
    """

    def __init__(self, judges: list, *, agent_model: str, min_votes: int = 2):
        if not judges:
            raise ValueError("a panel needs at least one judge")
        self.judges = list(judges)
        self.agent_model = agent_model
        self.min_votes = min_votes

    @property
    def models(self) -> list[str]:
        return [j.model for j in self.judges]

    def independence(self) -> dict:
        return panel_independence(self.models, self.agent_model)

    def score_criterion(self, criterion, trace, tc) -> tuple[CriterionScore | None,
                                                             PanelVerdict]:
        """Returns ``(CriterionScore | None, PanelVerdict)``.

        Two values on purpose. The panel detail must NOT ride on CriterionScore:
        that model is hashed into every certificate, so a new field there breaks
        verification for every certificate already issued.

        ``None`` for the score when the panel could not reach ``min_votes`` —
        the caller records a scoring error. Inventing a number from one judge is
        the failure this returns None to prevent.
        """
        votes: list[PanelVote] = []
        for judge in self.judges:
            vote = PanelVote(judge_model=judge.model,
                             family=model_family(judge.model))
            try:
                cs = judge.score_criterion(criterion, trace, tc)
                vote.score = cs.score
                vote.rationale = cs.judge_rationale
                vote.cost_usd = cs.cost_usd
            except Exception as exc:  # noqa: BLE001 — one judge failing is data
                vote.error = f"{type(exc).__name__}: {exc}"
            votes.append(vote)

        verdict = aggregate(votes, criterion_id=criterion.criterion_id,
                            min_votes=self.min_votes)
        if verdict.score is None:
            return None, verdict
        return CriterionScore(
            criterion_id=criterion.criterion_id,
            score=verdict.score,
            scorer="judge",
            # Hard Rule 6 is unchanged by panelling. A panel that agrees with
            # itself has demonstrated agreement with itself, not calibration
            # against humans — that still comes from judge_calibration.
            calibrated=criterion.criterion_id in _calibrated_ids(),
            judge_rationale=_panel_rationale(verdict),
            cost_usd=round(sum(v.cost_usd for v in votes), 6),
        ), verdict


def _calibrated_ids() -> set[str]:
    from agenttic.scoring.judge_calibration import demonstrated_calibrated_judge
    return demonstrated_calibrated_judge()


def _panel_rationale(verdict: PanelVerdict) -> str:
    lines = [f"PANEL ({len(verdict.cast)} voted): {verdict.note}"]
    for v in verdict.votes:
        if v.error:
            lines.append(f"  [{v.family}] {v.judge_model}: DID NOT VOTE — {v.error}")
        else:
            why = (v.rationale or "").strip().replace("\n", " ")[:200]
            lines.append(f"  [{v.family}] {v.judge_model}: {v.score} — {why}")
    return "\n".join(lines)


def make_panel(cfg: dict, agent_model: str, client=None,
               models: list[str] | None = None) -> JudgePanel:
    """Build a panel from configured judge models, skipping the agent's own.

    Reads ``scoring.judge_panel`` if present, else the distinct judge models
    already configured. Every judge shares one client here, which is the
    limitation worth naming: the panel is only as diverse as the providers
    reachable from this process, and `independence()` will say so.
    """
    from agenttic.scoring.judge import LLMJudge

    if models is None:
        configured = cfg.get("scoring", {}).get("judge_panel")
        if configured:
            models = list(configured)
        else:
            m = cfg.get("models", {})
            seen, models = set(), []
            for key in ("judge_light", "judge_executor", "judge_strong"):
                mid = m.get(key)
                if mid and mid not in seen:
                    seen.add(mid)
                    models.append(mid)
    usable = [m for m in models if m != agent_model]
    if not usable:
        raise ValueError(
            f"no judge model differs from the agent model {agent_model!r} — "
            "Hard Rule 4 leaves nothing to panel")
    return JudgePanel([LLMJudge(model=m, agent_model=agent_model, client=client,
                                cfg=cfg) for m in usable],
                      agent_model=agent_model)
