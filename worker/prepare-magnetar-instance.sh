#!/usr/bin/env bash
set -euo pipefail

worker_root="${VRAGECAGE_WORKER_ROOT:-$HOME/.local/share/vragecage-worker}"
instance_name="${1:-empty-world}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

"$script_dir/prepare-instance.sh" "$instance_name"
instance="$worker_root/instances/$instance_name"

python3 - "$instance/SpaceEngineers-Dedicated.cfg" "$instance/World" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

config = Path(sys.argv[1])
world = Path(sys.argv[2]).resolve()
tree = ET.parse(config)
node = tree.getroot().find("LoadWorld")
if node is None:
    raise SystemExit(f"LoadWorld missing from {config}")
node.text = str(world)
tree.write(config, encoding="utf-8", xml_declaration=True)
PY

printf 'magnetar\n' > "$instance/.vragecage-engine"
mkdir -p "$instance/MagnetarConfig"
printf 'prepared Magnetar instance %s\n' "$instance"
