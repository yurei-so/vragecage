#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"

link_sdk() {
  local bits="$1"
  local source="$worker_root/steamcmd/linux$bits/steamclient.so"
  local directory="$HOME/.steam/sdk$bits"
  local target="$directory/steamclient.so"

  [[ -f "$source" ]] || { echo "missing SteamCMD SDK library: $source" >&2; exit 70; }
  mkdir -p "$directory"
  if [[ -L "$target" ]]; then
    [[ "$(readlink -f -- "$target")" == "$(readlink -f -- "$source")" ]] || {
      echo "refusing to replace existing Steam SDK link: $target" >&2
      exit 73
    }
  elif [[ -e "$target" ]]; then
    echo "refusing to replace existing Steam SDK file: $target" >&2
    exit 73
  else
    ln -s "$source" "$target"
  fi
  printf 'sdk%s %s -> %s\n' "$bits" "$target" "$source"
}

link_sdk 32
link_sdk 64
