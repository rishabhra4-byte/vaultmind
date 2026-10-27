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
COPY --from=web /web/dist ./static

RUN useradd --create-home vaultmind && mkdir -p /data && chown vaultmind /data
USER vaultmind

ENV VAULTMIND_DATABASE_URL=sqlite:////data/vaultmind.db \
    VAULTMIND_STORAGE_DIR=/data/storage
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
