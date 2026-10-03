#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
instance_name="${1:-empty-world}"
instance="$worker_root/instances/$instance_name"
server_dir="$worker_root/server/DedicatedServer64"
prefix="$worker_root/prefix"

case "$instance_name" in
  (*[!A-Za-z0-9._-]*|'') echo "invalid instance name: $instance_name" >&2; exit 64 ;;
esac
[[ -x "$HOME/.local/bin/umu-run" ]] || { echo "missing umu-run" >&2; exit 70; }
[[ -f "$server_dir/SpaceEngineersDedicated.exe" ]] || { echo "missing dedicated server" >&2; exit 70; }
[[ -f "$instance/SpaceEngineers-Dedicated.cfg" ]] || { echo "instance is not prepared: $instance" >&2; exit 70; }
bind_ip="$(sed -n 's|.*<IP>\(.*\)</IP>.*|\1|p' "$instance/SpaceEngineers-Dedicated.cfg" | head -1)"
if [[ "$bind_ip" != "127.0.0.1" && "${VRAGECAGE_ALLOW_NON_LOOPBACK:-0}" != "1" ]]; then
  echo "refusing configured non-loopback bind ($bind_ip) without VRAGECAGE_ALLOW_NON_LOOPBACK=1" >&2
  exit 64
fi

export PATH="$HOME/.local/bin:$PATH"
export WINEPREFIX="$prefix"
export GAMEID="umu-default"
export STORE="none"
export PROTONFIXES_DISABLE=1
export UMU_LOG="${UMU_LOG:-warn}"
export PROTON_LOG="${PROTON_LOG:-0}"
export WINEDEBUG="${WINEDEBUG:--all}"
export SteamAppId=298740
export SteamGameId=298740
export WINEDLLOVERRIDES="mscoree=n,b;mshtml=n,b;msvcp140=n,b;msvcp140_1=n,b;msvcp140_2=n,b;vcruntime140=n,b;vcruntime140_1=n,b"

cd "$server_dir"
exec umu-run "$server_dir/SpaceEngineersDedicated.exe" \
  -noconsole \
  -path "$instance" \
  -ignorelastsession
