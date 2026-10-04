#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
instance_name="${1:-empty-world}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
instance="$worker_root/instances/$instance_name"
magnetar_dir="${VRAGECAGE_MAGNETAR_DIR:-$worker_root/magnetar/current}"
server_dir="$worker_root/server/DedicatedServer64"
# Magnetar derives its private Workshop cache from the Steam install topology.
# Preserve this path spelling instead of resolving the symlink to server_dir.
magnetar_ds64="$worker_root/magnetar-steam/steamapps/common/SpaceEngineersDedicatedServer/DedicatedServer64"

case "$instance_name" in
  (*[!A-Za-z0-9._-]*|'') echo "invalid instance name: $instance_name" >&2; exit 64 ;;
esac
[[ -x "$magnetar_dir/MagnetarInterim.bin" ]] || { echo "missing Magnetar launcher: $magnetar_dir/MagnetarInterim.bin" >&2; exit 70; }
[[ -x "$worker_root/dotnet/dotnet" ]] || { echo "missing worker .NET runtime" >&2; exit 70; }
[[ -f "$server_dir/SpaceEngineersDedicated.exe" ]] || { echo "missing dedicated server" >&2; exit 70; }
[[ -f "$magnetar_ds64/SpaceEngineersDedicated.exe" ]] || { echo "missing Magnetar Steam-shaped server link" >&2; exit 70; }
[[ -f "$instance/SpaceEngineers-Dedicated.cfg" ]] || { echo "instance is not prepared: $instance" >&2; exit 70; }
[[ "$(cat "$instance/.vragecage-engine" 2>/dev/null)" == "magnetar" ]] || { echo "instance is not a Magnetar instance" >&2; exit 64; }

if [[ "${VRAGECAGE_LAB_AUTHORITY:-}" == "destructive-lab" ]]; then
  [[ -n "${VRAGECAGE_LAB_RUN_ID:-}" ]] || { echo "missing destructive-lab run identity" >&2; exit 64; }
  "$script_dir/lab-contract.py" verify-runtime "$instance" "$VRAGECAGE_LAB_RUN_ID" >/dev/null
elif [[ -e "$instance/.vragecage-lab.json" ]]; then
  echo "refusing to launch a destructive-lab instance without activated authority" >&2
  exit 64
fi

bind_ip="$(sed -n 's|.*<IP>\(.*\)</IP>.*|\1|p' "$instance/SpaceEngineers-Dedicated.cfg" | head -1)"
if [[ "$bind_ip" != "127.0.0.1" && "${VRAGECAGE_ALLOW_NON_LOOPBACK:-0}" != "1" ]]; then
  echo "refusing configured non-loopback bind ($bind_ip) without VRAGECAGE_ALLOW_NON_LOOPBACK=1" >&2
  exit 64
fi

export DOTNET_ROOT="$worker_root/dotnet"
export PATH="$DOTNET_ROOT:$PATH"
cd "$magnetar_dir"
exec "$magnetar_dir/MagnetarInterim.bin" \
  -ds64 "$magnetar_ds64" \
  -path "$instance" \
  -config "$instance/MagnetarConfig" \
  -noimplicitmod \
  -consent deny \
  -multiInstance
