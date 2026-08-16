"""Receipt-gating reverse proxy (verification spec §5, sidecar variant).

The decorator in :mod:`agenttic.gate.middleware` needs the tool's source. This
does not: the tool runs unmodified behind the sidecar, and every request reaches
it only after the *same* :func:`verify_tool_receipt` pipeline has passed. Not one
of the seven steps is reimplemented here — this module is transport plumbing
around that one call.

The one thing a proxy has that the decorator does not is a route table, and the
one thing it lacks is the endpoint's own arguments. So bound values come from the
matched path template instead of ``sig.bind_partial``. Two consequences worth
stating out loud:

* **A path-sourced bound value is always a ``str``.** ``compute_bound_params``
  hashes through ``canonical_json``, so ``"1"`` and ``1`` are different receipts.
  The gateway-side ``args`` for a proxied tool must carry strings.
* **The forwarded path is rebuilt from the template**, never passed through, so
  no traversal or ``//host`` in the original path reaches upstream.

Fail-closed means allowlist, not filter: there is no default route and no
pass-through branch. An unmatched path is refused, because an unlisted path is
an ungoverned path — a proxy that forwards what it does not understand is not a
gate.

The nonce is claimed *before* the upstream hop (it is step 6, inside verify).
A 502 therefore burns the capability, and there is deliberately no un-claim: a
refund is a replay window with paperwork. The retry path is a fresh receipt from
the gateway, which re-evaluates policy — a governed event rather than a replay.

Residue this cannot fix: on a timeout the proxy cannot distinguish "upstream
never saw it" from "upstream did it and the response was lost". That is
at-most-once, and only an idempotency key understood by the tool closes it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Callable
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, compile_path

from agenttic.gate.middleware import (
    _REASONS,
    DEFAULT_NONCE_STORE,
    DEFAULT_REVOCATIONS,
    DEFAULT_SKEW_SECONDS,
    HEADER_NAME,
    NonceStore,
    RevocationCache,
    decode_receipt_header,
    verify_tool_receipt,
)
from agenttic.passport.receipts import tool_access_entry
from agenttic.verifier.sdk import VerifyError

# Never relayed in either direction. ``host``/``content-length`` are dropped
# because the hop rewrites both; the receipt header is dropped because handing a
# capability token to the tool would let the tool replay it.
#
# The method-override family is dropped for the same reason the receipt is: the
# sidecar picks its route, its action class and therefore its whole receipt
# requirement from ``request.method``. Django, Rails, Symfony, Spring and most
# API gateways honour these headers by default, so relaying one lets a *read*
# receipt drive a DELETE. A gate that deliberately does not modify the tool does
# not get to assume the tool ignores them.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "host", "content-length", HEADER_NAME.lower(),
    "x-http-method-override", "x-method-override", "x-http-method",
})

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# ponytail: buffered, capped. Add streaming when a proxied tool actually
# returns or accepts something large.
MAX_BODY_BYTES = 1 << 20

DEFAULT_TIMEOUT = (3, 30)


class _Refuse(Exception):
    """Refused before the upstream socket was ever opened."""


def compile_routes(cfg: dict) -> list[tuple[str, re.Pattern, str, str]]:
    """``(method, regex, path_format, tool)`` per catalogued tool declaring a
    ``route``. An entry without one is simply not proxied — additive, so the
    catalog stays the single source of truth the minter already hashes.
    """
    tools = ((cfg or {}).get("enforcement", {})
             .get("tool_access", {}).get("tools") or {})
    out = []
    for tool, entry in (tools.items() if isinstance(tools, dict) else []):
        route = (entry or {}).get("route") if isinstance(entry, dict) else None
        if not route:
            continue
        method, _, path = str(route).partition(" ")
        if not path.startswith("/"):
            raise ValueError(f"route for {tool!r} must be 'METHOD /path': {route!r}")
        regex, path_format, _ = compile_path(path)
        out.append((method.upper(), regex, path_format, tool))
    return out


def _requests_forward(method: str, url: str, body: bytes,
                      headers: dict[str, str]) -> tuple[int, bytes, dict]:
    # allow_redirects=False is load-bearing: a 302 from upstream must not be
    # chased by the proxy, which would put an unverified URL on the wire.
    import requests
    r = requests.request(method, url, data=body or None, headers=headers,
                         timeout=DEFAULT_TIMEOUT, allow_redirects=False)
    return r.status_code, r.content, dict(r.headers)


def build_sidecar(
    cfg: dict,
    *,
    jwks: dict | Callable[[], dict] | None = None,
    upstream: str | None = None,
    forward: Callable[..., tuple[int, bytes, dict]] = _requests_forward,
    nonce_store: NonceStore | None = None,
    revocations: RevocationCache | None = None,
    now: Callable[[], datetime] | datetime | None = None,
    skew_seconds: float = DEFAULT_SKEW_SECONDS,
) -> Starlette:
    """A reverse proxy that refuses anything not carrying a valid, current,
    action-matched, single-use Tool Access Receipt.

    ``upstream`` defaults to ``enforcement.tool_access.upstream``. ``forward`` is
    the injectable hop — a test points it at the upstream app's own test client,
    so no socket is involved.
    """
    routes = compile_routes(cfg)
    base = (upstream or (cfg or {}).get("enforcement", {})
            .get("tool_access", {}).get("upstream") or "").rstrip("/")
    if routes and not base:
        # Loudly at startup, not as a puzzling 502 per request. Nothing is
        # forwarded either way, so this is clarity, not safety.
        raise ValueError("enforcement.tool_access.upstream is required to proxy")

    async def handler(request: Request) -> Response:
        try:
            header = request.headers.get(HEADER_NAME)
            if not header:
                raise _Refuse("missing tool access receipt")

            matches = [(pf, tool, m) for method, rx, pf, tool in routes
                       if method == request.method
                       for m in [rx.match(request.url.path)] if m]
            if len(matches) != 1:
                # 0 = unlisted path, ungoverned path. >1 = a config ambiguity to
                # fix, not an ordering to guess at. Both refuse.
                raise _Refuse("no tool access receipt covers this request")
            path_format, tool, match = matches[0]

            entry = tool_access_entry(cfg, tool)
            if entry is None:
                # Deliberate inversion of the decorator's contract: there
                # "not gated" is the ordinary ungated case, here it means the
                # entry is absent or malformed, so refuse.
                raise _Refuse("no tool access receipt covers this request")

            action_class = entry.get("action_class", "read")
            if action_class == "irreversible" and request.url.query:
                # Nothing in the receipt covers the query string, so ?cascade=1
                # would ride in unbound. Refuse rather than silently strip.
                raise _Refuse("tool access receipt does not authorise this call")

            body = b""
            async for chunk in request.stream():
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    raise _Refuse("request body too large")

            if action_class == "irreversible" and body:
                # Same reason the query string is refused: nothing in the
                # receipt covers the body either, so ``{"cascade": true}`` would
                # ride in unbound. Only the bound params may appear — their
                # VALUES are still taken from the path below, so a conflicting
                # one changes nothing. Stripping instead of refusing would be
                # worse than the hole: dropping ``{"dry_run": true}`` turns a
                # rehearsal into a deletion.
                try:
                    payload = json.loads(body)
                except Exception:
                    raise _Refuse("tool access receipt does not authorise this call")
                if (not isinstance(payload, dict)
                        or not set(payload) <= set(entry.get("bound_params") or ())):
                    raise _Refuse("tool access receipt does not authorise this call")

            params = match.groupdict()
            values = None
            if entry.get("bound_params"):
                values = {}
                for name in entry["bound_params"]:
                    if name in params:
                        values[name] = params[name]   # the path wins: it is what
                        continue                      # upstream will act on
                    try:
                        values[name] = json.loads(body)[name]
                    except Exception:
                        raise _Refuse(
                            "tool access receipt does not authorise this call")

            verify_tool_receipt(
                decode_receipt_header(header),
                (jwks() if callable(jwks) else jwks) or {"keys": []},
                tool=tool, action_class=action_class,
                params_schema=entry.get("input_schema"),
                nonce_store=nonce_store or DEFAULT_NONCE_STORE,
                revocations=revocations or DEFAULT_REVOCATIONS,
                bound_values=values, now=now, skew_seconds=skew_seconds)
        except VerifyError as exc:
            return _refused(_REASONS.get(type(exc), "tool access receipt rejected"))
        except _Refuse as exc:
            return _refused(str(exc))
        except Exception:  # a surprise is a refusal, never a pass-through
            return _refused("tool access receipt rejected")

        # Verified. Rebuilt from the template, so only matched params reach the
        # upstream URL. The query string is relayed only for non-irreversible
        # classes; irreversible already refused above.
        # Re-encoded, exactly once, because the value was decoded exactly once
        # on the way in. Interpolating it raw makes the next hop decode a second
        # time, so a client sending ``c%252D1`` gets a receipt bound to ``c%2D1``
        # and a tool acting on ``c-1`` — verify one instance, act on another.
        # ``safe=""`` also keeps a value from injecting a path separator.
        url = base + path_format.format(
            **{k: quote(v, safe="") for k, v in params.items()})
        if request.url.query:
            url += "?" + request.url.query
        fwd = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP}
        try:
            status, content, headers = await run_in_threadpool(
                forward, request.method, url, body, fwd)
        except Exception:
            # No retry: the proxy cannot know whether upstream applied an
            # irreversible action. The nonce stays burned.
            return JSONResponse({"detail": "upstream unavailable"}, 502)
        return Response(content, status_code=status,
                        headers={k: v for k, v in headers.items()
                                 if k.lower() not in _HOP_BY_HOP})

    return Starlette(routes=[Route("/{path:path}", handler, methods=_METHODS)])


def _refused(detail: str) -> Response:
    return JSONResponse({"detail": detail}, 403)
