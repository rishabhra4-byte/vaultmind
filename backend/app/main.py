import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import Base, SessionLocal, engine
from .routers import access, admin, assistant, auth, documents
from .seed import seed
from .services import rebuild_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vaultmind")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"  # the built frontend, in the Docker image


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if get_settings().seed_demo_data and seed(db):
            log.info("Seeded demo company data")
        log.info("Search index built with %d documents", rebuild_index(db))
    yield


app = FastAPI(
    title="VaultMind",
    description="AI document assistant with role- and team-based access control.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, documents.router, assistant.router, access.router, admin.router):
    app.include_router(r)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


if STATIC_DIR.exists():
    # Serve the single-page app; unknown non-API paths fall back to index.html for client routing.
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = STATIC_DIR / path
        if path and candidate.is_file() and STATIC_DIR in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
