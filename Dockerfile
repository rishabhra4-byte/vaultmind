# --- 1. Build the React frontend ---------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Python API that also serves the built frontend ------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini .
COPY backend/demo_drive ./demo_drive
COPY --from=web /web/dist ./static

RUN useradd --create-home vaultmind && mkdir -p /data && chown vaultmind /data
USER vaultmind

# Production mode: a real VAULTMIND_JWT_SECRET is required and demo data is off unless
# VAULTMIND_SEED_DEMO_DATA=true. Migrations run automatically at startup.
ENV VAULTMIND_ENV=production \
    VAULTMIND_DATABASE_URL=sqlite:////data/vaultmind.db \
    VAULTMIND_STORAGE_DIR=/data/storage
VOLUME ["/data"]
EXPOSE 8000

# Readiness, not just liveness: fails if the database is unreachable.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=4).status == 200 else 1)"

# One worker: the search index is held in memory per process (see docs/production-readiness.md).
# uvicorn handles SIGTERM from `docker stop`, finishing in-flight requests for up to 20 seconds.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "20"]
