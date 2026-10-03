#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
steam_root="$worker_root/steamcmd"
runtime_root="$worker_root/runtime-i386"
loader="$runtime_root/usr/lib/i386-linux-gnu/ld-linux.so.2"

if [[ ! -x "$loader" ]]; then
  echo "missing private i386 loader: $loader" >&2
  exit 70
fi
if [[ ! -x "$steam_root/linux32/steamcmd" ]]; then
  echo "missing SteamCMD: $steam_root/linux32/steamcmd" >&2
  exit 70
fi
export LD_LIBRARY_PATH="$steam_root/linux32:$runtime_root/usr/lib/i386-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export STEAMROOT="$steam_root"
ulimit -n 2048
mkdir -p "$worker_root/home" "$worker_root/tmp"

while true; do
  set +e
  bwrap \
    --ro-bind / / \
    --dev-bind /dev /dev \
    --proc /proc \
    --bind "$worker_root" "$worker_root" \
    --tmpfs /tmp \
    --tmpfs /usr/lib \
    --ro-bind /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu \
    --ro-bind "$runtime_root/usr/lib/i386-linux-gnu" /usr/lib/i386-linux-gnu \
    --ro-bind "$loader" /usr/lib/ld-linux.so.2 \
    --setenv HOME "$worker_root/home" \
    --setenv LD_LIBRARY_PATH "$LD_LIBRARY_PATH" \
    --setenv STEAMROOT "$steam_root" \
    --chdir "$steam_root" \
    "$steam_root/linux32/steamcmd" "$@"
  status=$?
  set -e
  [[ $status -eq 42 ]] || exit "$status"
done
