import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_settings
from .database import SessionLocal, engine
from .migrations import upgrade_database
from .routers import access, admin, assistant, auth, documents
from .seed import seed
from .services import rebuild_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vaultmind")

settings = get_settings()
settings.validate_for_startup()  # refuses to start with an unsafe production config

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"  # the built frontend, in the Docker image

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "font-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    upgrade_database()
    with SessionLocal() as db:
        if settings.demo_enabled and seed(db):
            log.info("Seeded demo company data")
        log.info("Search index built with %d documents", rebuild_index(db))
    yield
    log.info("Shutting down")
    engine.dispose()


class BodySizeLimit:
    """Reject request bodies over the limit before they are parsed or spooled to disk."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._too_large(send)

        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise HTTPException(413, "Request body too large")
            return message

        try:
            await self.app(scope, limited_receive, send)
        except HTTPException:
            if too_large:
                return await self._too_large(send)
            raise

    async def _too_large(self, send: Send) -> None:
        mb = self.max_bytes // (1024 * 1024)
        await JSONResponse({"detail": f"Request is too large. Files are limited to about {mb} MB."}, status_code=413)(
            {"type": "http"}, None, send
        )


app = FastAPI(
    title="VaultMind",
    description="AI document assistant with role- and team-based access control.",
    version="1.1.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # auth uses a bearer header, not cookies
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
# Upload limit plus headroom for the multipart envelope and form fields.
app.add_middleware(BodySizeLimit, max_bytes=(settings.max_upload_mb + 1) * 1024 * 1024)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.hsts:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    path = request.url.path
    if path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")  # API responses carry private data
    else:
        h.setdefault("Content-Security-Policy", CSP)
        if path.startswith("/assets/") or path.startswith("/fonts/"):
            h.setdefault("Cache-Control", "public, max-age=31536000, immutable")  # file names are content-hashed
        else:
            h.setdefault("Cache-Control", "no-cache")
    return response


for r in (auth.router, documents.router, assistant.router, access.router, admin.router):
    app.include_router(r)


@app.get("/api/health", tags=["meta"])
def health():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/api/ready", tags=["meta"])
def ready():
    """Readiness: the database answers. Used by the container health check."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness check failed")
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return {"status": "ok", "database": "ok"}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"], include_in_schema=False)
def api_not_found(path: str):
    """Unknown API paths get a JSON 404, never the web app's HTML."""
    raise HTTPException(404, "Not found")


def mount_frontend(target: FastAPI, static_dir: Path) -> None:
    """Serve the single-page app; unknown non-API paths fall back to index.html for client routing."""
    if not static_dir.exists():
        return
    target.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
    if (static_dir / "fonts").exists():
        target.mount("/fonts", StaticFiles(directory=static_dir / "fonts"), name="fonts")
    root = static_dir.resolve()

    @target.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")


mount_frontend(app, STATIC_DIR)
