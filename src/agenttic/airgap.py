"""Air-gapped mode: no-egress startup self-check (SPEC-7 Step 38, T38.3).

Air-gap mode is a promise: the scanner, certification engine, and OTel ingest run
with **zero outbound network**. This module is the enforcement of that promise at
startup — it audits the running configuration for any capability whose code path
would require egress and, if air-gap mode is on, **refuses to boot naming the
offender** (Hard Rule 34). Features that inherently need the internet (hosted
public verify pages, upstream Index browse) are flagged *unavailable* rather than
silently degraded.

The capability table is declarative and append-only: adding a new egress-capable
feature means adding one row here, so the self-check can never fall behind the
code (config over code).
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse


class AirgapEgressError(RuntimeError):
    """Raised at startup when air-gap mode is on but an enabled capability would
    require outbound network. The message names every offender."""


def is_airgap(cfg: dict, env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    from agenttic._env import get_env
    if str(get_env("ASCORE_AIRGAP", "", environ=env)).lower() in ("1", "true", "yes"):
        return True
    return bool((cfg.get("airgap", {}) or {}).get("enabled", False))


def _host_is_local(host: str) -> bool:
    """True for hosts reachable without leaving the VPC/cluster: loopback,
    RFC1918/private ranges, and cluster-internal suffixes. A public routable
    host is 'external' — that's what air-gap forbids."""
    if not host:
        return True
    host = host.split(":")[0].strip().lower()
    if host in ("localhost",) or host.endswith((".local", ".internal", ".svc",
                                                ".cluster.local")):
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _url_is_external(url: str) -> bool:
    if not url:
        return False
    try:
        return not _host_is_local(urlparse(url).hostname or "")
    except Exception:
        return True


# --- capability predicates -------------------------------------------------

def _remote_llm(cfg: dict, env: dict) -> bool:
    ag = cfg.get("airgap", {}) or {}
    # An offline/local LLM (or the deterministic mock) clears this. Otherwise the
    # default provider is the Anthropic API (public) — an egress path.
    if ag.get("mock_llm") or ag.get("local_llm_base_url"):
        return False
    from agenttic._env import get_env
    base = get_env("ASCORE_LLM_BASE_URL", environ=env) or (cfg.get("anthropic", {}) or {}).get("base_url")
    if base and _host_is_local(urlparse(base).hostname or ""):
        return False
    return True


def _otel_remote(cfg: dict, env: dict) -> bool:
    obs = cfg.get("observability", {}) or {}
    if not obs.get("otel_enabled"):
        return False
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or obs.get("otel_endpoint", "")
    # otel_enabled with no endpoint defaults to the public collector convention.
    return _url_is_external(endpoint) if endpoint else True


def _external_webhooks(cfg: dict, env: dict) -> bool:
    urls = (cfg.get("feeds", {}) or {}).get("webhook_urls", []) or []
    return any(_url_is_external(u) for u in urls)


def _smtp_email(cfg: dict, env: dict) -> bool:
    email = cfg.get("email", {}) or {}
    if not email.get("enabled"):
        return False
    host = (email.get("smtp", {}) or {}).get("host", "")
    return bool(host) and not _host_is_local(host)


def _upstream_index(cfg: dict, env: dict) -> bool:
    url = (cfg.get("interop", {}) or {}).get("index_url", "")
    return _url_is_external(url)


@dataclass(frozen=True)
class Capability:
    name: str
    kind: str  # "blocking" (refuses boot) | "egress_only" (flagged unavailable)
    detail: str
    enabled: Callable[[dict, dict], bool]


# Append-only registry. New egress-capable features add a row here.
CAPABILITIES: list[Capability] = [
    Capability("remote_llm", "blocking",
               "LLM calls default to the Anthropic API (public). Set "
               "airgap.local_llm_base_url to a private inference endpoint, or "
               "airgap.mock_llm: true for the offline deterministic provider.",
               _remote_llm),
    Capability("otel_remote_export", "blocking",
               "observability.otel_enabled exports spans to an external OTLP "
               "endpoint. Point it at an in-cluster collector or disable it.",
               _otel_remote),
    Capability("risk_webhooks", "blocking",
               "feeds.webhook_urls contains a public URL; delivery would egress. "
               "Use intra-VPC webhook targets only.",
               _external_webhooks),
    Capability("smtp_email", "blocking",
               "email.enabled with an external SMTP relay host would egress. "
               "Use an in-cluster relay or disable email.",
               _smtp_email),
    Capability("upstream_index_import", "blocking",
               "interop.index_url points at a public Index; import would egress.",
               _upstream_index),
    # Egress-only features — flagged unavailable offline, never boot-blocking.
    Capability("hosted_public_verify", "egress_only",
               "Hosted public verify pages require the internet; offline, verify "
               "runs locally via the CLI/SDK and JWKS instead.",
               lambda cfg, env: True),
    Capability("upstream_index_browse", "egress_only",
               "Browsing the public Agenttic Index requires the internet; the "
               "local registry is authoritative offline.",
               lambda cfg, env: True),
]


def egress_self_check(cfg: dict, env: dict | None = None) -> dict:
    """Audit the config. Returns {enabled, offenders, unavailable, allow}.

    ``offenders`` are enabled blocking capabilities not on the allow-list (each
    with name+detail). ``unavailable`` are egress-only features that are flagged
    off. ``allow`` echoes the explicit escape-hatch list."""
    env = os.environ if env is None else env
    enabled = is_airgap(cfg, env)
    allow = list((cfg.get("airgap", {}) or {}).get("allow", []) or [])
    offenders, unavailable = [], []
    for cap in CAPABILITIES:
        try:
            active = cap.enabled(cfg, env)
        except Exception:
            active = False
        if not active:
            continue
        if cap.kind == "egress_only":
            unavailable.append({"name": cap.name, "detail": cap.detail})
        elif cap.name not in allow:
            offenders.append({"name": cap.name, "detail": cap.detail})
    return {"enabled": enabled, "offenders": offenders,
            "unavailable": unavailable, "allow": allow}


def assert_airgap_safe(cfg: dict, env: dict | None = None) -> dict:
    """Refuse to boot if air-gap mode is on and any blocking capability is
    enabled. Returns the self-check report when safe (or when not in air-gap)."""
    report = egress_self_check(cfg, env)
    if report["enabled"] and report["offenders"]:
        names = ", ".join(o["name"] for o in report["offenders"])
        details = "\n".join(f"  - {o['name']}: {o['detail']}"
                            for o in report["offenders"])
        raise AirgapEgressError(
            f"air-gap mode is ON but {len(report['offenders'])} capability(ies) "
            f"would require outbound network: {names}\n{details}\n"
            "Disable them, point them at intra-VPC hosts, or add the name to "
            "airgap.allow (explicit, logged escape hatch) — refusing to boot.")
    return report
