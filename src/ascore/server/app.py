"""FastAPI application: the HTTP/SSE surface the React Flow canvas talks to.

create_app() wires config → Registry → UIStore → EventBus → ExecutionManager
in the lifespan (single process, single SQLite file). In production the
built frontend (ui/dist) is served from the same app; in development the
Vite dev server proxies /api here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ascore.config import load_config
from ascore.registry.sqlite_store import Registry
from ascore.server.auth import check_startup, require_auth
from ascore.server.events import EventBus
from ascore.server.executor import ExecutionManager
from ascore.server.store import UIStore

UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


def create_app(config_path: str = "config.yaml", *, clients: dict | None = None,
               registry: Registry | None = None) -> FastAPI:
    """``clients`` and ``registry`` are test-injection points (fake LLM/agent
    clients; a tmp-path registry)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = load_config(config_path)
        check_startup(cfg)  # fail closed if auth.required without a token
        reg = registry or Registry(cfg["paths"]["registry_db"])
        store = UIStore(reg.engine)
        interrupted = store.interrupt_orphans()
        bus = EventBus(store)
        manager = ExecutionManager(cfg, reg, store, bus, clients=clients)
        app.state.cfg = cfg
        app.state.reg = reg
        app.state.store = store
        app.state.bus = bus
        app.state.manager = manager
        app.state.clients = clients or {}
        app.state.interrupted_on_boot = interrupted
        yield

    app = FastAPI(title="Agenttic", lifespan=lifespan)

    from ascore.server.routes.executions import router as executions_router
    from ascore.server.routes.leaderboard import router as leaderboard_router
    from ascore.server.routes.live import router as live_router
    from ascore.server.routes.resources import router as resources_router
    from ascore.server.routes.workflows import router as workflows_router

    # every /api route — incl. the SSE stream and the approval gate — requires
    # the API token once one is configured (no-op when auth is disabled).
    protected = [Depends(require_auth)]
    app.include_router(workflows_router, prefix="/api", dependencies=protected)
    app.include_router(executions_router, prefix="/api", dependencies=protected)
    app.include_router(resources_router, prefix="/api", dependencies=protected)
    app.include_router(live_router, prefix="/api", dependencies=protected)
    app.include_router(leaderboard_router, prefix="/api", dependencies=protected)

    if UI_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"),
                  name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):  # SPA fallback: any non-API route -> index
            candidate = UI_DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(UI_DIST / "index.html")

    return app
