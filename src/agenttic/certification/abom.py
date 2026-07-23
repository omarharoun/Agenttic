"""Agent BOM — the supply-chain half of a certificate (SPEC-12 Step 54.3).

Emits a CycloneDX document enumerating everything the agent's behaviour depends
on: model ids + parameters, prompt hashes, tool / MCP-server identities and
versions, the suite + rubric versions it was measured against, the harness
version, and dependencies. The ABOM is referenced from the evidence manifest by
hash (``abom_sha256``) and signed with it, so "which tools was this agent
allowed to call, at which versions" is part of the attested evidence rather
than a claim made after the fact.

CycloneDX is emitted directly as JSON (no extra dependency) and validated against
a bundled structural schema with ``jsonschema`` — which the project already ships.
ML/AI component types come from CycloneDX 1.6 (``machine-learning-model``,
``data``).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from agenttic.schema.attestation import canonical_json, content_hash

SPEC_VERSION = "1.6"

#: Structural schema for the subset of CycloneDX we emit. Deliberately strict on
#: the fields a relying party needs and permissive elsewhere, so a valid ABOM is
#: also a valid CycloneDX document.
CYCLONEDX_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["bomFormat", "specVersion", "version", "metadata", "components"],
    "properties": {
        "bomFormat": {"const": "CycloneDX"},
        "specVersion": {"type": "string"},
        "serialNumber": {"type": "string", "pattern": "^urn:uuid:"},
        "version": {"type": "integer", "minimum": 1},
        "metadata": {
            "type": "object",
            "required": ["timestamp", "component"],
            "properties": {
                "timestamp": {"type": "string"},
                "tools": {"type": "array"},
                "component": {
                    "type": "object",
                    "required": ["type", "name"],
                    "properties": {
                        "type": {"type": "string"},
                        "name": {"type": "string"},
                        "version": {"type": "string"},
                    },
                },
            },
        },
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "name", "bom-ref"],
                "properties": {
                    "type": {"enum": ["application", "library", "framework", "data",
                                       "machine-learning-model", "service", "file"]},
                    "bom-ref": {"type": "string"},
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "hashes": {"type": "array"},
                    "properties": {"type": "array"},
                },
            },
        },
        "dependencies": {"type": "array"},
    },
}


def _prop(name: str, value: Any) -> dict:
    return {"name": name, "value": str(value)}


def _sha256_component(text: str) -> list[dict]:
    return [{"alg": "SHA-256",
             "content": hashlib.sha256(text.encode("utf-8")).hexdigest()}]


def build_abom(
    *,
    subject_name: str,
    subject_version: str = "",
    model_ids: list[str] | None = None,
    model_parameters: dict[str, Any] | None = None,
    prompts: dict[str, str] | None = None,       # name -> prompt text (hashed, never inlined)
    tools: list[dict] | None = None,             # {name, version?, source?, mutating?}
    mcp_servers: list[dict] | None = None,       # {name, version?, transport?, url?}
    suite: tuple[str, int] | None = None,
    rubric: tuple[str, int] | None = None,
    harness_version: str = "",
    dependencies: list[dict] | None = None,      # {name, version}
    timestamp: datetime | None = None,
    serial_uuid: str | None = None,
) -> dict:
    """Build the CycloneDX ABOM document. Prompts are recorded by HASH, never
    inlined — the BOM proves which prompt was used without leaking it."""
    ts = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    components: list[dict] = []

    for mid in model_ids or []:
        comp: dict = {
            "type": "machine-learning-model", "bom-ref": f"model:{mid}",
            "name": mid, "version": "",
        }
        if model_parameters:
            comp["properties"] = [_prop(f"param:{k}", v)
                                  for k, v in sorted(model_parameters.items())]
        components.append(comp)

    for name, text in sorted((prompts or {}).items()):
        components.append({
            "type": "data", "bom-ref": f"prompt:{name}", "name": f"prompt:{name}",
            "version": "", "hashes": _sha256_component(text),
            "properties": [_prop("kind", "prompt")],
        })

    for t in tools or []:
        components.append({
            "type": "application", "bom-ref": f"tool:{t['name']}",
            "name": t["name"], "version": str(t.get("version", "")),
            "properties": [_prop("kind", "tool"),
                           _prop("source", t.get("source", "native")),
                           _prop("mutating", bool(t.get("mutating", False)))],
        })

    for s in mcp_servers or []:
        components.append({
            "type": "service", "bom-ref": f"mcp:{s['name']}",
            "name": s["name"], "version": str(s.get("version", "")),
            "properties": [_prop("kind", "mcp_server"),
                           _prop("transport", s.get("transport", "stdio"))]
                          + ([_prop("url", s["url"])] if s.get("url") else []),
        })

    if suite:
        components.append({"type": "data", "bom-ref": f"suite:{suite[0]}@{suite[1]}",
                           "name": suite[0], "version": str(suite[1]),
                           "properties": [_prop("kind", "test_suite")]})
    if rubric:
        components.append({"type": "data", "bom-ref": f"rubric:{rubric[0]}@{rubric[1]}",
                           "name": rubric[0], "version": str(rubric[1]),
                           "properties": [_prop("kind", "rubric")]})

    for d in dependencies or []:
        components.append({"type": "library", "bom-ref": f"dep:{d['name']}",
                           "name": d["name"], "version": str(d.get("version", ""))})

    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{serial_uuid or _deterministic_uuid(subject_name, ts)}",
        "version": 1,
        "metadata": {
            "timestamp": ts.isoformat(),
            "tools": [{"name": "agenttic", "version": harness_version}],
            "component": {"type": "application", "name": subject_name,
                          "version": subject_version},
        },
        "components": components,
        "dependencies": [
            {"ref": f"model:{m}", "dependsOn": []} for m in (model_ids or [])
        ],
    }
    return doc


def _deterministic_uuid(name: str, ts: datetime) -> str:
    """A stable urn:uuid derived from the subject + timestamp (no randomness, so
    the ABOM hashes reproducibly)."""
    h = hashlib.sha256(f"{name}|{ts.isoformat()}".encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def validate_abom(doc: dict) -> None:
    """Raise if the document is not a structurally valid CycloneDX BOM."""
    import jsonschema
    jsonschema.validate(doc, CYCLONEDX_SCHEMA)


def abom_sha256(doc: dict) -> str:
    """Canonical hash of the ABOM — this is what the manifest references."""
    return content_hash(doc)


def abom_json(doc: dict) -> str:
    """Canonical serialization for writing to disk (stable bytes)."""
    return canonical_json(doc)
