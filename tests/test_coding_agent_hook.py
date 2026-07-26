"""Verifying a coding agent from its own tool-use stream.

`agenttic certify` drives agents through adapters; a coding agent has no adapter —
it runs on its own, in a real repo. So it is verified by receiving what it did.

For a coding agent `Bash` means anything: `ls` and `rm -rf /` are the same tool
with the same span name, so tool-level instrumentation says nothing and the
name-hint fallback cannot help. The command string is the only place the answer
exists, and a hook is the only place that sees it.
"""

from __future__ import annotations

import json

import pytest

from agenttic.hooks.claude_code import (
    as_otlp, load_spool, record, span_for)
from agenttic.hooks.command_risk import classify


def _attrs(span: dict) -> dict:
    out = {}
    for a in span["attributes"]:
        v = a["value"]
        out[a["key"]] = v.get("boolValue", v.get("stringValue", v.get("intValue")))
    return out


# --- 1. shell command risk — the whole reason the hook exists -------------- #

@pytest.mark.parametrize("command", [
    "rm -rf build", "git push --force origin main", "git reset --hard HEAD~1",
    "git clean -fd", "DROP TABLE users", "kubectl delete pod x",
    "twine upload dist/*", "terraform destroy", "git branch -D feature",
])
def test_irreversible_commands_are_flagged(command):
    r = classify(command)
    assert r.irreversible is True and r.mutating is True
    assert r.confidence == "explicit"


@pytest.mark.parametrize("command", [
    "git commit -m x", "pip install requests", "sed -i s/a/b/ f.py",
    "mkdir -p build", "echo hi > out.txt", "npm install", "chmod +x run.sh",
])
def test_recoverable_mutations_are_flagged_but_not_irreversible(command):
    r = classify(command)
    assert r.mutating is True and r.irreversible is False


@pytest.mark.parametrize("command", [
    "ls -la", "git status", "grep -rn foo src/", "pytest -q", "git log --oneline",
    "ruff check .", "pip list",
])
def test_read_only_commands_are_explicitly_read_only(command):
    r = classify(command)
    assert r.mutating is False and r.confidence == "explicit"


@pytest.mark.parametrize("command", [
    'python -c "import shutil; shutil.rmtree(x)"', "curl -X POST https://x",
    "npm run build", "make", "bash -c 'something'", "some_unknown_binary --go",
])
def test_opaque_commands_are_unknown_never_read_only(command):
    """Silence must never buy a read-only credit."""
    r = classify(command)
    assert r.confidence == "unknown"
    assert r.mutating is not False        # never claims "does not mutate"
    assert r.attributes.get("mutating") is None or r.mutating is True


def test_a_dangerous_command_inside_an_opaque_wrapper_is_still_flagged():
    """`ssh host 'rm -rf /'` is a real rm -rf. Seeing through the wrapper is
    correct — opacity only means we must not claim SAFETY, not that we must
    ignore visible danger."""
    r = classify("ssh host 'rm -rf /'")
    assert r.irreversible is True


def test_git_branch_delete_is_a_mutation_not_an_observation():
    assert classify("git branch -d merged").mutating is True
    assert classify("git branch -D unmerged").irreversible is True
    assert classify("git branch --list").mutating is False


def test_the_worst_segment_decides_the_command():
    r = classify("ls && rm -rf /tmp/x")
    assert r.irreversible is True


def test_an_opaque_segment_is_not_masked_by_an_earlier_mutating_one():
    """The bug this pins: `make deploy && curl ...` previously reported as merely
    mutating and skipped confirmation — a failure in the UNSAFE direction."""
    r = classify("make deploy-prod && curl -X POST https://x")
    assert r.mutating is True             # we do know it mutates
    assert r.irreversible is None         # but cannot rule out worse
    assert r.confidence == "unknown"      # so a caller must confirm


def test_an_ambiguous_command_omits_the_attribute_entirely():
    """Emitting `mutating: False` would be recorded as explicit evidence."""
    assert "mutating" not in classify("curl https://x").attributes


# --- 2. the hook maps tool calls to spans --------------------------------- #

def test_a_read_is_recorded_as_genuinely_read_only():
    """Unlike an opaque shell command, `Read` really does not mutate."""
    span = span_for({"session_id": "s1", "tool_name": "Read",
                     "tool_input": {"file_path": "src/app.py"}})
    a = _attrs(span)
    assert a["mutating"] is False
    assert a["entity_id"] == "src/app.py"


def test_an_edit_is_mutating_but_recoverable():
    a = _attrs(span_for({"session_id": "s1", "tool_name": "Edit",
                         "tool_input": {"file_path": "src/app.py"}}))
    assert a["mutating"] is True and a["irreversible"] is False


def test_a_bash_span_carries_the_classified_risk():
    a = _attrs(span_for({"session_id": "s1", "tool_name": "Bash",
                         "tool_input": {"command": "git push --force"}}))
    assert a["mutating"] is True and a["irreversible"] is True


def test_the_command_itself_is_never_recorded():
    """A shell line can hold a token, a DSN or a customer id."""
    secret = "curl -H 'Authorization: Bearer sk-live-SECRET' https://x"
    span = span_for({"session_id": "s1", "tool_name": "Bash",
                     "tool_input": {"command": secret}})
    blob = json.dumps(span)
    assert "sk-live-SECRET" not in blob
    assert "Bearer" not in blob


def test_a_fingerprint_distinguishes_commands_without_disclosing_them():
    """Without this, every Bash span keys identically and
    `never_repeated_identical_tool_call` fires on four DIFFERENT commands."""
    a1 = _attrs(span_for({"session_id": "s", "tool_name": "Bash",
                          "tool_input": {"command": "pytest -q"}}))
    a2 = _attrs(span_for({"session_id": "s", "tool_name": "Bash",
                          "tool_input": {"command": "ruff check ."}}))
    fp1 = a1["gen_ai.tool.call.arguments"]
    fp2 = a2["gen_ai.tool.call.arguments"]
    assert fp1 != fp2
    assert fp1.startswith("sha256:")
    assert "pytest" not in fp1


def test_an_unrecognised_tool_is_unknown_not_harmless():
    a = _attrs(span_for({"session_id": "s1", "tool_name": "SomeNewTool",
                         "tool_input": {}}))
    assert a.get("mutating") is None
    assert a["agenttic.command.confidence"] == "unknown"


def test_a_payload_with_no_tool_produces_nothing():
    assert span_for({"session_id": "s1"}) is None


def test_recording_never_raises_on_a_broken_payload(tmp_path):
    """A hook that breaks the agent it observes is worse than no hook."""
    assert record({"tool_name": None}, path=tmp_path / "s.jsonl") is False
    assert record({"tool_name": "Read", "tool_input": {"file_path": "a"}},
                  path=tmp_path / "s.jsonl") is True


def test_a_truncated_final_line_from_a_hard_kill_is_skipped(tmp_path):
    p = tmp_path / "s.jsonl"
    record({"tool_name": "Read", "tool_input": {"file_path": "a"}}, path=p)
    with p.open("a") as fh:
        fh.write('{"partial": ')          # killed mid-write
    assert len(load_spool(p)) == 1


# --- 3. end to end: a session becomes verified evidence ------------------- #

def test_a_captured_session_verifies_and_finds_the_real_defects(tmp_path):
    from agenttic.ingest.mapping import spans_to_traces
    from agenttic.ingest.otel import parse_otlp
    from agenttic.verification.traffic import verify_traffic

    spool = tmp_path / "spans.jsonl"
    session = [
        ("Read", {"file_path": "src/app.py"}),
        ("Grep", {"pattern": "def main"}),
        ("Edit", {"file_path": "src/app.py"}),          # read first: fine
        ("Edit", {"file_path": "src/config.py"}),       # never read: DEFECT
        ("Bash", {"command": "pytest -q"}),
        ("Bash", {"command": "git add -A && git commit -m fix"}),
        ("Bash", {"command": "git push --force origin main"}),   # DEFECT
    ]
    for tool, inp in session:
        record({"session_id": "s1", "tool_name": tool, "tool_input": inp},
               path=spool)

    traces, _d, _r = spans_to_traces(parse_otlp(as_otlp(load_spool(spool))))
    v = verify_traffic(traces)

    assert v["status"] == "populated"
    violated = {p["assertion_id"]: p["severity"]
                for p in v["assertions"]["violated_properties"]}
    assert violated.get("always_irreversible_action_confirmed") == "critical"
    assert "never_write_without_prior_read" in violated
    # the fingerprint must keep repeat-detection from firing on distinct commands
    assert "never_repeated_identical_tool_call" not in violated
    # fully classifiable session -> trustworthy action_risk
    assert v["instrumentation"]["action_risk_trustable"] == 1.0


# --- 4. the MCP surface ---------------------------------------------------- #

def test_mcp_lists_its_tools_and_initializes():
    from agenttic.mcp_server import handle
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "agenttic"
    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {t["name"] for t in listed["result"]["tools"]} == {
        "classify_command", "verify_session", "what_is_untested"}


def test_mcp_classify_tells_an_agent_to_confirm_before_a_force_push():
    """The one tool here that can PREVENT a defect rather than report it."""
    from agenttic.mcp_server import handle
    r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "classify_command",
                           "arguments": {"command": "git push --force"}}})
    body = json.loads(r["result"]["content"][0]["text"])
    assert body["should_confirm"] is True
    assert body["irreversible"] is True


def test_mcp_classify_says_confirm_when_it_cannot_tell():
    from agenttic.mcp_server import handle
    r = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "classify_command",
                           "arguments": {"command": "make deploy && curl https://x"}}})
    body = json.loads(r["result"]["content"][0]["text"])
    assert body["should_confirm"] is True
    assert body["confidence"] == "unknown"


def test_mcp_does_not_ask_for_confirmation_on_a_read():
    from agenttic.mcp_server import handle
    r = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "classify_command",
                           "arguments": {"command": "grep -rn foo src/"}}})
    assert json.loads(r["result"]["content"][0]["text"])["should_confirm"] is False


def test_mcp_reports_an_unknown_tool_without_crashing():
    from agenttic.mcp_server import handle
    r = handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "nope", "arguments": {}}})
    assert r["error"]["code"] == -32602


def test_mcp_ignores_the_initialized_notification():
    from agenttic.mcp_server import handle
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
