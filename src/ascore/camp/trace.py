"""
Traces = the memory.

Every episode (one case, one attempt, one grade) is a durable, reusable record —
what the AgentCamp README kept calling "memory": the labelled dataset you later
feed to a fine-tuning / distillation job to specialize a smaller model for one
job.

In the standalone MVP this was a JSONL file. Folded into Agenttic, episodes are
persisted per-tenant in the database (see :mod:`ascore.camp.store`), so this
module keeps two things:

- ``Episode`` + the append-only ``TraceStore`` (JSONL), unchanged, so the
  vendored trainer / improve loop keep working exactly as tested; and
- ``distillation_records`` — a pure function that reshapes episodes into
  chat-format SFT records, used both by the file export and by the API export
  (which streams straight from the DB, no temp files).

By default only *passing* episodes are exported, because you generally want to
distill from correct behavior.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List


@dataclass
class Episode:
    episode_id: str
    task_id: str
    agent_id: str
    timestamp: float
    inputs: Dict[str, Any]
    action: Dict[str, Any]
    passed: bool
    score: float
    grade_detail: Dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def distillation_record(ep: Episode) -> Dict[str, Any]:
    """Reshape one episode into a chat-format SFT/distillation record."""
    user_content = ep.inputs.get("message") or json.dumps(ep.inputs)
    return {
        "messages": [
            {"role": "system", "content": ep.system_prompt},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": json.dumps(ep.action)},
        ],
        "meta": {
            "task_id": ep.task_id,
            "source_agent": ep.agent_id,
            "score": ep.score,
        },
    }


def distillation_records(
    episodes: Iterator[Episode], only_passing: bool = True
) -> Iterator[Dict[str, Any]]:
    """Yield chat-format records for episodes (passing only, by default)."""
    for ep in episodes:
        if only_passing and not ep.passed:
            continue
        yield distillation_record(ep)


class TraceStore:
    """Append-only JSONL store for episodes."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._count = 0

    def record(self, ep: Episode) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(ep.to_json() + "\n")
        self._count += 1

    def __len__(self) -> int:
        return self._count

    def all(self) -> Iterator[Episode]:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield Episode(**json.loads(line))

    def export_distillation_jsonl(
        self, out_path: str, only_passing: bool = True
    ) -> int:
        """Write chat-format SFT/distillation records. Returns count written."""
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        written = 0
        with open(out_path, "w", encoding="utf-8") as out:
            for record in distillation_records(self.all(), only_passing=only_passing):
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
        return written


class MemoryTraceStore(TraceStore):
    """In-memory episode store with the same interface as ``TraceStore``.

    Lets the vendored ``TrainingCamp`` / ``ImprovementLoop`` record episodes
    without touching disk; the service layer then persists them to the DB. This
    keeps the trainer/loop code unchanged while making the fold-in stateless.
    """

    def __init__(self) -> None:  # noqa: D107 — deliberately skips file setup
        self.path = "<memory>"
        self._episodes: List[Episode] = []
        self._count = 0

    def record(self, ep: Episode) -> None:
        self._episodes.append(ep)
        self._count += 1

    def all(self) -> Iterator[Episode]:
        return iter(self._episodes)

    @property
    def episodes(self) -> List[Episode]:
        return list(self._episodes)
