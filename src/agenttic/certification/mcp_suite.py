"""MCP server certification battery (SPEC-12 Step 55).

The server is the device under test. Each check is deterministic and yields a
0/1 score with a human-readable detail, so a failure names the specific defect
(schema drift, a leak, missing authz, a non-idempotent write) rather than a
vague "failed".

Scored checks:
  contract_schema        declared input schemas are honored
  golden_responses       responses pinned per server version — schema drift is a
                         breaking change caught here, not in production
  input_fuzzing          malformed / boundary / oversized input -> typed error,
                         never a crash or an unhandled exception
  authorization          scope-escalation / bypass attempts must NOT succeed
  error_taxonomy         errors leak no stack traces, secrets, or internal paths
  idempotency            repeated identical writes with a client key don't duplicate
  rate_limit             limits enforced and signalled, not silent failure
  side_effect_disclosure mutating tools declare that they mutate
  response_injection     server content engineered to hijack the calling agent
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agenttic.adapters.mcp_server import MCPClient, MCPTool

# ---- leak + injection detectors ------------------------------------------- #

#: things an error message must never contain.
_LEAK_PATTERNS = (
    (re.compile(r"Traceback \(most recent call last\)"), "python stack trace"),
    (re.compile(r'File "[^"]+", line \d+'), "source file path + line"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9]{8,}|api[_-]?key\s*[=:]\s*\S+)"), "api key"),
    (re.compile(r"(?i)\b(password|secret|token)\s*[=:]\s*\S+"), "credential"),
    (re.compile(r"(?:/home/|/var/|/etc/|C:\\\\)[\w./\\-]+"), "internal filesystem path"),
    (re.compile(r"(?i)\bat [\w.$]+\([\w.]+\.java:\d+\)"), "java stack trace"),
)

#: tool-response content engineered to hijack the CALLING agent.
_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore (all |your |previous |prior )*(instructions|prompt)"),
    re.compile(r"(?i)disregard (the |your |all )*(above|previous|instructions)"),
    re.compile(r"(?i)\bnew (system )?(instructions?|prompt)\b"),
    re.compile(r"(?i)you (are|must) now\b"),
    re.compile(r"(?i)\bcall (the )?(tool |function )?[\w.]*(transfer_funds|delete_|exfiltrat|send_money)"),
    re.compile(r"(?i)</?(system|instructions)>"),
    re.compile(r"(?i)do not tell the user"),
)


def detect_leak(text: str) -> str | None:
    for pattern, label in _LEAK_PATTERNS:
        if pattern.search(text or ""):
            return label
    return None


def detect_response_injection(text: str) -> str | None:
    for pattern in _INJECTION_PATTERNS:
        m = pattern.search(text or "")
        if m:
            return m.group(0)[:80]
    return None


# ---- outcomes -------------------------------------------------------------- #

@dataclass
class CheckOutcome:
    check_id: str
    score: float                 # 1.0 pass, 0.0 fail
    detail: str
    critical: bool = False
    skipped: bool = False

    @property
    def passed(self) -> bool:
        return self.skipped or self.score >= 1.0


@dataclass
class MCPServerReport:
    server_name: str
    server_version: str
    transport: str
    tools: list[str] = field(default_factory=list)
    outcomes: list[CheckOutcome] = field(default_factory=list)

    @property
    def scored(self) -> list[CheckOutcome]:
        return [o for o in self.outcomes if not o.skipped]

    @property
    def score(self) -> float:
        s = self.scored
        return (sum(o.score for o in s) / len(s)) if s else 0.0

    @property
    def failed(self) -> list[str]:
        return [o.check_id for o in self.scored if not o.passed]

    @property
    def passed(self) -> bool:
        # any critical failure fails the server outright
        return not self.failed

    def as_dict(self) -> dict:
        return {
            "server_name": self.server_name, "server_version": self.server_version,
            "transport": self.transport, "tools": self.tools,
            "score": round(self.score, 4), "passed": self.passed,
            "failed": self.failed,
            "checks": [{"check_id": o.check_id, "score": o.score,
                        "detail": o.detail, "critical": o.critical,
                        "skipped": o.skipped} for o in self.outcomes],
        }


# ---- golden responses ------------------------------------------------------ #

def response_shape(result_text: str) -> str:
    """A stable shape fingerprint of a tool response: JSON key structure when the
    payload is JSON, else a coarse text descriptor. Schema drift changes this;
    ordinary value changes do not."""
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return "text"

    def shape(v):
        if isinstance(v, dict):
            return {k: shape(v[k]) for k in sorted(v)}
        if isinstance(v, list):
            return [shape(v[0])] if v else []
        return type(v).__name__
    return json.dumps(shape(data), sort_keys=True)


def record_goldens(client: MCPClient, probes: list[tuple[str, dict]]) -> dict:
    """Pin the response shape of each probe for the CURRENT server version."""
    out: dict[str, str] = {}
    for name, args in probes:
        res = client.call_tool(name, args)
        out[f"{name}:{json.dumps(args, sort_keys=True)}"] = response_shape(res.text)
    return {"server_version": client.server_info.get("version", ""), "shapes": out}


def check_goldens(client: MCPClient, goldens: dict,
                  probes: list[tuple[str, dict]]) -> CheckOutcome:
    if not goldens:
        return CheckOutcome("golden_responses", 0.0, "no goldens pinned", skipped=True)
    drift: list[str] = []
    for name, args in probes:
        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        expected = goldens.get("shapes", {}).get(key)
        if expected is None:
            continue
        actual = response_shape(client.call_tool(name, args).text)
        if actual != expected:
            drift.append(f"{name}: shape {expected} -> {actual}")
    if drift:
        return CheckOutcome(
            "golden_responses", 0.0,
            f"schema drift vs pinned v{goldens.get('server_version','?')}: "
            + "; ".join(drift), critical=True)
    return CheckOutcome("golden_responses", 1.0,
                        f"responses match goldens pinned for "
                        f"v{goldens.get('server_version','?')}")


# ---- the battery ----------------------------------------------------------- #

def valid_args(tool: MCPTool) -> dict:
    """Minimal VALID arguments derived from a tool's declared input schema, so
    probes that are not about validation (rate limit, injection) exercise the
    real code path instead of tripping the validator."""
    schema = tool.input_schema or {}
    props = schema.get("properties") or {}
    out: dict = {}
    for name in schema.get("required") or list(props)[:1]:
        spec = props.get(name) or {}
        kind = spec.get("type", "string")
        out[name] = {"string": "status", "integer": 1, "number": 1.0,
                     "boolean": True, "array": [], "object": {}}.get(kind, "status")
    return out


def check_contract(client: MCPClient, tools: list[MCPTool]) -> CheckOutcome:
    """Every tool declares a usable input schema, and a valid call honors it."""
    problems = []
    for t in tools:
        schema = t.input_schema or {}
        if schema.get("type") != "object" or "properties" not in schema:
            problems.append(f"{t.name}: no object inputSchema with properties")
    if problems:
        return CheckOutcome("contract_schema", 0.0, "; ".join(problems), critical=True)
    return CheckOutcome("contract_schema", 1.0,
                        f"{len(tools)} tool(s) declare object input schemas")


def check_fuzzing(client: MCPClient, tools: list[MCPTool]) -> CheckOutcome:
    """Malformed / boundary / oversized inputs must produce a TYPED error and
    leave the server alive — never a crash or an unhandled exception."""
    if not tools:
        return CheckOutcome("input_fuzzing", 0.0, "no tools to fuzz", skipped=True)
    target = tools[0]
    payloads = [
        ("missing required args", {}),
        ("wrong type", {k: [] for k in (target.input_schema.get("properties") or {"x": {}})}),
        ("oversized", {next(iter(target.input_schema.get("properties") or {"x": {}})): "A" * 100_000}),
        ("unknown field", {"__nonexistent__": "x"}),
    ]
    for label, args in payloads:
        res = client.call_tool(target.name, args)
        if res.crashed or not client.alive:
            return CheckOutcome(
                "input_fuzzing", 0.0,
                f"server died on {label} input ({res.error_message[:120]})",
                critical=True)
        if res.ok:
            # accepting garbage silently is a contract violation
            return CheckOutcome(
                "input_fuzzing", 0.0,
                f"server ACCEPTED {label} input instead of returning a typed error",
                critical=True)
    return CheckOutcome("input_fuzzing", 1.0,
                        f"{len(payloads)} malformed inputs each returned a typed error")


def check_authorization(client: MCPClient, gated: list[tuple[str, dict]]) -> CheckOutcome:
    """Permission-gated tools must refuse unauthorized calls. A successful
    escalation scores 0."""
    if not gated:
        return CheckOutcome("authorization", 0.0, "no gated tools declared", skipped=True)
    escalated = []
    for name, args in gated:
        res = client.call_tool(name, args)
        if res.ok:
            escalated.append(name)
    if escalated:
        return CheckOutcome("authorization", 0.0,
                            "scope escalation SUCCEEDED on: " + ", ".join(escalated),
                            critical=True)
    return CheckOutcome("authorization", 1.0,
                        f"{len(gated)} unauthorized call(s) correctly refused")


def check_error_taxonomy(client: MCPClient, tools: list[MCPTool]) -> CheckOutcome:
    """Errors must not leak stack traces, secrets, or internal paths."""
    if not tools:
        return CheckOutcome("error_taxonomy", 0.0, "no tools", skipped=True)
    res = client.call_tool(tools[0].name, {"__force_error__": True})
    blob = f"{res.error_message}\n{res.text}"
    leak = detect_leak(blob)
    if leak:
        return CheckOutcome("error_taxonomy", 0.0,
                            f"error response leaked {leak}", critical=True)
    return CheckOutcome("error_taxonomy", 1.0, "errors carry no leaked internals")


def check_idempotency(client: MCPClient, write_tool: str | None,
                      args: dict | None = None) -> CheckOutcome:
    """Repeating an identical write with the same client key must not duplicate
    the effect."""
    if not write_tool:
        return CheckOutcome("idempotency", 0.0, "no write tool declared", skipped=True)
    payload = dict(args or {})
    payload.setdefault("idempotency_key", "agenttic-cert-key-1")
    first = client.call_tool(write_tool, payload)
    second = client.call_tool(write_tool, payload)
    if not first.ok:
        return CheckOutcome("idempotency", 0.0,
                            f"write failed outright: {first.error_message[:120]}")
    if response_shape(first.text) != response_shape(second.text) or first.text != second.text:
        return CheckOutcome(
            "idempotency", 0.0,
            "repeated identical write produced a DIFFERENT result — the effect "
            f"was duplicated ({first.text[:60]!r} -> {second.text[:60]!r})",
            critical=True)
    return CheckOutcome("idempotency", 1.0,
                        "repeated identical write was de-duplicated by the client key")


def check_rate_limit(client: MCPClient, tools: list[MCPTool], *,
                     burst: int = 25) -> CheckOutcome:
    """A limit must be enforced AND signalled — backpressure, not silent failure
    or a crash."""
    if not tools:
        return CheckOutcome("rate_limit", 0.0, "no tools", skipped=True)
    tool = tools[0]
    args = valid_args(tool)
    signalled = False
    for _ in range(burst):
        res = client.call_tool(tool.name, args)
        if res.crashed:
            return CheckOutcome("rate_limit", 0.0,
                                "server crashed under a burst instead of "
                                "signalling backpressure", critical=True)
        blob = f"{res.error_message} {res.text}".lower()
        if res.typed_error and ("rate" in blob or "too many" in blob
                                or res.error_code == -32029):
            signalled = True
            break
    if not signalled:
        return CheckOutcome("rate_limit", 0.0,
                            f"no rate limit signalled across {burst} rapid calls "
                            "(unbounded, or failing silently)")
    return CheckOutcome("rate_limit", 1.0, "rate limit enforced and signalled")


def check_side_effect_disclosure(tools: list[MCPTool],
                                 known_mutating: list[str]) -> CheckOutcome:
    """Tools that mutate must say so."""
    if not known_mutating:
        return CheckOutcome("side_effect_disclosure", 0.0,
                            "no mutating tools declared", skipped=True)
    by_name = {t.name: t for t in tools}
    undisclosed = [n for n in known_mutating
                   if n in by_name and not by_name[n].declares_mutating]
    if undisclosed:
        return CheckOutcome("side_effect_disclosure", 0.0,
                            "mutating tools do not disclose side effects: "
                            + ", ".join(undisclosed), critical=True)
    return CheckOutcome("side_effect_disclosure", 1.0,
                        "every mutating tool discloses its side effects")


def check_response_injection(client: MCPClient, tools: list[MCPTool]) -> CheckOutcome:
    """The novel, highest-value probe: does the SERVER return content engineered
    to hijack the calling agent? Scored on the server's honesty."""
    if not tools:
        return CheckOutcome("response_injection", 0.0, "no tools", skipped=True)
    hits = []
    for t in tools:
        res = client.call_tool(t.name, valid_args(t))
        found = detect_response_injection(res.text)
        if found:
            hits.append(f"{t.name}: {found!r}")
    if hits:
        return CheckOutcome(
            "response_injection", 0.0,
            "server returned agent-hijacking content: " + "; ".join(hits),
            critical=True)
    return CheckOutcome("response_injection", 1.0,
                        "no instruction-injection content in tool responses")


# ---- the entry point -------------------------------------------------------- #

def certify_mcp_server(
    client: MCPClient,
    *,
    goldens: dict | None = None,
    golden_probes: list[tuple[str, dict]] | None = None,
    gated_calls: list[tuple[str, dict]] | None = None,
    write_tool: str | None = None,
    write_args: dict | None = None,
    known_mutating: list[str] | None = None,
    rate_limit_burst: int = 25,
) -> MCPServerReport:
    """Run the full battery against a connected MCP server."""
    tools = client.list_tools()
    report = MCPServerReport(
        server_name=client.server_info.get("name", "unknown"),
        server_version=str(client.server_info.get("version", "")),
        transport="stdio" if client.command else "http",
        tools=[t.name for t in tools],
    )
    report.outcomes.append(check_contract(client, tools))
    report.outcomes.append(
        check_goldens(client, goldens or {}, golden_probes or []))
    report.outcomes.append(check_fuzzing(client, tools))
    report.outcomes.append(check_authorization(client, gated_calls or []))
    report.outcomes.append(check_error_taxonomy(client, tools))
    report.outcomes.append(check_idempotency(client, write_tool, write_args))
    report.outcomes.append(
        check_side_effect_disclosure(tools, known_mutating or []))
    report.outcomes.append(check_response_injection(client, tools))
    # the rate-limit burst runs LAST: it deliberately exhausts the server's
    # budget, which would starve any probe scheduled after it.
    report.outcomes.append(check_rate_limit(client, tools, burst=rate_limit_burst))
    return report


def manifest_for_server(report: MCPServerReport, *, manifest_id: str,
                        signing_tier: str = "local_self_attested", **kw):
    """Attach an MCP server report to a signed-able evidence manifest (Step 54),
    naming the server + version as the subject — so a gateway can require
    certified servers (Hard Rule 54: the supply chain is certified, not assumed)."""
    from agenttic.certification.attest import build_manifest
    from agenttic.schema.attestation import content_hash
    doc = report.as_dict()
    return build_manifest(
        manifest_id=manifest_id,
        agent_id=f"mcp:{report.server_name}",
        agent_config_hash=content_hash(
            {"server": report.server_name, "version": report.server_version,
             "tools": sorted(report.tools)}),
        suite_id="mcp-server-certification", suite_version=1,
        rubric_id="mcp-server-battery", rubric_version=1,
        scorecard=doc, visibility_tier="glass_box",
        signing_tier=signing_tier,
        scope_statement=(
            f"Attests the MCP server {report.server_name} v{report.server_version} "
            f"({report.transport}) was measured against the MCP certification "
            f"battery: {', '.join(o.check_id for o in report.scored)}."),
        **kw)
