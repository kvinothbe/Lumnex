#!/bin/sh
# Dashboard entrypoint for Railway. Bootstraps data files once if missing,
# then starts the FastAPI app on Railway's $PORT.
set -e

echo "[start_dashboard] running bootstrap_if_needed..."
python -m scripts.bootstrap_if_needed

echo "[start_dashboard] starting uvicorn on port ${PORT:-8000}"
exec uvicorn vizuara.dashboard.app:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
