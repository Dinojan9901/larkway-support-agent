# Stage 1: build the React frontend (frontend/ -> /build/public).
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the FastAPI app, serving the built frontend from ./public.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY evals ./evals
COPY tests ./tests
COPY scripts ./scripts
COPY --from=frontend /build/public ./public

# Build the document index and fail the build if the policy pack has drifted from
# the source PDFs — a mismatch means the agent would answer from stale rules.
RUN python -m app.ingest && python scripts/verify_policy_pack.py

EXPOSE 8000
# Pass LARKWAY_JWT_SECRET (and a model key) at run time, e.g. --env-file .env.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
