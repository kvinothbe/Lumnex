#!/bin/sh
# Poller entrypoint for Railway. Bootstraps data files once if missing,
# then runs the polling daemon forever.
set -e

echo "[start_poller] running bootstrap_if_needed..."
python -m scripts.bootstrap_if_needed

echo "[start_poller] starting daemon (AUTO_SEND_ENABLED=${VIZUARA_AUTO_SEND_ENABLED:-false})"
exec python -m vizuara.poller.daemon
