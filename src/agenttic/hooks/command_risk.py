"""Classify a shell command's risk — the piece a coding agent cannot do without.

For a conversational agent, one tool name means one risk class. For a coding
agent, **`Bash` means anything**: `ls` and `rm -rf /` are the same tool with the
same span name. So tool-level instrumentation tells you nothing, and the
name-hint fallback in :mod:`agenttic.verification.builtins` cannot help — `Bash`
matches no write hint, so every shell call would land in the ``unknown``
confidence class and contribute no action-risk coverage at all.

The command string is the only place the answer exists, and a hook is the only
place that sees it. Hence this module.

**The rule that keeps it honest: when we cannot tell, we say nothing.**
:func:`classify` returns ``None`` for the ``mutating`` / ``irreversible`` flags on
an ambiguous command rather than guessing ``False``. A `False` would be recorded
as ``explicit`` evidence by the fidelity guard — a stated "this does not mutate" —
and claiming that about `python -c "..."` or `make deploy` would be a lie that
buys coverage credit. Silence is correctly penalised as ``unknown`` instead.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Vocabulary. Ordered most-destructive first — the first match wins.
# --------------------------------------------------------------------------- #

#: Commands that destroy or publish something no rollback recovers.
_IRREVERSIBLE = (
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)", "recursive/forced delete"),
    (r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset discards work"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*[fd]", "clean deletes untracked files"),
    (r"\bgit\s+branch\s+-D\b", "force branch delete"),
    (r"\bgit\s+filter-branch\b|\bgit\s+filter-repo\b", "history rewrite"),
    (r"(?i)\b(drop|truncate)\s+(table|database|schema)\b", "destructive DDL"),
    (r"(?i)\bdelete\s+from\b(?!.*\bwhere\b)", "unbounded DELETE"),
    (r"\bdd\s+.*\bof=", "raw device write"),
    (r"\bmkfs\b|\bshred\b|\bwipefs\b", "filesystem destruction"),
    (r"\bdocker\s+(system\s+prune|volume\s+rm|rmi)\b", "image/volume removal"),
    (r"\bkubectl\s+delete\b", "kubernetes delete"),
    (r"\bterraform\s+(destroy|apply)\b", "infrastructure change"),
    (r"\b(npm|yarn|pnpm)\s+publish\b", "package publish"),
    (r"\btwine\s+upload\b|\buv\s+publish\b", "package publish"),
    (r"\bgh\s+(release\s+create|repo\s+delete)\b", "release/repo mutation"),
    (r"\bsystemctl\s+(stop|disable|mask)\b", "service stop"),
    (r"\bkill\s+-9\b|\bpkill\b", "forced process kill"),
    (r"\balembic\s+(upgrade|downgrade)\b|\bmanage\.py\s+migrate\b", "db migration"),
)

#: Commands that change state but are recoverable (git, a re-install, a re-run).
_MUTATING = (
    (r"\bgit\s+(commit|add|rm|mv|checkout|switch|merge|rebase|stash|tag|apply|"
     r"cherry-pick|revert|restore)\b", "git write"),
    (r"\bgit\s+branch\s+-d\b", "branch delete"),
    (r"\bgit\s+push\b", "push"),
    (r"\brm\b|\bmv\b|\bmkdir\b|\btouch\b|\brmdir\b", "filesystem write"),
    (r"\bcp\b", "copy writes a destination"),
    (r"\bsed\s+.*-i\b|\bsed\s+-i\b", "in-place edit"),
    (r"\btee\b", "writes a file"),
    (r"\b(chmod|chown|ln)\b", "permission/link change"),
    (r"\b(npm|yarn|pnpm)\s+(install|add|remove|ci|update)\b", "dependency change"),
    (r"\b(pip|pip3|uv)\s+(install|uninstall|sync|add|remove)\b", "dependency change"),
    (r"\b(apt|apt-get|brew|dnf|yum|pacman)\s+(install|remove|upgrade)\b",
     "system package change"),
    (r"\bcargo\s+(add|remove|install|update)\b", "dependency change"),
    (r"\bgo\s+(get|mod\s+tidy)\b", "dependency change"),
    (r"(?i)\b(insert\s+into|update\s+\w+\s+set)\b", "SQL write"),
    (r"\bdocker\s+(run|build|compose\s+up|tag|load|push)\b", "container action"),
    (r"\bsystemctl\s+(start|restart|reload|enable)\b", "service change"),
    (r"\bmake\s+(install|deploy|publish|release)\b", "make target that ships"),
)

#: Commands that only observe. Anything not listed is AMBIGUOUS, not read-only.
_READ_ONLY = (
    r"\b(ls|cat|head|tail|wc|file|stat|du|df|pwd|whoami|hostname|uname|date|env|"
    r"printenv|which|type|echo|sort|uniq|cut|tr|column|basename|dirname|realpath)\b",
    r"\b(grep|rg|ag|ack|find|fd|locate|jq|yq|awk)\b",
    r"\bgit\s+(status|log|diff|show|blame|branch|remote|describe|rev-parse|"
    r"ls-files|config\s+--get|shortlog|tag\s*$|fetch)\b",
    r"\b(docker\s+(ps|images|logs|inspect)|kubectl\s+(get|describe|logs))\b",
    r"\b(pytest|npm\s+test|npm\s+run\s+test|cargo\s+test|go\s+test|tox|"
    r"vitest|jest)\b",
    r"\b(ruff|flake8|mypy|eslint|tsc|black\s+--check|prettier\s+--check)\b",
    r"\b(pip|uv)\s+(list|show|freeze|tree)\b",
    r"\b(head|tail)\b",
)

#: Redirections that write. Detected structurally, not by command name.
_REDIRECT_WRITE = re.compile(r"(?<![0-9<>])>{1,2}(?!&)")

#: Interpreters and wrappers whose effect is opaque from the command line. These
#: force ``unknown`` even if an inner token happens to look read-only.
_OPAQUE = re.compile(
    r"\b(python[0-9.]*\s+-c|node\s+-e|bash\s+-c|sh\s+-c|zsh\s+-c|eval|exec|"
    r"xargs|curl|wget|http|ssh|scp|rsync|nc|make|npm\s+run|yarn\s+run|"
    r"pnpm\s+run|just|task|invoke|nohup)\b")


@dataclass(frozen=True)
class CommandRisk:
    """The verdict. ``mutating``/``irreversible`` are tri-state ON PURPOSE:
    ``None`` means "cannot tell", which must never be recorded as ``False``."""

    mutating: bool | None
    irreversible: bool | None
    reason: str
    confidence: str            # "explicit" | "unknown"

    @property
    def attributes(self) -> dict:
        """Span attributes to emit. Omits what it does not know, so the ingest
        fidelity guard can flag the gap instead of being misled by a false
        ``False``."""
        out: dict = {"agenttic.command.risk_reason": self.reason}
        if self.mutating is not None:
            out["mutating"] = self.mutating
        if self.irreversible is not None:
            out["irreversible"] = self.irreversible
        return out


def _segments(command: str) -> list[str]:
    """Split on shell operators so `ls && rm -rf x` is judged on its worst part."""
    parts = re.split(r"&&|\|\||;|\||\n", command)
    return [p.strip() for p in parts if p.strip()]


def classify(command: str) -> CommandRisk:
    """Classify a shell command. The worst segment decides the whole command."""
    cmd = (command or "").strip()
    if not cmd:
        return CommandRisk(None, None, "empty command", "unknown")

    known_mutating = False
    mutating_reason = ""
    saw_readonly = False
    unclassified: list[str] = []

    for seg in _segments(cmd):
        # NOT lowercased: shell flags are case-sensitive and the difference
        # matters — `git branch -D` force-deletes an unmerged branch while `-d`
        # refuses to. Case-insensitivity is opted into per pattern via `(?i)`.
        text = seg

        # Worst case wins outright: nothing later can make this safer.
        for pattern, why in _IRREVERSIBLE:
            if re.search(pattern, text):
                return CommandRisk(True, True, why, "explicit")

        matched = False
        for pattern, why in _MUTATING:
            if re.search(pattern, text):
                known_mutating, mutating_reason, matched = True, why, True
                break
        if matched:
            continue

        if _REDIRECT_WRITE.search(seg):
            known_mutating = True
            mutating_reason = "shell redirection writes a file"
            continue

        # An opaque wrapper hides its effect. Every segment is checked for this,
        # even after another segment already matched as mutating — otherwise
        # `make deploy && curl ...` would report as merely mutating and skip
        # confirmation, which is a failure in the unsafe direction.
        if _OPAQUE.search(text):
            unclassified.append(
                f"{_head(seg)!r} is opaque (interpreter/network/runner)")
            continue

        if any(re.search(p, text) for p in _READ_ONLY):
            saw_readonly = True
            continue

        unclassified.append(f"{_head(seg)!r} unrecognised")

    if unclassified:
        # We may know it mutates, but we cannot rule out something worse, so
        # `irreversible` stays None and confidence stays unknown — which is what
        # makes a caller confirm.
        detail = "; ".join(unclassified[:3])
        if known_mutating:
            return CommandRisk(
                True, None,
                f"mutates ({mutating_reason}) AND contains unclassifiable "
                f"segment(s): {detail} — cannot rule out an irreversible effect",
                "unknown")
        return CommandRisk(None, None,
                           f"not classified: {detail}", "unknown")

    if known_mutating:
        return CommandRisk(True, False, mutating_reason, "explicit")
    if saw_readonly:
        return CommandRisk(False, False, "read-only command", "explicit")
    return CommandRisk(None, None, "not classified", "unknown")


def _head(segment: str) -> str:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    return tokens[0] if tokens else segment[:24]
