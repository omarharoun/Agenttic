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

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from ascore.config import load_config
from ascore.registry.sqlite_store import Registry
from ascore.server import metrics
from ascore.server.auth import check_startup, require_auth
from ascore.server.events import EventBus, make_transport
from ascore.server.executor import ExecutionManager
from ascore.server.observability import ObservabilityMiddleware, configure_logging
from ascore.server.ratelimit import RateLimitMiddleware
from ascore.server.store import UIStore

UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"

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


class Workspace:
    """One tenant's isolated stack."""
    def __init__(self, cfg, reg, store, bus, manager, tenant):
        self.cfg, self.reg, self.store = cfg, reg, store
        self.bus, self.manager, self.tenant = bus, manager, tenant


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
        self._db_url = (os.environ.get("ASCORE_DB")
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
                from ascore.registry.sqlite_store import make_engine
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
            self._ws[tenant] = Workspace(self.cfg, reg, store, bus, manager, tenant)
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
        yield

    app = FastAPI(title="Agenttic", lifespan=lifespan)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(ObservabilityMiddleware)  # outermost: ids, timing, logs

    @app.get("/health", include_in_schema=False)
    async def health():  # liveness — process is up
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

    from ascore.server.routes.cost import router as cost_router
    from ascore.server.routes.executions import router as executions_router
    from ascore.server.routes.leaderboard import router as leaderboard_router
    from ascore.server.routes.live import router as live_router
    from ascore.server.routes.resources import router as resources_router
    from ascore.server.routes.workflows import router as workflows_router

    # every /api route authenticates (sets role + tenant) then binds the
    # tenant's workspace onto request.state — incl. SSE and the approval gate.
    protected = [Depends(require_auth), Depends(bind_workspace)]
    app.include_router(workflows_router, prefix="/api", dependencies=protected)
    app.include_router(executions_router, prefix="/api", dependencies=protected)
    app.include_router(resources_router, prefix="/api", dependencies=protected)
    app.include_router(live_router, prefix="/api", dependencies=protected)
    app.include_router(leaderboard_router, prefix="/api", dependencies=protected)
    app.include_router(cost_router, prefix="/api", dependencies=protected)

    if UI_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"),
                  name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):  # SPA fallback: any non-API route -> index
            target = safe_static_path(UI_DIST, path) if path else None
            if target is not None:
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    return app
