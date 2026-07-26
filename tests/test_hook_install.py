"""Installing into someone's assistant config.

Printing a JSON snippet and saying "merge this" is homework, not an install. But
editing a file a person relies on daily has to be reversible and must never lose
what was already there — so these pin the merge, not the happy path.
"""

from __future__ import annotations

import json

from agenttic.hooks.install import (
    HOOK_COMMAND, MCP_ENTRY, MCP_SERVER_NAME, install_hook, install_mcp,
    uninstall_hook, uninstall_mcp)


def _write(p, data):
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


# --- the tool-use hook ----------------------------------------------------- #

def test_installs_into_a_file_that_does_not_exist_yet(tmp_path):
    s = tmp_path / "settings.json"
    res = install_hook(s)
    assert res.action == "installed"
    assert res.backup is None                      # nothing existed to back up
    got = json.loads(s.read_text())
    assert got["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == HOOK_COMMAND


def test_never_loses_settings_that_were_already_there(tmp_path):
    """The failure that would actually hurt someone."""
    s = _write(tmp_path / "settings.json", {
        "model": "opus", "theme": "dark",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "my-own-guard"}]}]},
    })
    install_hook(s)
    got = json.loads(s.read_text())
    assert got["model"] == "opus" and got["theme"] == "dark"
    assert got["permissions"]["allow"] == ["Bash(ls:*)"]
    assert got["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "my-own-guard"
    assert got["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == HOOK_COMMAND


def test_keeps_other_peoples_posttooluse_hooks(tmp_path):
    s = _write(tmp_path / "settings.json", {"hooks": {"PostToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "formatter"}]}]}})
    install_hook(s)
    cmds = [h["command"] for m in json.loads(s.read_text())["hooks"]["PostToolUse"]
            for h in m["hooks"]]
    assert "formatter" in cmds and HOOK_COMMAND in cmds


def test_running_it_twice_does_not_register_twice(tmp_path):
    s = tmp_path / "settings.json"
    install_hook(s)
    second = install_hook(s)
    assert second.action == "already"
    cmds = [h["command"] for m in json.loads(s.read_text())["hooks"]["PostToolUse"]
            for h in m["hooks"]]
    assert cmds.count(HOOK_COMMAND) == 1


def test_backs_the_file_up_before_touching_it(tmp_path):
    s = _write(tmp_path / "settings.json", {"model": "opus"})
    res = install_hook(s)
    assert res.backup is not None and res.backup.exists()
    assert json.loads(res.backup.read_text()) == {"model": "opus"}


def test_refuses_to_overwrite_a_file_it_cannot_parse(tmp_path):
    """Better to stop than to replace something we do not understand."""
    s = tmp_path / "settings.json"
    s.write_text("{ this is not json", encoding="utf-8")
    res = install_hook(s)
    assert res.action == "error"
    assert "not valid JSON" in res.detail
    assert s.read_text() == "{ this is not json"          # untouched


def test_uninstall_removes_only_our_entry(tmp_path):
    s = _write(tmp_path / "settings.json", {"hooks": {"PostToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "formatter"}]}]}})
    install_hook(s)
    uninstall_hook(s)
    cmds = [h["command"] for m in json.loads(s.read_text())["hooks"]["PostToolUse"]
            for h in m["hooks"]]
    assert cmds == ["formatter"]


def test_uninstall_is_a_no_op_when_we_were_never_installed(tmp_path):
    s = _write(tmp_path / "settings.json", {"model": "opus"})
    assert uninstall_hook(s).action == "skipped"


# --- the MCP server -------------------------------------------------------- #

def test_registers_the_mcp_server(tmp_path):
    c = tmp_path / "claude.json"
    res = install_mcp("claude-code", path_override=c)
    assert res.action == "installed"
    assert json.loads(c.read_text())["mcpServers"][MCP_SERVER_NAME] == MCP_ENTRY


def test_keeps_other_mcp_servers(tmp_path):
    c = _write(tmp_path / "claude.json", {"mcpServers": {
        "github": {"command": "gh-mcp"}}, "otherSetting": 1})
    install_mcp("claude-code", path_override=c)
    got = json.loads(c.read_text())
    assert got["mcpServers"]["github"] == {"command": "gh-mcp"}
    assert got["mcpServers"][MCP_SERVER_NAME] == MCP_ENTRY
    assert got["otherSetting"] == 1


def test_mcp_install_is_idempotent(tmp_path):
    c = tmp_path / "claude.json"
    install_mcp("claude-code", path_override=c)
    assert install_mcp("claude-code", path_override=c).action == "already"


def test_mcp_uninstall_leaves_the_others(tmp_path):
    c = _write(tmp_path / "claude.json", {"mcpServers": {"github": {"command": "x"}}})
    install_mcp("claude-code", path_override=c)
    uninstall_mcp("claude-code", path_override=c)
    servers = json.loads(c.read_text())["mcpServers"]
    assert "github" in servers and MCP_SERVER_NAME not in servers


def test_an_unknown_client_is_refused_rather_than_guessed(tmp_path):
    res = install_mcp("some-editor", path_override=tmp_path / "x.json")
    assert res.action == "error" and "unknown target" in res.detail
