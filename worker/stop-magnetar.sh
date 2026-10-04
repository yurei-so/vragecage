#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
instance_name="${1:-}"
timeout_seconds="${VRAGECAGE_STOP_TIMEOUT:-45}"

case "$instance_name" in
  (*[!A-Za-z0-9._-]*|'') echo "invalid instance name: $instance_name" >&2; exit 64 ;;
esac

instance="$worker_root/instances/$instance_name"
mapfile -t pids < <(pgrep -f "MagnetarInterim\.bin.*-path $instance( |$)" || true)
if ((${#pids[@]} == 0)); then
  echo "server is not running"
  exit 0
fi
if ((${#pids[@]} != 1)); then
  echo "refusing ambiguous stop; matched ${#pids[@]} Magnetar processes: ${pids[*]}" >&2
  exit 75
fi

pid="${pids[0]}"
kill -TERM "$pid"
for ((elapsed = 0; elapsed < timeout_seconds; elapsed++)); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "server stopped cleanly"
    exit 0
  fi
  sleep 1
done

echo "server did not stop within ${timeout_seconds}s; no force-kill was attempted" >&2
exit 75
