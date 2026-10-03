#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
instance_name="${1:-empty-world}"
bind_ip="${VRAGECAGE_BIND_IP:-127.0.0.1}"
server_port="${VRAGECAGE_SERVER_PORT:-27017}"
steam_port="${VRAGECAGE_STEAM_PORT:-8767}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
instance="$worker_root/instances/$instance_name"
source_world="$worker_root/server/Content/CustomWorlds/Empty World"
template="$script_dir/fixtures/SpaceEngineers-Dedicated.cfg.in"

case "$instance_name" in
  (*[!A-Za-z0-9._-]*|'') echo "invalid instance name: $instance_name" >&2; exit 64 ;;
esac
if [[ "$bind_ip" != "127.0.0.1" && "${VRAGECAGE_ALLOW_NON_LOOPBACK:-0}" != "1" ]]; then
  echo "refusing non-loopback bind without VRAGECAGE_ALLOW_NON_LOOPBACK=1" >&2
  exit 64
fi
[[ -d "$source_world" ]] || { echo "missing bundled Empty World: $source_world" >&2; exit 70; }
[[ -f "$template" ]] || { echo "missing config template: $template" >&2; exit 70; }
if [[ -e "$instance/SpaceEngineers-Dedicated.cfg" && "${VRAGECAGE_REPREPARE_INSTANCE:-0}" != "1" ]]; then
  echo "refusing to overwrite existing instance; choose a new name or set VRAGECAGE_REPREPARE_INSTANCE=1" >&2
  exit 73
fi

mkdir -p "$instance/World"
cp -a "$source_world/." "$instance/World/"

load_world="Z:${instance//\//\\}\\World"
load_world_sed="${load_world//\\/\\\\}"
sed \
  -e "s|@LOAD_WORLD@|$load_world_sed|g" \
  -e "s|@BIND_IP@|$bind_ip|g" \
  -e "s|@STEAM_PORT@|$steam_port|g" \
  -e "s|@SERVER_PORT@|$server_port|g" \
  "$template" > "$instance/SpaceEngineers-Dedicated.cfg"

printf 'prepared %s\n' "$instance"
