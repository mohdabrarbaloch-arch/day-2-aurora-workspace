"""Aurora application entrypoint.

Routing note (deliberate): FastAPI serves the static frontend itself and only
`/` returns HTML. There is NO catch-all rewrite to index.html. That means a
failed import produces a visible 500 instead of a friendly 200 shell hiding a
dead backend — the exact failure this project was built to avoid.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import auth, chat, workspaces
from app.config import settings
from app.db import init_db
from app.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.schemas import HealthOut

logging.basicConfig(
    level=logging.INFO if not settings.is_production else logging.WARNING,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("aurora")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Aurora",
    version=__version__,
    description=(
        "A multimodal workspace: upload images and documents, then ask questions "
        "across all of them. Every answer cites the source it came from."
    ),
    # Moved off the defaults so a stray catch-all cannot impersonate the docs.
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback and return JSON — never an HTML error page.

    A caller expecting JSON that receives HTML fails confusingly at the client.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."},
    )


@app.on_event("startup")
def _startup() -> None:
    init_db()
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    logger.info(
        "Aurora %s started (env=%s, provider=%s)",
        __version__,
        settings.app_env,
        "gemini" if settings.use_gemini else "local",
    )


@app.get("/api/health", response_model=HealthOut, tags=["system"])
def health() -> HealthOut:
    """Liveness probe. Also reports which provider is actually active."""
    database = "ok"
    try:
        from sqlalchemy import text

        from app.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"

    return HealthOut(
        status="ok",
        app=settings.app_name,
        version=__version__,
        provider="gemini" if settings.use_gemini else "local",
        database=database,
    )


app.include_router(auth.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the single-page frontend."""
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise FileNotFoundError("Frontend bundle missing: app/static/index.html")
    return FileResponse(page, media_type="text/html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    icon = STATIC_DIR / "favicon.svg"
    if not icon.is_file():
        raise FileNotFoundError("favicon.svg missing")
    return FileResponse(icon, media_type="image/svg+xml")


@app.get("/{full_path:path}", include_in_schema=False)
def not_found(full_path: str) -> JSONResponse:
    """Explicit JSON 404.

    This exists so an unknown path says "not found" instead of being rewritten
    into the SPA shell, which would make a broken route look healthy.
    """
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"No route for /{full_path}"},
    )
