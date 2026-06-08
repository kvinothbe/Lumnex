#!/bin/sh
# Visualizer entrypoint for Railway (optional third service).
set -e

echo "[start_visualizer] running bootstrap_if_needed..."
python -m scripts.bootstrap_if_needed

echo "[start_visualizer] starting uvicorn on port ${PORT:-8000}"
exec uvicorn vizuara.visualizer.app:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
