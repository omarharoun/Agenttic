"""Write ourselves into an assistant's settings — carefully.

Printing a JSON snippet and telling someone to merge it by hand is not an
install; it is homework. But editing a config file that a person relies on every
day is the kind of change that has to be reversible and must never lose anything
that was already there.

So: read, merge, back up, write. Never clobber a settings file, never duplicate an
entry we already added, and always leave a `.bak-*` beside the original so the
change can be undone with one `mv`.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The tool-use hook we register, and the MCP entry we register.
HOOK_COMMAND = "agenttic hook claude-code"
MCP_SERVER_NAME = "agenttic"
MCP_ENTRY = {"command": "agenttic", "args": ["mcp"]}

CLAUDE_SETTINGS = "~/.claude/settings.json"
#: Where each client keeps its MCP servers, and under which key.
MCP_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "claude-code": ("~/.claude.json", ("mcpServers",)),
    "claude-desktop-linux": (
        "~/.config/Claude/claude_desktop_config.json", ("mcpServers",)),
    "claude-desktop-mac": (
        "~/Library/Application Support/Claude/claude_desktop_config.json",
        ("mcpServers",)),
    "cursor": ("~/.cursor/mcp.json", ("mcpServers",)),
    "windsurf": ("~/.codeium/windsurf/mcp_config.json", ("mcpServers",)),
}


class InstallResult:
    """What actually happened, so the caller can report it truthfully."""

    def __init__(self, path: Path, action: str, backup: Path | None = None,
                 detail: str = ""):
        self.path = path
        self.action = action        # "installed" | "already" | "skipped" | "error"
        self.backup = backup
        self.detail = detail

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return f"<InstallResult {self.action} {self.path}>"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _backup(path: Path) -> Path | None:
    """Copy the file aside before touching it. No backup, no write."""
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = path.with_suffix(path.suffix + f".bak-agenttic-{stamp}")
    shutil.copy2(path, dest)
    return dest


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# the tool-use hook
# --------------------------------------------------------------------------- #

def install_hook(settings_path: str | Path = CLAUDE_SETTINGS) -> InstallResult:
    """Register the PostToolUse hook, preserving every hook already configured."""
    path = Path(settings_path).expanduser()
    try:
        data = _load(path)
    except json.JSONDecodeError as exc:
        return InstallResult(path, "error",
                             detail=f"{path} is not valid JSON ({exc}); refusing "
                                    "to overwrite a file we cannot parse")

    hooks = data.setdefault("hooks", {})
    post = hooks.setdefault("PostToolUse", [])
    if not isinstance(post, list):
        return InstallResult(path, "error",
                             detail="hooks.PostToolUse is not a list; leaving it alone")

    for matcher in post:
        for h in (matcher or {}).get("hooks", []) or []:
            if h.get("command") == HOOK_COMMAND:
                return InstallResult(path, "already",
                                     detail="the hook is already registered")

    backup = _backup(path)
    post.append({"matcher": "*", "hooks": [
        {"type": "command", "command": HOOK_COMMAND}]})
    _write(path, data)
    return InstallResult(path, "installed", backup)


def uninstall_hook(settings_path: str | Path = CLAUDE_SETTINGS) -> InstallResult:
    """Remove only our own entry, and any matcher block it emptied."""
    path = Path(settings_path).expanduser()
    try:
        data = _load(path)
    except json.JSONDecodeError as exc:
        return InstallResult(path, "error", detail=str(exc))

    post = (data.get("hooks") or {}).get("PostToolUse")
    if not isinstance(post, list):
        return InstallResult(path, "skipped", detail="no PostToolUse hooks present")

    kept, removed = [], 0
    for matcher in post:
        hs = [h for h in (matcher or {}).get("hooks", []) or []
              if h.get("command") != HOOK_COMMAND]
        removed += len((matcher or {}).get("hooks", []) or []) - len(hs)
        if hs:
            kept.append({**matcher, "hooks": hs})
    if not removed:
        return InstallResult(path, "skipped", detail="our hook was not registered")

    backup = _backup(path)
    data["hooks"]["PostToolUse"] = kept
    _write(path, data)
    return InstallResult(path, "installed", backup, detail=f"removed {removed} entry")


# --------------------------------------------------------------------------- #
# the MCP server
# --------------------------------------------------------------------------- #

def detect_mcp_targets() -> list[tuple[str, Path]]:
    """Config files that actually exist on this machine.

    We only offer to edit files a client has already created — writing a config
    for an app that is not installed would be litter.
    """
    out = []
    for name, (raw, _keys) in MCP_TARGETS.items():
        p = Path(raw).expanduser()
        if p.exists():
            out.append((name, p))
    return out


def install_mcp(target: str = "claude-code",
                path_override: str | Path | None = None) -> InstallResult:
    """Register `agenttic mcp` as an MCP server for one client."""
    if target not in MCP_TARGETS:
        return InstallResult(Path("-"), "error",
                             detail=f"unknown target {target!r}; known: "
                                    + ", ".join(sorted(MCP_TARGETS)))
    raw, keys = MCP_TARGETS[target]
    path = Path(path_override).expanduser() if path_override else Path(raw).expanduser()
    try:
        data = _load(path)
    except json.JSONDecodeError as exc:
        return InstallResult(path, "error",
                             detail=f"{path} is not valid JSON ({exc}); refusing "
                                    "to overwrite a file we cannot parse")

    node = data
    for k in keys:
        node = node.setdefault(k, {})
    if not isinstance(node, dict):
        return InstallResult(path, "error",
                             detail=f"{'.'.join(keys)} is not an object")

    if node.get(MCP_SERVER_NAME) == MCP_ENTRY:
        return InstallResult(path, "already", detail="already registered")

    backup = _backup(path)
    node[MCP_SERVER_NAME] = dict(MCP_ENTRY)
    _write(path, data)
    return InstallResult(path, "installed", backup)


def uninstall_mcp(target: str = "claude-code",
                  path_override: str | Path | None = None) -> InstallResult:
    raw, keys = MCP_TARGETS.get(target, (None, None))
    if raw is None:
        return InstallResult(Path("-"), "error", detail=f"unknown target {target!r}")
    path = Path(path_override).expanduser() if path_override else Path(raw).expanduser()
    try:
        data = _load(path)
    except json.JSONDecodeError as exc:
        return InstallResult(path, "error", detail=str(exc))

    node = data
    for k in keys:
        node = node.get(k) if isinstance(node, dict) else None
        if node is None:
            return InstallResult(path, "skipped", detail="not registered")
    if MCP_SERVER_NAME not in node:
        return InstallResult(path, "skipped", detail="not registered")

    backup = _backup(path)
    node.pop(MCP_SERVER_NAME)
    _write(path, data)
    return InstallResult(path, "installed", backup, detail="removed")
