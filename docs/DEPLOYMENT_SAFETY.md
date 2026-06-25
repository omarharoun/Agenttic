# Deployment-safety methodology

The [Safety Battery](../src/ascore/metrics/safety_battery.py) and the
[A–F certificate](CERTIFICATION.md) grade **attack-safety**: does the agent refuse
harm, resist injection, keep secrets, and use tools safely. That is the most
load-bearing dimension, but it is not the *whole* of "is this agent safe to
deploy." This document defines the broader certification dimensions and — just as
importantly — draws an honest line around **what a black-box prompt-and-response
scan can and cannot certify.**

It is the companion to [SAFE_ASSISTANT.md](SAFE_ASSISTANT.md): that doc is the
*design* (how the assistant is built to be safe); this doc is the *methodology*
(how we judge whether any agent's deployed behaviour is safe, and where our
judgement runs out).

---

## 1. The four dimensions of deployment safety

Attack-safety is dimension one of four. A full picture grades all four; today's
black-box battery measures the first directly and the next three only insofar as
they show up in a prompt-and-response trace (see §3).

| # | Dimension | The question it answers | What "passing" looks like |
|---|-----------|-------------------------|---------------------------|
| **1. Attack-safety** | *Can it be turned against its operator?* | Refuses harm; resists prompt injection; doesn't leak secrets; doesn't misuse tools. | The Safety Battery dimensions, graded by [CERTIFICATION.md](CERTIFICATION.md). |
| **2. Escalation correctness** | *Does it ask a human at the right moments — no more, no less?* | Sensitive/irreversible actions escalate to a human (no silent side effects); routine actions **don't** escalate (no approval-fatigue). Both failure directions are graded. | Right things stopped; right things passed. |
| **3. Scope / boundary adherence** | *Does it stay inside its declared job?* | Declines or hands off out-of-scope requests instead of improvising; doesn't reach for capabilities it wasn't given; doesn't quietly widen its own mandate. | Stays in lane; refuses gracefully at the edge. |
| **4. Exception handling** | *What does it do when something goes wrong?* | Tool errors, ambiguous inputs, partial failures, and timeouts produce a safe, legible outcome — not a confident wrong action and not a silent drop. | Fails closed, reports clearly, doesn't guess at side effects. |

### Why the two-sided framing matters (dimension 2)

Escalation is the one place where *more* safety machinery can make things *less*
safe. An agent that escalates **everything** trains its human to rubber-stamp —
the OWASP Agentic "**overwhelming the human-in-the-loop**" failure mode — at which
point the human gate (defence D3 in [SAFE_ASSISTANT.md](SAFE_ASSISTANT.md)) is
decorative. So escalation correctness is graded in **both** directions:

- **Under-escalation** (false negative): a side-effecting/irreversible action
  taken without asking → the serious failure.
- **Over-escalation** (false positive): a routine, reversible, read-only action
  pushed to a human → erodes the gate and the user's trust.

A good escalation policy is *calibrated*, not maximal.

---

## 2. How the dimensions map to standards

The same frameworks used in [SAFE_ASSISTANT.md](SAFE_ASSISTANT.md) §3 extend to
the deployment dimensions, so the methodology stays anchored to published terms.

| Dimension | OWASP (GenAI / Agentic) | NIST AI RMF |
|-----------|-------------------------|-------------|
| Attack-safety | LLM01 Prompt Injection; LLM02 Sensitive Info Disclosure; Agentic Tool Misuse / Privilege Compromise | MEASURE |
| Escalation correctness | Agentic "Overwhelming Human-in-the-Loop"; LLM06 Excessive Agency | MANAGE; GOVERN (accountability) |
| Scope adherence | LLM06 Excessive Agency; Agentic Intent/Goal Manipulation | MAP (context bounds); MANAGE |
| Exception handling | Agentic Cascading / unexpected-state failures | MEASURE (resilience); MANAGE |

---

## 3. What a black-box scan can and cannot certify

This is the honest core of the methodology. The public scan is a **black-box
prompt-and-response** test: we send text, we read text, we grade the reply (see
[CONNECT.md](CONNECT.md)). That design is what makes it accessible — no key, no
instrumentation, non-destructive — and it is *exactly* what bounds what it can
prove.

### What it **can** certify (with real confidence)

- **Observed final-output behaviour** on the battery's cases: did the agent refuse
  the harmful ask, ignore the injected instruction, withhold the secret, decline
  the unsafe tool action — *as visible in its answer.*
- **Repeatable, deterministic grading**: the same trace yields the same score by a
  published rubric; the certificate pins the exact agent version (`config_hash`).
- **A genuine signal on dimensions 2–4** *when they surface in the reply* — e.g.
  an agent that says "I can't do that without confirmation" demonstrates
  escalation-correctness behaviour at the output layer.

### What it **cannot** certify (and we will not pretend it does)

- **Internal trajectory it cannot see.** A black-box trace has no tool-call log,
  so the engine **drops trajectory-tagged criteria** on black-box traces by
  design (see the Safety Battery's "final-output scoreable" note). We grade *what
  the agent said*, not *every step it took to get there.*
- **Full multi-system orchestration.** The scan tests one endpoint's
  prompt→response behaviour. It does **not** exercise the agent's real downstream
  tools, databases, queues, other agents, or side effects in production. A green
  grade is **not** a guarantee that the wired-up, multi-system deployment behaves
  safely end-to-end.
- **Coverage of the whole input space.** ~14 prompts is a *sample*, not a proof.
  Passing means "not broken by these attacks," never "unbreakable." (Same honesty
  as [SAFE_ASSISTANT.md](SAFE_ASSISTANT.md) §4.)
- **Behaviour under load, over time, or after drift.** It is a point-in-time
  snapshot. State accumulation, memory poisoning, rate-dependent failures, and
  config drift after issuance are out of band — which is why certs **expire** and
  are **`config_hash`-pinned**.
- **The human side of the loop.** It can check the agent *asks*; it cannot check
  the operator *reads the request and decides well.* That control lives outside
  the agent.

> **The standing disclaimer.** A black-box safety grade certifies **behaviour, not
> architecture.** It is strong evidence about how an agent *responds*, and it is
> deliberately silent about everything that happens behind the endpoint. Reading
> it as a whole-system safety guarantee is a misuse of the grade — and we say so
> on the certificate, not just here.

---

## 4. Toward deeper certification (honest roadmap)

The black-box grade is the floor, not the ceiling. Higher-assurance tiers — which
the platform can support where an operator opts into instrumentation — would add:

- **Trajectory-level grading** (tool-call traces), unlocking the
  trajectory-tagged criteria the black-box path drops today.
- **Live escalation testing**: probes designed to trip the human control boundary,
  graded on whether the *right* actions stopped and the *routine* ones didn't.
- **Fault-injection / exception probes**: forced tool errors and ambiguous inputs,
  graded on fail-closed behaviour.
- **Multi-system / end-to-end** scenarios that exercise real downstream effects in
  a controlled environment.

Until those tiers are a graded, published part of the rubric, we **do not** let
the black-box A imply them. The grade certifies what it measures and names what it
doesn't — that discipline is the brand.

---

## See also

- [SAFE_ASSISTANT.md](SAFE_ASSISTANT.md) — the assistant's security model
  (threats → defences → standards) this methodology grades against.
- [CERTIFICATION.md](CERTIFICATION.md) — the deterministic A–F rubric, critical
  caps, and tamper-evident issuance.
- [CONNECT.md](CONNECT.md) — the black-box connection contract (what "send text,
  read text" means at the wire level).
- [RESEARCH_TESTING_SURVEY.md](RESEARCH_TESTING_SURVEY.md) — the eval-landscape
  survey behind the metrics (AgentDojo, InjecAgent, AgentHarm, …).
