#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
server_exe="$worker_root/server/DedicatedServer64/SpaceEngineersDedicated.exe"
timeout_seconds="${VRAGECAGE_STOP_TIMEOUT:-45}"
instance_name="${1:-}"
declare -a pids=()

if [[ -n "$instance_name" ]]; then
  case "$instance_name" in
    (*[!A-Za-z0-9._-]*|'') echo "invalid instance name: $instance_name" >&2; exit 64 ;;
  esac
  process_pattern="^[A-Z]:.*vragecage-worker.*SpaceEngineersDedicated\\.exe.*-path .*/instances/$instance_name.*-ignorelastsession"
else
  process_pattern='^[A-Z]:.*vragecage-worker.*SpaceEngineersDedicated\.exe.*-noconsole'
fi

while IFS= read -r pid; do
  [[ -n "$pid" ]] && pids+=("$pid")
done < <(pgrep -f "$process_pattern" || true)

if ((${#pids[@]} == 0)); then
  echo "server is not running"
  exit 0
fi
if ((${#pids[@]} != 1)); then
  echo "refusing ambiguous stop; matched ${#pids[@]} server processes: ${pids[*]}" >&2
  exit 75
fi

pid="${pids[0]}"
kill -INT "$pid"
for ((elapsed = 0; elapsed < timeout_seconds; elapsed++)); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "server stopped cleanly"
    exit 0
  fi
  sleep 1
done

echo "server did not stop within ${timeout_seconds}s; no force-kill was attempted" >&2
exit 75
