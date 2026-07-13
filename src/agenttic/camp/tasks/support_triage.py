"""
A concrete task: customer-support triage.

The agent reads an incoming message and must output:
    {"category": ..., "priority": ..., "action": ...}

Cases come from templates. Most are "common" (a keyword-rules agent handles them),
but a minority are "edge" cases where the right *action* is not the obvious one —
e.g. a suspicious-login ticket should escalate to security, not just reset a
password. Those edge cases are what keep a naive agent below a 99% floor, which
is exactly what makes the guardrail demo meaningful.

The grader is deterministic: an attempt passes only if BOTH category and action
are correct. Priority contributes to a continuous score but not to pass/fail.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from ..task import Case, GradeResult

CATEGORIES = ("billing", "technical", "account", "other")
PRIORITIES = ("normal", "high", "urgent")


@dataclass
class _Template:
    weight: float
    category: str
    priority: str
    action: str
    phrasings: Tuple[str, ...]


# Each phrasing is a full customer message.
_TEMPLATES: List[_Template] = [
    # ---- Common cases: obvious category -> obvious action ---------------------
    _Template(20, "billing", "normal", "issue_refund", (
        "I was charged twice for my subscription this month, please refund one.",
        "You billed me after I cancelled. I want a refund for that payment.",
    )),
    _Template(20, "technical", "high", "escalate_to_engineering", (
        "The app crashes with a 500 error every time I open my dashboard.",
        "Export is completely broken, it fails with a timeout every time.",
    )),
    _Template(20, "account", "normal", "reset_password", (
        "I forgot my password and can't log in, can you help me reset it?",
        "I need to reset my password, the login page won't accept the old one.",
    )),
    _Template(16, "other", "normal", "answer_faq", (
        "Do you have a mobile app, and which countries do you support?",
        "What are your business hours and where can I find your docs?",
    )),

    # ---- Edge cases: category is clear, but the obvious action is WRONG -------
    # Suspicious login -> security, NOT a plain password reset.
    _Template(5, "account", "urgent", "escalate_to_security", (
        "There was a login from a device I don't recognize and my 2fa is off.",
        "I think my account was hacked, someone accessed it and changed my email.",
    )),
    # Billing how-to -> answer FAQ, NOT a refund.
    _Template(5, "billing", "normal", "answer_billing_faq", (
        "How do I update the credit card on my subscription before the next charge?",
        "Where can I download an invoice for last month's payment for accounting?",
    )),
    # Vague slowness -> ask for repro, NOT an engineering escalation yet.
    _Template(4, "technical", "normal", "ask_for_repro", (
        "The app feels a bit slow sometimes but I'm not sure when it happens.",
        "Occasionally things seem laggy, no error though, hard to say when.",
    )),
]

_TOTAL_WEIGHT = sum(t.weight for t in _TEMPLATES)

_FILLERS = ("", "Hi team, ", "Hello, ", "Quick one: ", "Please help — ")


class SupportTriageTask:
    task_id = "support_triage"
    name = "Customer Support Triage"

    def system_prompt(self) -> str:
        return (
            "You are a support triage agent. Read the customer's message and reply "
            "with a JSON object containing exactly: category (one of "
            "billing|technical|account|other), priority (normal|high|urgent), and "
            "action. Choose the single best next action; do not default to refunds "
            "or password resets when the situation calls for escalation or an FAQ."
        )

    def _pick_template(self, rng: random.Random) -> _Template:
        r = rng.uniform(0, _TOTAL_WEIGHT)
        acc = 0.0
        for t in _TEMPLATES:
            acc += t.weight
            if r <= acc:
                return t
        return _TEMPLATES[-1]

    def sample_case(self, rng: random.Random) -> Case:
        t = self._pick_template(rng)
        phrasing = rng.choice(t.phrasings)
        message = rng.choice(_FILLERS) + phrasing
        return Case(
            case_id="%08x" % rng.getrandbits(32),
            inputs={"message": message},
            gold={"category": t.category, "priority": t.priority, "action": t.action},
        )

    def grade(self, case: Case, action: Dict[str, Any]) -> GradeResult:
        gold = case.gold
        cat_ok = action.get("category") == gold["category"]
        act_ok = action.get("action") == gold["action"]
        pri_ok = action.get("priority") == gold["priority"]

        passed = cat_ok and act_ok
        score = 0.4 * cat_ok + 0.4 * act_ok + 0.2 * pri_ok

        return GradeResult(
            passed=passed,
            score=score,
            detail={
                "category": {"got": action.get("category"), "want": gold["category"], "ok": cat_ok},
                "action": {"got": action.get("action"), "want": gold["action"], "ok": act_ok},
                "priority": {"got": action.get("priority"), "want": gold["priority"], "ok": pri_ok},
            },
        )
