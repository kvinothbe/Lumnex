#!/bin/sh
# Visualizer entrypoint for Railway (optional third service).
#
# Bootstrap runs in the BACKGROUND so uvicorn binds the port immediately and the
# /healthz probe passes; the wiki graph populates once the bootstrap finishes.
set -e

echo "[start_visualizer] launching bootstrap_if_needed in background..."
python -m scripts.bootstrap_if_needed &

echo "[start_visualizer] starting uvicorn on port ${PORT:-8000}"
exec uvicorn vizuara.visualizer.app:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
