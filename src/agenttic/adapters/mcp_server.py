"""MCP server adapter — treat the SERVER as the device under test (SPEC-12 Step 55).

MCP is becoming the enterprise tool interface and essentially nobody certifies
the servers. This is a minimal, dependency-free MCP client (JSON-RPC 2.0) that
speaks both transports the spec requires:

* **stdio** — spawn the server process, exchange newline-delimited JSON-RPC.
* **streaming HTTP** — POST JSON-RPC to an endpoint (uses ``httpx``, already a
  project dependency).

It performs protocol discovery (``initialize`` → ``tools/list``) so each tool's
declared schema becomes test material for the certification battery.

Deliberately no ``mcp`` SDK dependency: the certifier must be able to probe a
*misbehaving* server (malformed frames, crashes, protocol violations) without an
SDK normalising the very faults we are trying to detect.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    """Transport/protocol level failure (not a tool-level error result)."""


@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    #: annotations the server may declare, e.g. destructiveHint/readOnlyHint
    annotations: dict = field(default_factory=dict)

    @property
    def declares_mutating(self) -> bool:
        """Does the tool disclose that it has side effects? (side-effect
        disclosure check). True when it says so via annotations or description."""
        a = {k.lower(): v for k, v in (self.annotations or {}).items()}
        if a.get("destructivehint") is True or a.get("mutating") is True:
            return True
        if a.get("readonlyhint") is False:
            return True
        text = f"{self.description}".lower()
        return any(w in text for w in
                   ("writes", "modifies", "mutates", "deletes", "creates",
                    "side effect", "side-effect", "transfers", "sends"))


@dataclass
class ToolResult:
    """The outcome of one ``tools/call``."""

    ok: bool
    text: str = ""
    raw: dict = field(default_factory=dict)
    is_error: bool = False
    error_code: int | None = None
    error_message: str = ""
    crashed: bool = False        # transport died / server exited
    latency_ms: float = 0.0

    @property
    def typed_error(self) -> bool:
        """A *typed* error (JSON-RPC error or isError result) rather than a
        crash or an unhandled exception leaking through."""
        return (self.is_error or self.error_code is not None) and not self.crashed


class MCPClient:
    """Minimal MCP client. Use as a context manager."""

    def __init__(self, *, command: list[str] | None = None, url: str = "",
                 timeout: float = 10.0, env: dict | None = None):
        if not command and not url:
            raise ValueError("MCPClient needs either a stdio command or an http url")
        self.command = command
        self.url = url
        self.timeout = timeout
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self.server_info: dict = {}
        self.capabilities: dict = {}

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "MCPClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> dict:
        if self.command:
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1, env=self.env)
        res = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "agenttic-certifier", "version": "1"},
        })
        self.server_info = (res or {}).get("serverInfo", {}) or {}
        self.capabilities = (res or {}).get("capabilities", {}) or {}
        self.notify("notifications/initialized")
        return res or {}

    def close(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    @property
    def alive(self) -> bool:
        if self._proc is None:
            return bool(self.url)
        return self._proc.poll() is None

    # -- transport ---------------------------------------------------------
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send_stdio(self, payload: dict, *, expect_reply: bool) -> dict | None:
        assert self._proc is not None
        if self._proc.poll() is not None:
            raise MCPError("server process is not running")
        line = json.dumps(payload) + "\n"
        try:
            self._proc.stdin.write(line)      # type: ignore[union-attr]
            self._proc.stdin.flush()          # type: ignore[union-attr]
        except (BrokenPipeError, ValueError) as e:
            raise MCPError(f"server closed its input: {e}")
        if not expect_reply:
            return None
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            raw = self._proc.stdout.readline()  # type: ignore[union-attr]
            if raw == "":
                raise MCPError("server closed its output (crashed or exited)")
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                raise MCPError(f"server emitted a non-JSON frame: {raw[:200]!r}")
            if msg.get("id") == payload.get("id"):
                return msg
            # notifications / unrelated frames: keep reading
        raise MCPError(f"timeout waiting for a reply to {payload.get('method')}")

    def _send_http(self, payload: dict, *, expect_reply: bool) -> dict | None:
        import httpx
        try:
            r = httpx.post(self.url, json=payload, timeout=self.timeout)
        except Exception as e:
            raise MCPError(f"http transport failure: {e}")
        if not expect_reply:
            return None
        if r.status_code >= 500:
            raise MCPError(f"server returned {r.status_code}")
        try:
            return r.json()
        except Exception:
            raise MCPError(f"server returned non-JSON: {r.text[:200]!r}")

    def _send(self, payload: dict, *, expect_reply: bool = True) -> dict | None:
        if self.command:
            return self._send_stdio(payload, expect_reply=expect_reply)
        return self._send_http(payload, expect_reply=expect_reply)

    def request(self, method: str, params: dict | None = None) -> dict:
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method,
               "params": params or {}}
        reply = self._send(msg) or {}
        if "error" in reply:
            err = reply["error"] or {}
            raise MCPError(
                f"{method} failed: [{err.get('code')}] {err.get('message', '')}")
        return reply.get("result", {}) or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}},
                       expect_reply=False)
        except MCPError:
            pass          # notifications are best-effort by definition

    def send_raw(self, raw: str) -> str:
        """Write a raw (possibly malformed) frame — used by the fuzzing check to
        prove the server rejects garbage without dying."""
        if not self.command:
            raise MCPError("raw frames are a stdio-only probe")
        assert self._proc is not None
        self._proc.stdin.write(raw + "\n")    # type: ignore[union-attr]
        self._proc.stdin.flush()              # type: ignore[union-attr]
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            line = self._proc.stdout.readline()  # type: ignore[union-attr]
            if line == "":
                raise MCPError("server closed its output after a malformed frame")
            if line.strip():
                return line.strip()
        raise MCPError("timeout after a malformed frame")

    # -- discovery + calls -------------------------------------------------
    def list_tools(self) -> list[MCPTool]:
        res = self.request("tools/list")
        out: list[MCPTool] = []
        for t in res.get("tools", []) or []:
            out.append(MCPTool(
                name=t.get("name", ""), description=t.get("description", "") or "",
                input_schema=t.get("inputSchema", {}) or {},
                annotations=t.get("annotations", {}) or {}))
        return out

    def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        started = time.time()
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
               "params": {"name": name, "arguments": arguments or {}}}
        try:
            reply = self._send(msg) or {}
        except MCPError as e:
            return ToolResult(ok=False, crashed=True, error_message=str(e),
                              latency_ms=(time.time() - started) * 1000)
        ms = (time.time() - started) * 1000
        if "error" in reply:
            err = reply["error"] or {}
            return ToolResult(ok=False, raw=reply, is_error=True,
                              error_code=err.get("code"),
                              error_message=str(err.get("message", "")),
                              latency_ms=ms)
        result = reply.get("result", {}) or {}
        text = _content_text(result)
        return ToolResult(ok=not result.get("isError", False), text=text,
                          raw=result, is_error=bool(result.get("isError", False)),
                          error_message=text if result.get("isError") else "",
                          latency_ms=ms)


def _content_text(result: dict) -> str:
    parts = []
    for item in result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif isinstance(item, str):
            parts.append(item)
    if not parts and result:
        parts.append(json.dumps(result, sort_keys=True))
    return "\n".join(parts)


def connect_stdio(command: list[str], **kw: Any) -> MCPClient:
    return MCPClient(command=command, **kw)


def connect_http(url: str, **kw: Any) -> MCPClient:
    return MCPClient(url=url, **kw)
