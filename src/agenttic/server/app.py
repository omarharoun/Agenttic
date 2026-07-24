"""FastAPI application: the HTTP/SSE surface the React Flow canvas talks to.

create_app() wires config → Workspaces in the lifespan. Each **tenant** is an
isolated workspace = its own SQLite database + UIStore + EventBus +
ExecutionManager; the ``default`` tenant maps to the configured ``registry_db``
(so existing single-tenant data is untouched). A request's tenant comes from its
auth principal (see server/auth.py); ``bind_workspace`` resolves it and exposes
the tenant's reg/store/manager/bus on ``request.state`` for the routes.

This file-per-tenant model gives hard isolation with no data migration; for a
Postgres/scale future it would become row-level tenant_id scoping (see
docs/PRODUCTION_READINESS.md §1.3).
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from agenttic.config import load_config
from agenttic.registry.sqlite_store import Registry
from agenttic.server import metrics
from agenttic.server.auth import check_startup, require_auth
from agenttic.server.events import EventBus, make_transport
from agenttic.server.executor import ExecutionManager
from agenttic.server.observability import ObservabilityMiddleware, configure_logging
from agenttic.server.ratelimit import RateLimitMiddleware
from agenttic.server.store import UIStore

# UI_DIST: env override (ASCORE_UI_DIST) for installed/container layouts where
# the package lives in site-packages; falls back to the repo-relative path for
# local/dev runs.
from agenttic._env import get_env as _get_env
UI_DIST = Path(_get_env("ASCORE_UI_DIST")
               or Path(__file__).resolve().parents[3] / "ui" / "dist")

_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_static_path(base: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``base`` and return it only if it stays inside
    ``base`` and is a real file — blocks `../` path traversal / LFI. Returns
    None for traversal attempts or non-files (caller falls back to index)."""
    base = base.resolve()
    try:
        target = (base / rel).resolve()
    except (ValueError, OSError):
        return None
    if target == base or base not in target.parents:
        return None
    return target if target.is_file() else None


def prerendered_page(base: Path, path: str) -> Path | None:
    """Map a request path to its prerendered ``<route>.html`` in the build, if
    one exists. ``""`` -> ``index.html`` (the landing), ``"pricing"`` (or
    ``"pricing/"``) -> ``pricing.html``. Only top-level page files are eligible
    — a nested/dynamic path like ``certified/<id>`` or ``app/build`` has no
    matching file and returns None (caller serves the clean shell). This is what
    keeps client hydration matching the served markup: /pricing gets pricing's
    prerendered HTML, not the landing's."""
    rel = path.strip("/")
    if "/" in rel or rel == ".." or rel.startswith("../"):
        return None  # nested / traversal — never a top-level prerendered page
    name = "index.html" if rel == "" else f"{rel}.html"
    target = safe_static_path(base, name)
    return target if (target is not None and target.name == name) else None


_SHELL_CACHE: dict[str, str] = {}


def _empty_root_div(html: str) -> str:
    """Return ``html`` with the contents of ``<div id="root">…</div>`` removed,
    leaving an empty mount. Uses a balanced ``<div>`` scan so arbitrarily nested
    prerendered markup is stripped correctly (a regex can't match balanced
    tags)."""
    m = re.search(r'<div id="root"[^>]*>', html)
    if not m:
        return html
    start = m.end()
    depth = 1
    for tok in re.finditer(r"<div\b|</div\s*>", html[start:]):
        if tok.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return html[:start] + html[start + tok.start():]
        else:
            depth += 1
    return html  # unbalanced (shouldn't happen) — leave as-is


def clean_shell(base: Path) -> str | None:
    """A bare, NON-prerendered SPA shell derived from ``index.html``: identical
    <head> and built asset/script tags, but with the ``data-server-rendered``
    marker removed and ``#root`` emptied. Because no ``[data-server-rendered=
    true]`` element is present, vite-react-ssg's client entry calls
    ``createRoot()`` (a fresh client render) instead of ``hydrateRoot()`` — so
    there is no server markup to mismatch, and React #418/#423 hydration errors
    can't occur on dynamic routes. Cached per build (keyed by index.html mtime).
    Returns None if the build isn't present."""
    idx = base / "index.html"
    try:
        key = f"{idx.resolve()}::{idx.stat().st_mtime_ns}"
    except OSError:
        return None
    cached = _SHELL_CACHE.get(key)
    if cached is not None:
        return cached
    html = idx.read_text(encoding="utf-8")
    html = html.replace(' data-server-rendered="true"', "")
    html = _empty_root_div(html)
    _SHELL_CACHE[key] = html
    return html


class Workspace:
    """One tenant's isolated stack."""
    def __init__(self, cfg, reg, store, bus, manager, tenant, ab=None,
                 optimizer=None, camp=None, certifier=None, enforcer=None):
        self.cfg, self.reg, self.store = cfg, reg, store
        self.bus, self.manager, self.tenant = bus, manager, tenant
        self.ab = ab
        self.optimizer = optimizer
        self.camp = camp
        self.certifier = certifier
        self.enforcer = enforcer


class Workspaces:
    """Lazily builds and caches a Workspace per tenant.

    Backend is chosen once: if a Postgres URL is configured (``ASCORE_DB`` env
    or ``database.url``), all tenants share one engine and isolate by
    ``tenant_id`` (row-level). Otherwise SQLite is used DB-per-tenant — the
    ``default`` tenant uses ``paths.registry_db`` (or an injected registry, for
    tests) and others get a sibling file ``<db_stem>.<tenant><suffix>``.
    """

    def __init__(self, cfg: dict, default_registry: Registry | None = None,
                 clients: dict | None = None, loop=None):
        self.cfg = cfg
        self.clients = clients or {}
        self._default_registry = default_registry
        self.loop = loop  # captured at startup so per-tenant EventBus works
        self._ws: dict[str, Workspace] = {}             # even off the event loop
        self._db_url = (_get_env("ASCORE_DB")
                        or (cfg.get("database", {}) or {}).get("url") or "")
        self._postgres = bool(self._db_url) and not self._db_url.startswith("sqlite")
        self._shared_engine = None  # one engine shared across tenants (Postgres)
        self._transport = None      # one event transport shared across executions

    @staticmethod
    def normalize(tenant: str | None) -> str:
        return tenant if tenant and _TENANT_RE.match(tenant) else "default"

    @property
    def backend(self) -> str:
        return "postgres" if self._postgres else "sqlite"

    def _db_path(self, tenant: str) -> str:
        base = Path(self.cfg["paths"]["registry_db"])
        if tenant == "default":
            return str(base)
        return str(base.with_name(f"{base.stem}.{tenant}{base.suffix}"))

    def _build(self, tenant: str):
        if self._postgres:
            if self._shared_engine is None:
                from agenttic.registry.sqlite_store import make_engine
                self._shared_engine = make_engine(self._db_url)
            reg = Registry(engine=self._shared_engine, tenant=tenant)
            store = UIStore(self._shared_engine, tenant=tenant)
            return reg, store
        # SQLite: file-per-tenant; tenant_id stays "default" within each file
        if tenant == "default" and self._default_registry is not None:
            reg = self._default_registry
        else:
            reg = Registry(self._db_path(tenant))
        return reg, UIStore(reg.engine)

    def get(self, tenant: str) -> Workspace:
        tenant = self.normalize(tenant)
        if tenant not in self._ws:
            reg, store = self._build(tenant)
            store.interrupt_orphans()
            if self._transport is None:
                self._transport = make_transport(self.cfg, self.loop)
            bus = EventBus(store, loop=self.loop, transport=self._transport)
            manager = ExecutionManager(self.cfg, reg, store, bus,
                                       clients=self.clients)
            from agenttic.server.ab_manager import ABManager
            ab = ABManager(self.cfg, reg, clients=self.clients)
            from agenttic.server.optimizer_manager import OptimizerManager
            optimizer = OptimizerManager(self.cfg, reg, clients=self.clients)
            from agenttic.server.certify_manager import CertifyManager
            certifier = CertifyManager(self.cfg, reg, clients=self.clients)
            from agenttic.enforce.gateway import EnforcementGateway
            enforcer = EnforcementGateway(reg, self.cfg)
            from agenttic.camp.store import CampStore
            camp_tenant = tenant if self._postgres else "default"
            camp = CampStore(reg.engine, tenant=camp_tenant)
            camp.interrupt_orphans()  # sweep runs left 'running' by a dead process
            self._ws[tenant] = Workspace(self.cfg, reg, store, bus, manager,
                                         tenant, ab=ab, optimizer=optimizer,
                                         camp=camp, certifier=certifier,
                                         enforcer=enforcer)
        return self._ws[tenant]


def bind_workspace(request: Request) -> None:
    """Expose the caller's tenant workspace on request.state (runs after
    require_auth, which set request.state.tenant)."""
    tenant = getattr(request.state, "tenant", "default")
    ws = request.app.state.workspaces.get(tenant)
    request.state.cfg = ws.cfg
    request.state.reg = ws.reg
    request.state.store = ws.store
    request.state.manager = ws.manager
    request.state.ab = ws.ab
    request.state.optimizer = ws.optimizer
    request.state.camp = ws.camp
    request.state.certifier = ws.certifier
    request.state.enforcer = ws.enforcer
    request.state.bus = ws.bus
    request.state.clients = request.app.state.clients


def create_app(config_path: str = "config.yaml", *, clients: dict | None = None,
               registry: Registry | None = None) -> FastAPI:
    """``clients`` and ``registry`` are test-injection points (fake LLM/agent
    clients; a tmp-path registry for the default tenant)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio
        cfg = load_config(config_path)
        configure_logging(cfg)
        from agenttic.server.tracing import setup_tracing
        # Air-gap self-check FIRST: if air-gap mode is on and any enabled
        # capability would require egress, refuse to boot naming the offender
        # (Hard Rule 34) — before we wire tracing/exporters that might egress.
        from agenttic.airgap import assert_airgap_safe
        assert_airgap_safe(cfg)
        setup_tracing(cfg)  # OTel exporter when observability.otel_enabled
        check_startup(cfg)  # fail closed if auth.required without a token
        workspaces = Workspaces(cfg, default_registry=registry, clients=clients,
                                loop=asyncio.get_running_loop())
        default = workspaces.get("default")  # eager: interrupt default orphans
        app.state.cfg = cfg
        app.state.workspaces = workspaces
        # back-compat: default-tenant objects exposed directly on app.state
        app.state.reg = default.reg
        app.state.store = default.store
        app.state.bus = default.bus
        app.state.manager = default.manager
        app.state.clients = clients or {}
        # app-level passport signing key (one JWKS for the deployment)
        from agenttic.passport.keys import PassportKeyManager
        app.state.passport_keys = PassportKeyManager(cfg)
        # live service-health checker — probes real components for /api/status
        # and stamps process start time for an honest uptime figure.
        from agenttic.server.health import HealthChecker
        app.state.health = HealthChecker()
        # first-admin bootstrap (env-driven, idempotent)
        admin_email = _get_env("ASCORE_ADMIN_EMAIL")
        from agenttic.secrets import get_secret
        admin_pw = get_secret("ASCORE_ADMIN_PASSWORD")
        if admin_email and admin_pw:
            try:
                from agenttic.server.users import UserStore
                created = UserStore(default.reg.engine).ensure_admin(
                    admin_email, admin_pw)
                logging.getLogger("ascore").info(
                    "admin bootstrap: %s", "created" if created else "exists")
            except Exception as exc:  # noqa: BLE001 — never block startup
                logging.getLogger("ascore").warning("admin bootstrap failed: %s",
                                                     type(exc).__name__)
        # Billing: replace the permissive Copilot credits stub with real
        # free-credit accounting (only if it's still the default stub, so tests
        # that install their own provider aren't clobbered). Restored on shutdown.
        from agenttic.billing import credits_provider as _billing_credits
        app.state.billing_provider_token = _billing_credits.install_if_default(
            workspaces, cfg)
        try:
            yield
        finally:
            _billing_credits.restore(
                getattr(app.state, "billing_provider_token", None))

    app = FastAPI(title="Agenttic", lifespan=lifespan)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(ObservabilityMiddleware)  # outermost: ids, timing, logs

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # consistent envelope; never leak internals to the client. The full
        # error is logged server-side with the request id for correlation.
        from fastapi.responses import JSONResponse
        rid = getattr(request.state, "request_id", None)
        logging.getLogger("agenttic.error").error(
            "unhandled error", extra={"extra_fields": {
                "request_id": rid, "path": request.url.path,
                "error": f"{type(exc).__name__}: {exc}"}})
        return JSONResponse(
            status_code=500,
            content={"error": "internal server error", "request_id": rid})

    @app.get("/health", include_in_schema=False)
    async def health():  # liveness — process is up
        return {"status": "ok"}

    @app.get("/healthz", include_in_schema=False)
    async def healthz():  # lightweight liveness alias (no component probing)
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def ready():  # readiness — default DB reachable
        from fastapi.responses import JSONResponse
        try:
            with app.state.reg.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"status": "not_ready", "detail": str(exc)},
                                status_code=503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint():
        return PlainTextResponse(metrics.render())

    from agenttic.server.routes.ab import router as ab_router
    from agenttic.server.routes.assistant import router as assistant_router
    from agenttic.server.routes.auth import router as auth_router
    from agenttic.server.routes.camp import router as camp_router
    from agenttic.server.routes.certifications import (
        public_router as certifications_public_router,
    )
    from agenttic.server.routes.certifications import router as certifications_router
    from agenttic.server.routes.connect import router as connect_router
    from agenttic.server.routes.copilot import router as copilot_router
    from agenttic.server.routes.cost import router as cost_router
    from agenttic.server.routes.executions import router as executions_router
    from agenttic.server.routes.hardening import router as hardening_router
    from agenttic.server.routes.ingest import router as ingest_router
    from agenttic.server.routes.leaderboard import router as leaderboard_router
    from agenttic.server.routes.live import router as live_router
    from agenttic.server.routes.optimize import router as optimize_router
    from agenttic.server.routes.quickstart import router as quickstart_router
    from agenttic.server.routes.resources import router as resources_router
    from agenttic.server.routes.scan import router as scan_router
    from agenttic.server.routes.settings import router as settings_router
    from agenttic.server.routes.standard import router as standard_router
    from agenttic.server.routes.workflows import router as workflows_router

    # every /api route authenticates (sets role + tenant) then binds the
    # tenant's workspace onto request.state — incl. SSE and the approval gate.
    # auth endpoints are PUBLIC (they ARE the authentication); rate-limited by
    # the middleware + per-email lockout.
    app.include_router(auth_router, prefix="/api")

    # Public, UNAUTHENTICATED certificate verification (powers the public
    # "Tested with Agenttic" page + embeddable badges). Mounted before the
    # auth-protected routers; looked up by cert id regardless of tenant.
    app.include_router(certifications_public_router, prefix="/api")

    # Public, UNAUTHENTICATED service-status rollup (powers the /status page).
    # Aggregate-only; no auth so uptime is visible even during an incident.
    from agenttic.server.routes.status import public_router as status_public_router
    app.include_router(status_public_router, prefix="/api")
    from agenttic.server.routes.capabilities import router as capabilities_router
    app.include_router(capabilities_router, prefix="/api")

    # Public billing surfaces (UNAUTHENTICATED): the pricing catalog for the
    # landing/pricing page, and the Stripe + PayPal webhooks (signature-verified,
    # idempotent). Mounted before the auth-protected routers.
    from agenttic.server.routes.billing import public_router as billing_public_router
    from agenttic.server.routes.billing import webhook_router as billing_webhook_router
    app.include_router(billing_public_router, prefix="/api")
    app.include_router(billing_webhook_router, prefix="/api")

    @app.get("/.well-known/agenttic-cert-keys.json", include_in_schema=False)
    def well_known_cert_keys(request: Request):
        """Stable, well-known location for the Ed25519 public keys that sign
        Agenttic safety certificates — the trust anchor for third-party,
        issuer-independent certificate verification (see docs/CERTIFICATION.md)."""
        from fastapi.responses import JSONResponse

        from agenttic import certification as _cert
        return JSONResponse(
            {"alg": _cert.SIGNATURE_ALG,
             "keys": _cert.published_public_keys(request.app.state.cfg)},
            headers={"Cache-Control": "public, max-age=300"})

    protected = [Depends(require_auth), Depends(bind_workspace)]
    app.include_router(workflows_router, prefix="/api", dependencies=protected)
    app.include_router(executions_router, prefix="/api", dependencies=protected)
    app.include_router(resources_router, prefix="/api", dependencies=protected)
    app.include_router(live_router, prefix="/api", dependencies=protected)
    app.include_router(leaderboard_router, prefix="/api", dependencies=protected)
    app.include_router(cost_router, prefix="/api", dependencies=protected)
    app.include_router(settings_router, prefix="/api", dependencies=protected)
    app.include_router(ab_router, prefix="/api", dependencies=protected)
    app.include_router(standard_router, prefix="/api", dependencies=protected)
    app.include_router(hardening_router, prefix="/api", dependencies=protected)
    app.include_router(optimize_router, prefix="/api", dependencies=protected)
    app.include_router(quickstart_router, prefix="/api", dependencies=protected)
    app.include_router(scan_router, prefix="/api", dependencies=protected)
    app.include_router(connect_router, prefix="/api", dependencies=protected)
    app.include_router(assistant_router, prefix="/api", dependencies=protected)
    app.include_router(copilot_router, prefix="/api", dependencies=protected)
    from agenttic.server.routes.billing import router as billing_router
    app.include_router(billing_router, prefix="/api", dependencies=protected)
    app.include_router(certifications_router, prefix="/api", dependencies=protected)
    from agenttic.server.routes.dossiers import router as dossiers_router
    app.include_router(dossiers_router, prefix="/api", dependencies=protected)
    # Public, unauthenticated dossier verification at the root path (registered
    # before the SPA catch-all so it isn't shadowed).
    from agenttic.server.routes.dossiers import public_router as dossiers_public
    app.include_router(dossiers_public)
    from agenttic.server.routes.enforce import router as enforce_router
    app.include_router(enforce_router, prefix="/api", dependencies=protected)
    # OTLP/HTTP receiver — standard /v1/traces path (no /api prefix), auth+tenant
    # scoped like every other protected route.
    app.include_router(ingest_router, dependencies=protected)
    # public JWKS + passport status (unauthenticated); issuer routes are protected
    from agenttic.server.routes.passport import public_router as passport_public
    from agenttic.server.routes.passport import router as passport_router
    app.include_router(passport_public)
    app.include_router(passport_router, prefix="/api", dependencies=protected)
    from agenttic.server.routes.feeds import router as feeds_router
    app.include_router(feeds_router, prefix="/api", dependencies=protected)
    app.include_router(camp_router, prefix="/api", dependencies=protected)

    if UI_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"),
                  name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):  # SPA fallback for any non-API route
            # An unmatched API request must NOT fall through to the HTML SPA
            # shell. The frontend calls res.json() on these paths; a 200
            # text/html body (index.html) blows up as "JSON.parse: unexpected
            # character at line 1 column 1" and crashes the app. Keep the API
            # surface always-JSON: a real 404 with a JSON body.
            if path == "api" or path.startswith("api/") \
                    or path == "v1" or path.startswith("v1/"):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            # A real built file (favicon.ico, robots.txt, fonts/…) — serve it.
            target = safe_static_path(UI_DIST, path) if path else None
            if target is not None:
                return FileResponse(target)
            # A prerendered route (/, /pricing, /methodology, …) — serve ITS own
            # prerendered HTML so client hydration matches the served markup.
            page = prerendered_page(UI_DIST, path)
            if page is not None:
                return FileResponse(page)
            # A dynamic / non-prerendered route (/scan, /certified/<id>, /app/*,
            # /login, …) — serve the bare shell (empty #root, no SSR marker) so
            # React client-renders fresh with ZERO hydration mismatch. Serving
            # the prerendered LANDING here is exactly what caused React #418/#423.
            shell = clean_shell(UI_DIST)
            if shell is not None:
                return HTMLResponse(shell)
            return FileResponse(UI_DIST / "index.html")

    return app
