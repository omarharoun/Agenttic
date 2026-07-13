"""
AgentCamp, folded into Agenttic — the training / evaluation layer.

AgentCamp's job is narrow and specific, and preserved verbatim from the
standalone MVP:

  1. Run an agent against a task many times in a (simulated) environment.
  2. Grade every attempt with a deterministic grader.
  3. Record every episode as reusable *memory* (traces).
  4. Refuse to promote the agent unless it clears a hard accuracy floor
     (Wilson 95% lower bound, non-overridable) AND a human explicitly approves.

Nothing here trains model weights. It produces the honest measurements and the
labelled trace dataset you would hand to a fine-tuning / distillation job. The
self-improving loop keeps a frozen held-out anchor so a self-training loop can't
quietly collapse.

Inside Agenttic this engine is wired to: the tenant's BYO-Anthropic-key agent as
the thing under camp (see :mod:`agenttic.camp.adapter_agent`), per-tenant DB
persistence of runs/episodes/gate decisions (see :mod:`agenttic.camp.store`), and
the promotion gate / Wilson bound as the training-side evidence behind the
certification & credibility story.
"""

from .agent import Agent, HeuristicSupportAgent
from .environment import Environment, MockSupportEnv
from .gate import GateDecision, PromotionGate
from .holdout import FrozenHoldout
from .improve import ImprovementLoop, LoopConfig, degenerate_factory, honest_factory
from .task import Case, GradeResult, Task
from .trace import (
    Episode,
    MemoryTraceStore,
    TraceStore,
    distillation_record,
    distillation_records,
)
from .trainer import CampConfig, CampReport, TrainingCamp, wilson_lower_bound

__all__ = [
    "Task",
    "Case",
    "GradeResult",
    "Environment",
    "MockSupportEnv",
    "Agent",
    "HeuristicSupportAgent",
    "Episode",
    "TraceStore",
    "MemoryTraceStore",
    "distillation_record",
    "distillation_records",
    "TrainingCamp",
    "CampConfig",
    "CampReport",
    "wilson_lower_bound",
    "PromotionGate",
    "GateDecision",
    "FrozenHoldout",
    "ImprovementLoop",
    "LoopConfig",
    "honest_factory",
    "degenerate_factory",
]

__version__ = "0.1.0"
