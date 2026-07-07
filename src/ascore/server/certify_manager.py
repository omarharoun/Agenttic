"""Background runner for certification jobs (SPEC-2 T14.6).

Certifying an agent runs a full elicitation matrix, so the HTTP request can't
block on it. ``CertifyManager`` launches an asyncio task per job and tracks
status in memory; durability comes from the persisted dossier (like the A/B
manager). The route returns a ``job_id`` to poll.
"""

from __future__ import annotations

import asyncio
import uuid


class CertifyManager:
    def __init__(self, cfg: dict, reg, clients: dict | None = None):
        self.cfg = cfg
        self.reg = reg
        self.clients = clients or {}
        self._jobs: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, *, agent_id: str, profile_id: str, variant: str = "reference",
              url: str = "", system_prompt: str = "", clients: dict | None = None,
              tenant: str = "default") -> str:
        job_id = uuid.uuid4().hex[:12]
        self._jobs[job_id] = {"job_id": job_id, "status": "running",
                              "agent_id": agent_id, "profile_id": profile_id,
                              "dossier_id": None, "tier": None, "error": None}
        run_clients = clients or self.clients
        task = asyncio.create_task(self._run(
            job_id, agent_id, profile_id, variant, url, system_prompt,
            run_clients, tenant))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return job_id

    async def _run(self, job_id, agent_id, profile_id, variant, url,
                   system_prompt, clients, tenant):
        from ascore.certification.certify import certify
        client = (clients or {}).get("agent")
        judge = (clients or {}).get("judge") or client
        try:
            res = await certify(
                self.cfg, self.reg, agent_id=agent_id, profile_id=profile_id,
                variant=variant, url=url, system_prompt=system_prompt,
                client=client, judge_client=judge, tenant=tenant)
            self._jobs[job_id].update(
                status="succeeded", dossier_id=res.dossier.dossier_id,
                tier=res.dossier.tier_decision.tier, cached=res.cached,
                cost_usd=res.cost_usd)
        except Exception as exc:  # noqa: BLE001 — surface as a failed job, not a 500
            self._jobs[job_id].update(
                status="failed", error=f"{type(exc).__name__}: {exc}")

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)
