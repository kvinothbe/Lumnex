# Vizuara container — same image runs either the dashboard or the poller.
# Pick which one via the START_CMD env var (set in Railway per service).

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIZUARA_DATA_DIR=/data

# Minimal OS deps: curl for healthchecks, ca-certificates for HTTPS, libgomp1 for torch.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps in their own layer for cache hits across rebuilds.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# PyTorch CPU build (much smaller + faster install than the default CUDA build).
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "numpy>=1.26" \
 && pip install .

# Bootstrap helper + entrypoint scripts.
COPY scripts/ ./scripts/

# Volume mount target for SQLite, MLP checkpoint, cost log, corpus summary, wiki.
RUN mkdir -p /data
VOLUME ["/data"]

# Default port for the dashboard service.
ENV PORT=8000
EXPOSE 8000

# START_CMD is set per Railway service:
#   dashboard service:  START_CMD="uvicorn vizuara.dashboard.app:app --host 0.0.0.0 --port $PORT"
#   poller service:     START_CMD="python -m vizuara.poller.daemon"
#   visualizer service: START_CMD="uvicorn vizuara.visualizer.app:app --host 0.0.0.0 --port $PORT"
# Default falls back to the dashboard so a single-service deploy "just works".
CMD ["sh", "-c", "${START_CMD:-uvicorn vizuara.dashboard.app:app --host 0.0.0.0 --port $PORT}"]
