"""TerraformAgent FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import account, architecture, execution, projects
from app.config import BASE_DIR, get_settings
from app.core.errors import AppError
from app.core.security import hash_password
from app.db import SessionLocal, init_db
from app.models import Membership, Organization, User
from app.services.events import bus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("terraformagent")
settings = get_settings()


def bootstrap_admin() -> None:
    """Create the initial local administrator when the database is empty."""
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        user = User(
            name="Administrator",
            email=settings.bootstrap_email.lower(),
            password_hash=hash_password(settings.bootstrap_password),
            authentication_provider="local",
        )
        db.add(user)
        db.flush()
        organization = Organization(name=settings.bootstrap_org, slug="terraformagent")
        db.add(organization)
        db.flush()
        db.add(Membership(organization_id=organization.id, user_id=user.id, role="owner"))
        db.commit()
        logger.info("Bootstrap administrator created: %s", user.email)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bus.bind_loop(asyncio.get_running_loop())
    bootstrap_admin()
    from app.services.llm_config import load_runtime_configuration

    load_runtime_configuration()
    logger.info("TerraformAgent started (environment=%s)", settings.environment)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="TerraformAgent",
        description="AI-powered Azure infrastructure agent with approval-gated Terraform execution.",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code, **exc.extra},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception):
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "internal_error"},
        )

    app.include_router(account.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(architecture.router, prefix="/api/v1")
    app.include_router(execution.router, prefix="/api/v1")

    frontend_dist = BASE_DIR.parent / "frontend" / "dist"
    if frontend_dist.exists():
        assets = frontend_dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            candidate = (frontend_dist / full_path).resolve()
            if full_path and str(candidate).startswith(str(frontend_dist.resolve())) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_dist / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        async def root():
            return {
                "name": "TerraformAgent API",
                "docs": "/api/docs",
                "note": "Frontend build not found. Run `npm run build` in frontend/.",
            }

    return app


app = create_app()
