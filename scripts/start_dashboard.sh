#!/bin/sh
# Dashboard entrypoint for Railway.
#
# The bootstrap (LumenX sync + wiki + corpus summary + confidence training)
# takes several minutes and makes Anthropic calls on first boot. If we ran it
# BEFORE uvicorn, the port would stay closed and Railway's healthcheck would
# time out. So we run it in the BACKGROUND and bind uvicorn immediately; the
# /healthz probe returns 200 right away while the volume fills in. Pages that
# need data show empty/zero state until the bootstrap finishes, then populate
# on the next refresh.
set -e

echo "[start_dashboard] launching bootstrap_if_needed in background..."
python -m scripts.bootstrap_if_needed &

echo "[start_dashboard] starting uvicorn on port ${PORT:-8000}"
exec uvicorn vizuara.dashboard.app:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
