"""SSRF protection for black-box agent URLs.

A black-box agent is just an operator-supplied URL the platform POSTs to
(`adapters/blackbox_http.py`). Without validation that is a server-side request
forgery primitive: `http://169.254.169.254/...` (cloud metadata → credential
theft), `http://localhost:...` (internal services), `file://...`, etc.

`validate_blackbox_url` enforces, config-driven (`security.*`):
* an allowed scheme (default http/https only — blocks file/gopher/...),
* an optional host allowlist,
* and — unless disabled — rejection of any host that is, or resolves to, a
  private / loopback / link-local / reserved / multicast / metadata address.

It is called at **registration** (catalog POST + CLI, `allow_unresolved=True` so
a not-yet-deployed endpoint can be registered) and at **request time** (the real
HTTP transport, `allow_unresolved=False` — it must resolve to a public address
to be dialed). The transport also disables redirects so a 3xx can't bounce a
validated URL onto an internal target.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

DEFAULT_SCHEMES = ("http", "https")


class UnsafeURLError(ValueError):
    """A URL failed SSRF validation (bad scheme / private target / not allowed)."""


def _sec(cfg: dict | None) -> dict:
    return (cfg or {}).get("security", {}) or {}


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _host_allowed(host: str, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    h = host.lower()
    return any(h == a.lower() or h.endswith("." + a.lower().lstrip("."))
               for a in allowlist)


def validate_blackbox_url(url: str, *, cfg: dict | None = None,
                          resolve: bool = True,
                          allow_unresolved: bool = True) -> str:
    """Return the url if safe; raise UnsafeURLError otherwise."""
    sec = _sec(cfg)
    schemes = tuple(sec.get("blackbox_allowed_schemes", DEFAULT_SCHEMES))
    block_private = sec.get("blackbox_block_private", True)
    allowlist = sec.get("blackbox_url_allowlist", []) or []

    parsed = urlparse(url)
    if parsed.scheme not in schemes:
        raise UnsafeURLError(
            f"scheme {parsed.scheme or '(none)'!r} not allowed; "
            f"permitted schemes: {list(schemes)}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"url {url!r} has no host")
    if not _host_allowed(host, allowlist):
        raise UnsafeURLError(
            f"host {host!r} is not in security.blackbox_url_allowlist")
    if not block_private:
        return url

    # literal IP — check directly, no DNS
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_blocked_ip(ip):
            raise UnsafeURLError(f"host {host} is a private/reserved address")
        return url

    # hostname — resolve and check every address it maps to
    if not resolve:
        return url
    try:
        infos = socket.getaddrinfo(host, parsed.port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        if allow_unresolved:
            return url  # registration of a not-yet-live endpoint is allowed
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}")
    for info in infos:
        addr = info[4][0]
        if _is_blocked_ip(ipaddress.ip_address(addr)):
            raise UnsafeURLError(
                f"host {host!r} resolves to blocked address {addr}")
    return url


def pin_blackbox_target(url: str, *, cfg: dict | None = None
                        ) -> tuple[str, int, str]:
    """Request-time SSRF check that RESOLVES ONCE and PINS the result.

    Returns ``(host, port, pinned_ip)``: validate scheme + allowlist, resolve the
    host a SINGLE time, verify EVERY resolved address is public, and return one
    validated IP for the caller to dial DIRECTLY — eliminating the second,
    unvalidated DNS lookup a rebinding attacker relies on (the classic gap where
    ``validate_blackbox_url`` resolves address A, then the HTTP client re-resolves
    to a private address B). The caller preserves the original ``host`` as the
    Host header / TLS SNI so certificate validation and virtual-hosting still work.
    Raises :class:`UnsafeURLError` on any failure (same messages as
    ``validate_blackbox_url`` so callers/telemetry stay consistent)."""
    sec = _sec(cfg)
    schemes = tuple(sec.get("blackbox_allowed_schemes", DEFAULT_SCHEMES))
    block_private = sec.get("blackbox_block_private", True)
    allowlist = sec.get("blackbox_url_allowlist", []) or []

    parsed = urlparse(url)
    if parsed.scheme not in schemes:
        raise UnsafeURLError(
            f"scheme {parsed.scheme or '(none)'!r} not allowed; "
            f"permitted schemes: {list(schemes)}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"url {url!r} has no host")
    if not _host_allowed(host, allowlist):
        raise UnsafeURLError(
            f"host {host!r} is not in security.blackbox_url_allowlist")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # literal IP — validate directly, no DNS, and pin it as-is
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if block_private and _is_blocked_ip(ip):
            raise UnsafeURLError(f"host {host} is a private/reserved address")
        return (host, port, str(ip))

    # hostname — resolve EXACTLY ONCE, validate every address, then pin one
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}")
    addrs = [info[4][0] for info in infos if info[4] and info[4][0]]
    if not addrs:
        raise UnsafeURLError(f"cannot resolve host {host!r}: no addresses")
    if block_private:
        for addr in addrs:
            if _is_blocked_ip(ipaddress.ip_address(addr)):
                raise UnsafeURLError(
                    f"host {host!r} resolves to blocked address {addr}")
    # every resolved address is validated public; dial exactly this one
    return (host, port, addrs[0])
