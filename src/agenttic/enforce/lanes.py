"""Inline enforcement lanes (SPEC-2 T24.1 / T24.2).

* **Lane 1** (≤ ``lane1_budget_ms``) — deterministic checks over the policy's
  lane-1 rules: allow/deny lists, action-class rules (write/read from config),
  config-driven argument matchers, an egress allowlist (reusing the SSRF
  validator), and per-session rate/budget ceilings. A deny names the rule and the
  matched pattern in its evidence.
* **Lane 2** (≤ ``lane2_budget_ms``) — pluggable classifiers: an injection screen
  on tool *results* (quarantine-tag, original preserved) and a secret/PII
  redaction transform on outbound *args* (T24.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class LaneResult:
    action: str                       # allow | deny | transform | require_approval | ...
    evidence: list[str] = field(default_factory=list)
    action_class: str = "unknown"
    fail_open: bool = False
    preserved_ref: str | None = None
    preserved_original: object = None  # the untouched original (gateway persists it)
    transformed: object = None         # the safe replacement (quarantine-tagged / redacted)


# --------------------------------------------------------------------------- #
# Action-class resolution (from config).
# --------------------------------------------------------------------------- #


def action_class_of(tool_name: str, cfg: dict) -> str:
    classes = (cfg or {}).get("enforcement", {}).get("action_classes", {})
    if tool_name in classes.get("write", []):
        return "write"
    if tool_name in classes.get("read", []):
        return "read"
    return "unknown"


# --------------------------------------------------------------------------- #
# Lane 1.
# --------------------------------------------------------------------------- #


def _matches(matcher: dict, tool_name: str, data, action_class: str,
             cfg: dict) -> tuple[bool, str]:
    """Does ``matcher`` match this call? Returns (matched, pattern_desc)."""
    # tool name(s)
    if "tool" in matcher:
        if tool_name != matcher["tool"]:
            return False, ""
    if "tools" in matcher:
        if tool_name not in matcher["tools"]:
            return False, ""
    # action class
    if "action_class" in matcher:
        if action_class != matcher["action_class"]:
            return False, ""
    # argument matcher: {arg: "url", contains|pattern|equals: ...}
    if "arg" in matcher:
        val = ""
        if isinstance(data, dict):
            val = str(data.get(matcher["arg"], ""))
        if "contains" in matcher and matcher["contains"] not in val:
            return False, ""
        if "equals" in matcher and val != matcher["equals"]:
            return False, ""
        if "pattern" in matcher:
            try:
                if not re.search(matcher["pattern"], val):
                    return False, ""
            except re.error:
                return False, ""
        desc = f"arg:{matcher['arg']}~{matcher.get('pattern') or matcher.get('contains') or matcher.get('equals')}"
        return True, desc
    # a bare tool/class/tools matcher with nothing else is a match on those alone
    if any(k in matcher for k in ("tool", "tools", "action_class")):
        return True, matcher.get("tool") or matcher.get("action_class") or "tools"
    return False, ""


def _egress_blocked(tool_name: str, data, cfg: dict) -> tuple[bool, str]:
    """Validate an egress URL arg against the SSRF policy (reuse). Returns
    (blocked, reason)."""
    if not isinstance(data, dict):
        return False, ""
    url = data.get("url") or data.get("endpoint") or ""
    if not url:
        return False, ""
    from ascore.security import validate_blackbox_url
    try:
        validate_blackbox_url(url, cfg=cfg, resolve=False)
        return False, ""
    except Exception as exc:  # noqa: BLE001 — any validation failure blocks egress
        return True, f"egress:{type(exc).__name__}"


def lane1_evaluate(session, phase: str, tool_name: str, data, cfg: dict):
    """Deterministic Lane-1 evaluation. Returns a :class:`LaneResult` when a rule
    decides, else None (fall through to Lane 2)."""
    action_class = action_class_of(tool_name, cfg)
    rules = [r for r in session.policy.rules if r.lane == "lane1"]

    deny_hit = None
    allow_hit = None
    for rule in rules:
        matched, pattern = _matches(rule.matcher, tool_name, data, action_class, cfg)
        # egress allowlist rule (reuses SSRF)
        if rule.matcher.get("egress") and phase == "tool_call":
            blocked, reason = _egress_blocked(tool_name, data, cfg)
            if blocked:
                return LaneResult(
                    action="deny", action_class=action_class,
                    evidence=[f"{rule.ref()}:pattern={reason}"])
            matched = matched or False
        # rate / budget ceiling per session per tool
        if "max_calls" in rule.matcher and (matched or rule.matcher.get("tool") == tool_name):
            counts = session.metadata.setdefault("_call_counts", {})
            counts[tool_name] = counts.get(tool_name, 0) + 1
            if counts[tool_name] > int(rule.matcher["max_calls"]):
                return LaneResult(
                    action="deny", action_class=action_class,
                    evidence=[f"{rule.ref()}:pattern=max_calls>{rule.matcher['max_calls']}"])
        if not matched:
            continue
        if rule.action in ("deny", "terminate_session", "revoke_access"):
            deny_hit = (rule, pattern)
            break  # most-restrictive wins immediately
        if rule.action == "allow" and allow_hit is None:
            allow_hit = (rule, pattern)
        if rule.action in ("require_approval", "transform") and deny_hit is None:
            return LaneResult(action=rule.action, action_class=action_class,
                              evidence=[f"{rule.ref()}:pattern={pattern}"])

    if deny_hit is not None:
        rule, pattern = deny_hit
        return LaneResult(action=rule.action, action_class=action_class,
                          evidence=[f"{rule.ref()}:pattern={pattern}"])
    if allow_hit is not None:
        rule, pattern = allow_hit
        return LaneResult(action="allow", action_class=action_class,
                          evidence=[f"{rule.ref()}:pattern={pattern}"])
    return None


# --------------------------------------------------------------------------- #
# Lane 2 — pluggable classifiers (T24.2).
# --------------------------------------------------------------------------- #

#: phrases that signal a prompt-injection payload embedded in a tool result
_INJECTION_MARKERS = (
    "ignore previous instructions", "ignore all previous", "disregard your",
    "system prompt", "you are now", "new instructions:", "override your",
    "exfiltrate", "send all", "reveal your instructions", "print your system",
)

#: secret / PII patterns to redact from OUTBOUND args
_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{8,}"), "api_key"),
    (re.compile(r"AKIA[0-9A-Z]{8,}"), "aws_key"),
    (re.compile(r"(?i)(aws_secret|api[_-]?key|password|secret)\s*[=:]\s*\S+"), "secret_assignment"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{10,}"), "bearer_token"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "email"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn"),
]


def _flatten_text(data) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return " ".join(str(v) for v in data.values())
    if isinstance(data, (list, tuple)):
        return " ".join(str(v) for v in data)
    return str(data) if data is not None else ""


def screen_injection(result) -> tuple[bool, list[str]]:
    """Injection screen for tool RESULTS. Returns (is_injection, markers). A
    result that merely *describes* resisting an injection is not flagged."""
    text = _flatten_text(result).lower()
    if not text:
        return False, []
    try:
        from ascore.metrics.injection_detect import resisted
        if resisted(text):
            return False, []
    except Exception:  # noqa: BLE001
        pass
    hits = [m for m in _INJECTION_MARKERS if m in text]
    return (len(hits) > 0), hits


def redact_secrets(args) -> tuple[bool, object, list[str]]:
    """Redact secrets/PII from OUTBOUND args. Returns (changed, redacted, kinds)."""
    kinds: list[str] = []

    def _redact_str(s: str) -> str:
        out = s
        for pat, kind in _SECRET_PATTERNS:
            if pat.search(out):
                kinds.append(kind)
                out = pat.sub("[REDACTED]", out)
        return out

    if isinstance(args, str):
        red = _redact_str(args)
        return (red != args), red, kinds
    if isinstance(args, dict):
        red = {}
        changed = False
        for k, v in args.items():
            if isinstance(v, str):
                nv = _redact_str(v)
                changed = changed or (nv != v)
                red[k] = nv
            else:
                red[k] = v
        return changed, red, kinds
    return False, args, kinds


def lane2_evaluate(session, phase: str, tool_name: str, data, cfg: dict):
    """Lane-2 classifiers. Injection screen on results ⇒ quarantine-tag (original
    preserved); secret/PII redaction on outbound args ⇒ transform (original
    preserved). Returns a :class:`LaneResult` transform, or None (allow)."""
    if phase == "tool_result":
        is_injection, markers = screen_injection(data)
        if is_injection:
            quarantined = {"quarantined": True, "reason": "prompt_injection_detected",
                           "markers": markers}
            return LaneResult(
                action="transform", action_class="read",
                evidence=[f"lane2:injection:{','.join(markers[:3])}"],
                preserved_original=data, transformed=quarantined)
        return None

    # tool_call: redact secrets/PII from outbound args
    changed, redacted, kinds = redact_secrets(data)
    if changed:
        return LaneResult(
            action="transform", action_class=action_class_of(tool_name, cfg),
            evidence=[f"lane2:redact:{','.join(sorted(set(kinds)))}"],
            preserved_original=data, transformed=redacted)
    return None
