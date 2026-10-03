#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_cli="$repo_root/bin/vragecage"
install_dir="${VRAGECAGE_CLI_BIN:-$HOME/.local/bin}"
target="$install_dir/vragecage"

[[ -x "$source_cli" ]] || { echo "CLI is not executable: $source_cli" >&2; exit 70; }
mkdir -p "$install_dir"

if [[ -L "$target" && "$(readlink -f -- "$target")" == "$(readlink -f -- "$source_cli")" ]]; then
  echo "vragecage already installed at $target"
  exit 0
fi
if [[ -e "$target" || -L "$target" ]]; then
  echo "refusing to replace existing path: $target" >&2
  exit 73
fi

ln -s "$source_cli" "$target"
echo "installed vragecage -> $source_cli"
