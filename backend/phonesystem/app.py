"""FastAPI application: the admin API plus the built web UI."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, calls, extensions, phones, system
from .api.deps import AppState
from .config import AppConfig
from .db import Database
from .security import CSRF_HEADER, CSRF_VALUE, LoginThrottle
from .services.calls import CdrImporter
from .services.confgen import ConfigManager
from .services.firmware import FirmwareStore

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(
    cfg: AppConfig,
    db: Database | None = None,
    config_manager: ConfigManager | None = None,
) -> FastAPI:
    db = db or Database(cfg.db_url)
    firmware = FirmwareStore(cfg.firmware_dir)
    firmware.ensure()
    app = FastAPI(title="PhoneSystem", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.phonesystem = AppState(
        cfg=cfg,
        db=db,
        config_manager=config_manager or ConfigManager(cfg),
        firmware=firmware,
        login_throttle=LoginThrottle(),
        cdr_importer=CdrImporter(db, cfg.cdr_file),
    )

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # Browsers won't send a custom header cross-site without a CORS
        # preflight, which we never allow. Together with SameSite=Strict
        # cookies this blocks cross-site request forgery.
        if (
            request.method in UNSAFE_METHODS
            and request.url.path.startswith("/api/")
            and request.headers.get(CSRF_HEADER) != CSRF_VALUE
        ):
            return JSONResponse({"detail": f"missing {CSRF_HEADER} header"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    for module in (auth, extensions, phones, calls, system):
        app.include_router(module.router)

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict:
        return {"ok": True}

    if cfg.frontend_dist and (cfg.frontend_dist / "index.html").is_file():
        _mount_frontend(app, cfg.frontend_dist)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)
