#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
worker_root="$temporary/worker"
mkdir -p "$worker_root/server/Content/CustomWorlds/Empty World"
touch "$worker_root/server/Content/CustomWorlds/Empty World/Sandbox.sbc"

VRAGECAGE_WORKER_ROOT="$worker_root" \
  "$repo_root/worker/prepare-instance.sh" test >/dev/null
config="$worker_root/instances/test/SpaceEngineers-Dedicated.cfg"
grep -Fq '<LoadWorld>Z:\' "$config"
grep -Fq '<IP>127.0.0.1</IP>' "$config"

if VRAGECAGE_WORKER_ROOT="$worker_root" \
    "$repo_root/worker/prepare-instance.sh" test >/dev/null 2>&1; then
  echo "prepare-instance overwrote an existing instance without its explicit gate" >&2
  exit 1
fi

if VRAGECAGE_WORKER_ROOT="$worker_root" VRAGECAGE_BIND_IP=0.0.0.0 \
    "$repo_root/worker/prepare-instance.sh" test >/dev/null 2>&1; then
  echo "prepare-instance accepted a non-loopback bind without its explicit gate" >&2
  exit 1
fi

VRAGECAGE_WORKER_ROOT="$worker_root" VRAGECAGE_BIND_IP=0.0.0.0 \
VRAGECAGE_ALLOW_NON_LOOPBACK=1 \
VRAGECAGE_REPREPARE_INSTANCE=1 \
  "$repo_root/worker/prepare-instance.sh" test >/dev/null
grep -Fq '<IP>0.0.0.0</IP>' "$config"

log="$worker_root/instances/test/SpaceEngineersDedicated_20261003_000000000.log"
printf '%s\n' \
  '2026 - Thread: 1 -> App Version: 01_210_014' \
  '2026 - Thread: 1 -> Bind IP : 0.0.0.0:27017' \
  '2026 - Thread: 1 -> Session loaded' \
  '2026 - Thread: 1 -> Game ready...' \
  '2026 - Thread: 1 -> Exiting..' \
  '2026 - Thread: 1 -> Saving world - END' \
  '2026 - Thread: 1 -> Steam closed' \
  '2026 - Thread: 1 -> Log Closed' > "$log"
receipt="$($repo_root/worker/server-receipt.py "$worker_root/instances/test")"
python3 - "$receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is True
assert receipt["clean_shutdown"] is True
assert receipt["app_version"] == "01_210_014"
assert receipt["bind"] == "0.0.0.0:27017"
assert len(receipt["log_sha256"]) == 64
PY

printf 'worker script tests passed\n'
