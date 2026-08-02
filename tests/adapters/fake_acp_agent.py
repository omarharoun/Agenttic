"""A fake ACP agent, for testing the client offline.

Speaks the real wire protocol — JSON-RPC 2.0 over stdio, the methods and field
names read off ``agent-client-protocol`` 0.8.1 — so the client is exercised
against the protocol rather than against a mock of itself. Behaviour is chosen
by ``ACP_FAKE_MODE`` so one script covers the interesting failure shapes:

    normal      a task with two tool calls, one of which fails, then an answer
    refuse      stops with stopReason=refusal
    permission  asks the client for permission before acting
    garbage     emits a non-JSON frame, then behaves
    crash       exits mid-session
    hang        never answers session/prompt
    noauth      demands authentication before session/new
"""

from __future__ import annotations

import json
import os
import sys
import time

MODE = os.environ.get("ACP_FAKE_MODE", "normal")


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reply(rid, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": rid, "result": result})


def error(rid, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def update(session_id: str, upd: dict) -> None:
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": session_id, "update": upd}})


def main() -> None:
    next_id = 1000
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, rid = msg.get("method"), msg.get("id")

        if method == "initialize":
            if MODE == "garbage":
                sys.stdout.write("Starting up, please wait...\n")
                sys.stdout.flush()
            out = {"protocolVersion": 1,
                   "agentInfo": {"name": "fake", "version": "0.1"},
                   "agentCapabilities": {"promptCapabilities": {"image": False}}}
            if MODE == "noauth":
                out["authMethods"] = [{"id": "oauth", "name": "OAuth"}]
            reply(rid, out)

        elif method == "authenticate":
            reply(rid, {})

        elif method == "session/new":
            if MODE == "noauth":
                error(rid, -32000, "Authentication required")
            else:
                reply(rid, {"sessionId": "sess-1"})

        elif method == "session/prompt":
            sid = (msg.get("params") or {}).get("sessionId") or "sess-1"
            if MODE == "hang":
                time.sleep(3600)
            if MODE == "crash":
                update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1",
                             "title": "read the file", "kind": "read",
                             "status": "pending", "rawInput": {"path": "a.py"}})
                sys.exit(3)
            if MODE == "permission":
                next_id += 1
                send({"jsonrpc": "2.0", "id": next_id,
                      "method": "session/request_permission",
                      "params": {"sessionId": sid,
                                 "toolCall": {"toolCallId": "t9", "kind": "delete",
                                              "title": "rm -rf build"},
                                 "options": [
                                     {"optionId": "y", "name": "Allow",
                                      "kind": "allow_once"},
                                     {"optionId": "n", "name": "Reject",
                                      "kind": "reject_once"}]}})
                # Wait for the client's answer before finishing the turn.
                for reply_line in sys.stdin:
                    if reply_line.strip():
                        got = json.loads(reply_line)
                        update(sid, {"sessionUpdate": "agent_message_chunk",
                                     "content": {"type": "text", "text":
                                                 json.dumps(got.get("result"))}})
                        break
                reply(rid, {"stopReason": "end_turn"})
                continue
            if MODE == "refuse":
                update(sid, {"sessionUpdate": "agent_message_chunk",
                             "content": {"type": "text",
                                         "text": "I will not do that."}})
                reply(rid, {"stopReason": "refusal"})
                continue

            update(sid, {"sessionUpdate": "user_message_chunk",
                         "content": {"type": "text", "text": "the task"}})
            # An unmodelled member of the union — must survive, unscored.
            update(sid, {"sessionUpdate": "plan",
                         "entries": [{"content": "look around", "status": "pending"}]})
            # Two calls in flight, answered in REVERSE order.
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1",
                         "title": "read calc.py", "kind": "read",
                         "status": "pending", "rawInput": {"path": "calc.py"}})
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t2",
                         "title": "edit calc.py", "kind": "edit",
                         "status": "pending", "rawInput": {"path": "calc.py"}})
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t2",
                         "status": "failed", "kind": "edit",
                         "rawOutput": {"error": "permission denied"}})
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1",
                         "status": "completed", "kind": "read",
                         "rawOutput": {"text": "def add(a, b): return a - b"}})
            if MODE == "garbage":
                sys.stdout.write("not json at all\n")
                sys.stdout.write('{"truncated": \n')
                sys.stdout.flush()
            update(sid, {"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": "Fixed the operator."}})
            reply(rid, {"stopReason": "end_turn",
                        "usage": {"inputTokens": 120, "outputTokens": 45,
                                  "totalTokens": 165}})
        elif rid is not None:
            error(rid, -32601, f"unknown method {method}")


if __name__ == "__main__":
    main()
