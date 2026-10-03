#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
instance_name="${1:-empty-world}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
instance="$worker_root/instances/$instance_name"
startup_timeout="${VRAGECAGE_STARTUP_TIMEOUT:-180}"
previous_log="$(find "$instance" -maxdepth 1 -name 'SpaceEngineersDedicated_*.log' -type f -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"

VRAGECAGE_WORKER_ROOT="$worker_root" "$script_dir/link-steam-sdk.sh"
VRAGECAGE_WORKER_ROOT="$worker_root" \
VRAGECAGE_BIND_IP=0.0.0.0 \
VRAGECAGE_ALLOW_NON_LOOPBACK=1 \
VRAGECAGE_REPREPARE_INSTANCE="${VRAGECAGE_REPREPARE_INSTANCE:-0}" \
  "$script_dir/prepare-instance.sh" "$instance_name"

VRAGECAGE_WORKER_ROOT="$worker_root" \
VRAGECAGE_ALLOW_NON_LOOPBACK=1 \
  "$script_dir/run-server.sh" "$instance_name" &
launcher_pid=$!

cleanup() {
  VRAGECAGE_WORKER_ROOT="$worker_root" "$script_dir/stop-server.sh" "$instance_name" || true
  wait "$launcher_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for ((elapsed = 0; elapsed < startup_timeout; elapsed++)); do
  current_log="$(find "$instance" -maxdepth 1 -name 'SpaceEngineersDedicated_*.log' -type f -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
  if [[ -n "$current_log" && "$current_log" != "$previous_log" ]] && \
      "$script_dir/server-receipt.py" "$instance" >/dev/null 2>&1; then
    VRAGECAGE_WORKER_ROOT="$worker_root" "$script_dir/stop-server.sh" "$instance_name"
    wait "$launcher_pid" || true
    trap - EXIT INT TERM
    "$script_dir/server-receipt.py" "$instance"
    exit 0
  fi
  if ! kill -0 "$launcher_pid" 2>/dev/null; then
    wait "$launcher_pid" || true
    "$script_dir/server-receipt.py" "$instance" || true
    echo "server exited before readiness" >&2
    exit 1
  fi
  sleep 1
done

"$script_dir/server-receipt.py" "$instance" || true
echo "server did not become ready within ${startup_timeout}s" >&2
exit 1
