#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
python3 "$repo_root/worker/tests/test-cli-wait.py"
python3 "$repo_root/worker/tests/test-import-world-client-dispatch.py"
python3 "$repo_root/worker/tests/test-local-vehicle-test-packages.py"
python3 "$repo_root/worker/tests/test-instrument-v4-target.py"
python3 "$repo_root/worker/tests/test-magnetar-receipt-policy.py"
python3 "$repo_root/worker/tests/test-preflight-launch.py"
python3 "$repo_root/worker/tests/test-magnetar-local-registration.py"
python3 "$repo_root/worker/tests/test-lab-contract-preflight.py"
python3 "$repo_root/worker/tests/test-lab-trial.py"
python3 "$repo_root/worker/tests/test-holoview-capture.py"
python3 "$repo_root/worker/tests/test-holoview-multiview-video.py"
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

blocked_instance="$worker_root/instances/local-mod-blocked"
mkdir -p "$blocked_instance"
blocked_log="$blocked_instance/SpaceEngineersDedicated_20261003_000000001.log"
printf '%s\n' \
  '2026 - Thread: 1 -> App Version: 01_210_014' \
  '2026 - Thread: 1 -> Downloading world mods - START' \
  '2026 - Thread: 1 -> Local mods are not allowed in multiplayer.' \
  '2026 - Thread: 1 -> Unable to download mods.' \
  '2026 - Thread: 1 -> Exiting..' \
  '2026 - Thread: 1 -> Steam closed' \
  '2026 - Thread: 1 -> Log Closed' > "$blocked_log"
if blocked_receipt="$($repo_root/worker/server-receipt.py "$blocked_instance")"; then
  echo "local-mod rejection produced a passing receipt" >&2
  exit 1
fi
python3 - "$blocked_receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is False
assert receipt["session_loaded"] is False
assert any("Local mods are not allowed" in line for line in receipt["fatal_markers"])
assert any("Unable to download mods" in line for line in receipt["fatal_markers"])
PY

mkdir -p "$temporary/mod/Data" "$temporary/mod-instance/World"
printf '<Definitions />\n' > "$temporary/mod/Data/Test.sbc"
printf '%s\n' \
  '{"schema":"vragecage.mod-contract.v1","expected_log_markers":["TestMod bootstrap complete"],"observation_prefix":"TestMod observation: "}' \
  > "$temporary/mod/vragecage.integration.json"
printf '%s\n' \
  '<?xml version="1.0"?><MyObjectBuilder_Checkpoint xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Mods /><ScriptManager xsi:type="xsd:string">DQAAgL8VAACAvx0AAABAIAEoATABOABAAEgBUAFYAGEAAAAAgIQuQWgBcAF9AACAP4ABPIoBCg0zMzM=</ScriptManager></MyObjectBuilder_Checkpoint>' \
  > "$temporary/mod-instance/World/Sandbox.sbc"
printf '<?xml version="1.0"?><MyObjectBuilder_WorldConfiguration><Mods /></MyObjectBuilder_WorldConfiguration>\n' \
  > "$temporary/mod-instance/World/Sandbox_config.sbc"
"$repo_root/worker/register-mod.py" "$temporary/mod-instance" TestMod "$temporary/mod" >/dev/null
test -f "$temporary/mod-instance/Mods/TestMod/Data/Test.sbc"
python3 - "$temporary/mod-instance" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

instance = Path(sys.argv[1])
checkpoint = (instance / "World/Sandbox.sbc").read_text(encoding="utf-8")
assert 'xmlns:xsd="http://www.w3.org/2001/XMLSchema"' in checkpoint
assert 'xsi:type="xsd:string"' in checkpoint
for name in ("Sandbox.sbc", "Sandbox_config.sbc"):
    mods = ET.parse(instance / "World" / name).getroot().find("Mods")
    assert mods is not None
    item = mods.find("ModItem")
    assert item is not None
    assert item.findtext("Name") == "TestMod"
    assert item.findtext("PublishedFileId") == "0"
PY

VRAGECAGE_WORKER_ROOT="$worker_root" VRAGECAGE_BIND_IP=0.0.0.0 \
VRAGECAGE_ALLOW_NON_LOOPBACK=1 \
  "$repo_root/worker/prepare-magnetar-instance.sh" magnetar >/dev/null
magnetar_instance="$worker_root/instances/magnetar"
test "$(cat "$magnetar_instance/.vragecage-engine")" = magnetar
python3 - "$magnetar_instance" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

instance = Path(sys.argv[1]).resolve()
assert ET.parse(instance / "SpaceEngineers-Dedicated.cfg").getroot().findtext("LoadWorld") == str(instance / "World")
PY

mkdir -p "$worker_root/incoming/imported-world"
printf '%s\n' '<Checkpoint xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Mods><ModItem><Name>TestMod</Name></ModItem><ModItem><Name>Published</Name><PublishedFileId>123</PublishedFileId></ModItem></Mods><ScriptManager xsi:type="xsd:string">opaque</ScriptManager><Gps><dictionary><item><Key>42</Key><Value><Entries><Entry><name>Red Zone</name><description>test target</description><coords><X>1</X><Y>2</Y><Z>3</Z></coords></Entry></Entries></Value></item></dictionary></Gps></Checkpoint>' \
  > "$worker_root/incoming/imported-world/Sandbox.sbc"
printf '%s\n' '<WorldConfiguration xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><Mods><ModItem><Name>TestMod</Name><PublishedFileId>0</PublishedFileId></ModItem><ModItem><Name>Published</Name><PublishedFileId>123</PublishedFileId></ModItem></Mods></WorldConfiguration>' \
  > "$worker_root/incoming/imported-world/Sandbox_config.sbc"
printf '<Sector xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><SectorObjects><MyObjectBuilder_EntityBase xsi:type="MyObjectBuilder_CubeGrid"><EntityId>9001</EntityId><DisplayName>Camera Drone</DisplayName><GridSizeEnum>Small</GridSizeEnum><CubeBlocks><MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_RemoteControl"><SubtypeName>SmallBlockRemoteControl</SubtypeName><EntityId>12345</EntityId><ComponentContainer><Components /></ComponentContainer><ShowOnHUD>false</ShowOnHUD></MyObjectBuilder_CubeBlock><MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_CameraBlock"><SubtypeName>SmallCameraBlock</SubtypeName><EntityId>67890</EntityId></MyObjectBuilder_CubeBlock></CubeBlocks></MyObjectBuilder_EntityBase></SectorObjects></Sector>\n' > "$worker_root/incoming/imported-world/SANDBOX_0_0_0_.sbs"
printf 'stale binary cache\n' > "$worker_root/incoming/imported-world/SANDBOX_0_0_0_.sbsB5"
VRAGECAGE_WORKER_ROOT="$worker_root" VRAGECAGE_BIND_IP=0.0.0.0 \
VRAGECAGE_ALLOW_NON_LOOPBACK=1 \
  "$repo_root/worker/import-world.py" imported "$worker_root/incoming/imported-world" \
    --engine magnetar --destructive-lab >/dev/null
test "$(cat "$worker_root/instances/imported/.vragecage-engine")" = magnetar
cmp "$worker_root/incoming/imported-world/SANDBOX_0_0_0_.sbs" \
  "$worker_root/instances/imported/World/SANDBOX_0_0_0_.sbs"
python3 - "$worker_root/instances/imported/.vragecage-world-import.json" <<'PY'
import json
import sys

receipt = json.load(open(sys.argv[1], encoding="utf-8"))
assert receipt["schema"] == "vragecage.world-import.v1"
assert receipt["engine"] == "magnetar"
assert receipt["file_count"] == 4
assert receipt["byte_count"] > 0
assert len(receipt["tree_sha256"]) == 64
PY
python3 - "$worker_root/instances/imported" <<'PY'
import json
import sys
from pathlib import Path

instance = Path(sys.argv[1])
root = json.loads((instance / ".vragecage-lab.json").read_text())
assert root["authority"] == "destructive-lab"
assert root["disposable"] is True
assert root["source_tree_sha256"] == json.loads(
    (instance / ".vragecage-world-import.json").read_text()
)["tree_sha256"]
PY
voidwright_source="$temporary/voidwright-source"
mkdir -p "$voidwright_source/Data/Scripts"
printf 'test-only Voidwright assembly placeholder\n' > "$voidwright_source/Data/Scripts/Voidwright.dll"
"$repo_root/worker/register-magnetar-mod.py" "$worker_root/instances/imported" Voidwright "$voidwright_source" >/dev/null
command="$($repo_root/worker/lab-contract.py plan-drive-to \
  "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
  --max-speed 0.5 --arrival-radius 2 --timeout-ticks 600 --depth-camera-id 67890)"
if "$repo_root/worker/lab-contract.py" plan-drive \
    "$worker_root/instances/imported" 12345 --distance 2 --max-speed 1 --timeout-ticks 600 \
    >/dev/null 2>&1; then
  echo "lab contract accepted a second pending command" >&2
  exit 1
fi
active="$($repo_root/worker/lab-contract.py activate "$worker_root/instances/imported")"
run_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_id"])' "$active")"
operation_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["command"]["operation_id"])' "$active")"
python3 - "$command" "$active" "$worker_root/instances/imported" "$repo_root" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

command = json.loads(sys.argv[1])
active = json.loads(sys.argv[2])
instance = Path(sys.argv[3])
assert active["command"] == command
assert len(active["command_sha256"]) == 64
assert not (instance / ".vragecage-pending-command.json").exists()
assert (Path(active["quarantine"]) / "command.json").is_file()
context = active["preflight_context"]
context_path = Path(context["path"])
assert context_path.parent == Path(active["quarantine"])
assert context_path.stat().st_mode & 0o077 == 0
context_bytes = context_path.read_bytes()
assert hashlib.sha256(context_bytes).hexdigest() == context["sha256"]
fields = dict(line.split("=", 1) for line in context_bytes.decode().splitlines())
assert fields["run_id"] == active["run_id"]
assert fields["fixture_id"] == active["fixture_id"]
assert fields["operation_id"] == command["operation_id"]
assert fields["action"] == command["action"]
assert fields["source_tree_sha256"] == json.loads(
    (instance / ".vragecage-lab.json").read_text()
)["source_tree_sha256"]
assert len(fields["fixture_sector_sha256"]) == 64
assert fields["fixture_sector_path"].endswith("/World/SANDBOX_0_0_0_.sbs")
assert fields["mods_path"].endswith("/Mods")
assert len(fields["mods_tree_sha256"]) == 64
assert (instance / "Mods/Voidwright/Data/Scripts/Voidwright.dll").is_file()
assert len(fields["command_sha256"]) == 64
command_path = context_path.parent / "command.json"
command_bytes = command_path.read_bytes()
try:
    command_path.write_bytes(command_bytes + b" ")
    rejected = subprocess.run(
        [str(Path(sys.argv[4]) / "worker/lab-contract.py"),
         "preflight-context", str(instance), active["run_id"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    assert rejected.returncode != 0, "preflight context accepted a changed command artifact"
finally:
    command_path.write_bytes(command_bytes)
PY
reverse_command="$("$repo_root/worker/lab-contract.py" plan-drive \
  "$worker_root/instances/imported" 12345 --distance 1.5 --max-speed 0.25 \
  --timeout-ticks 600 --reverse)"
python3 - "$reverse_command" <<'PY'
import json
import sys

command = json.loads(sys.argv[1])
assert command["action"] == "drive-distance"
assert command["reverse"] is True
assert command["distance_meters"] == 1.5
assert command["max_speed"] == 0.25
assert command["timeout_ticks"] == 600
PY
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
if "$repo_root/worker/lab-contract.py" plan-drive \
    "$worker_root/instances/imported" 12345 --distance 3.1 --max-speed 0.25 \
    --timeout-ticks 600 --reverse >/dev/null 2>&1; then
  echo "lab contract accepted an overlong reverse maneuver" >&2
  exit 1
fi
if "$repo_root/worker/lab-contract.py" plan-drive \
    "$worker_root/instances/imported" 12345 --distance 1.5 --max-speed 0.6 \
    --timeout-ticks 600 --reverse >/dev/null 2>&1; then
  echo "lab contract accepted an over-speed reverse maneuver" >&2
  exit 1
fi
(set +e; "$repo_root/worker/lab-contract.py" plan-drive \
  "$worker_root/instances/imported" 12345 --distance 1 --max-speed 1 --timeout-ticks 600 \
  >/dev/null 2>&1; printf '%s\n' "$?" > "$temporary/plan-a.status") &
plan_a=$!
(set +e; "$repo_root/worker/lab-contract.py" plan-drive \
  "$worker_root/instances/imported" 12345 --distance 1 --max-speed 1 --timeout-ticks 600 \
  >/dev/null 2>&1; printf '%s\n' "$?" > "$temporary/plan-b.status") &
plan_b=$!
wait "$plan_a" "$plan_b"
if [[ "$(grep -h '^0$' "$temporary/plan-a.status" "$temporary/plan-b.status" | wc -l)" != 1 ]]; then
  echo "concurrent lab command planning did not accept exactly one writer" >&2
  exit 1
fi
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
sensor_command="$($repo_root/worker/lab-contract.py plan-sensor-frame \
  "$worker_root/instances/imported" 67890 --width 32 --height 18 --range 150 --fov 70)"
python3 - "$sensor_command" <<'PY'
import json
import sys

command = json.loads(sys.argv[1])
assert command["action"] == "sensor-frame"
assert command["camera_id"] == 67890
assert command["width"] == 32 and command["height"] == 18
assert command["range_meters"] == 150
assert command["fov_degrees"] == 70
assert len(command["operation_id"]) == 32
PY
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
if "$repo_root/worker/lab-contract.py" plan-sensor-frame \
    "$worker_root/instances/imported" 67890 --width 65 --height 18 --range 150 --fov 70 \
    >/dev/null 2>&1; then
  echo "lab sensor contract accepted an oversized frame" >&2
  exit 1
fi
stator_command="$($repo_root/worker/lab-contract.py plan-rotate-stator \
  "$worker_root/instances/imported" 135678125455620908 113964912097958351 \
  'Obstacle Ramp Camera Rotor' 45 --max-rpm 0.5 --timeout-ticks 1800)"
python3 - "$stator_command" <<'PY'
import json
import sys

command = json.loads(sys.argv[1])
assert command["action"] == "rotate-stator"
assert command["stator_id"] == 135678125455620908
assert command["camera_id"] == 113964912097958351
assert command["expected_name"] == "Obstacle Ramp Camera Rotor"
assert command["desired_angle_degrees"] == 45
assert command["max_rpm"] == 0.5 and command["timeout_ticks"] == 1800
PY
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
reject_stator_args() {
  if "$repo_root/worker/lab-contract.py" plan-rotate-stator \
      "$worker_root/instances/imported" "$@" >/dev/null 2>&1; then
    echo "lab stator contract accepted an out-of-bounds command" >&2
    exit 1
  fi
}
reject_stator_args 135678125455620908 113964912097958351 Rotor 361 --max-rpm 0.5 --timeout-ticks 1800
reject_stator_args 135678125455620908 113964912097958351 Rotor 45 --max-rpm 0.6 --timeout-ticks 1800
reject_stator_args 135678125455620908 113964912097958351 Rotor 45 --max-rpm 0.5 --timeout-ticks 3601
navigation_command="$($repo_root/worker/lab-contract.py plan-drive-to \
  "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
  --max-speed 1 --arrival-radius 2 --timeout-ticks 1800 --offset-left 4)"
python3 - "$navigation_command" <<'PY'
import json
import sys
command = json.loads(sys.argv[1])
assert command["action"] == "drive-to-coordinate"
assert command["zone"] == "Red Zone"
assert command["target"] == [1.0, 2.0, 3.0]
assert command["target_offset_left_m"] == 4.0
assert command["arrival_radius"] == 2.0
PY
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
if "$repo_root/worker/lab-contract.py" plan-drive-to \
    "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
    --max-speed 1 --arrival-radius 2 --timeout-ticks 1800 --offset-left 10.1 \
    >/dev/null 2>&1; then
  echo "lab navigation contract accepted an oversized lateral offset" >&2
  exit 1
fi
depth_navigation_command="$($repo_root/worker/lab-contract.py plan-drive-to \
  "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
  --max-speed 0.5 --arrival-radius 2 --timeout-ticks 900 --depth-camera-id 67890 \
  --start-delay-ticks 3600)"
python3 - "$depth_navigation_command" <<'PY'
import json
import sys
command = json.loads(sys.argv[1])
assert command["action"] == "drive-to-coordinate-depth"
assert command["depth_camera_id"] == 67890
assert command["max_speed"] == 0.5
assert command["timeout_ticks"] == 900
assert command["start_delay_ticks"] == 3600
PY
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
if "$repo_root/worker/lab-contract.py" plan-drive-to \
    "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
    --max-speed 0.5 --arrival-radius 2 --timeout-ticks 900 --depth-camera-id 67890 \
    --start-delay-ticks 3601 >/dev/null 2>&1; then
  echo "depth navigation contract accepted an excessive start delay" >&2
  exit 1
fi
if "$repo_root/worker/lab-contract.py" plan-drive-to \
    "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
    --max-speed 1 --arrival-radius 2 --timeout-ticks 900 --start-delay-ticks 60 \
    >/dev/null 2>&1; then
  echo "non-depth navigation contract accepted a start delay" >&2
  exit 1
fi
if "$repo_root/worker/lab-contract.py" plan-drive-to \
    "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
    --max-speed 1 --arrival-radius 2 --timeout-ticks 900 --depth-camera-id 67890 \
    >/dev/null 2>&1; then
  echo "depth navigation contract accepted an unsafe speed cap" >&2
  exit 1
fi
if "$repo_root/worker/lab-contract.py" plan-drive-to \
    "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
    --max-speed 0.5 --arrival-radius 2 --timeout-ticks 1801 --depth-camera-id 67890 \
    >/dev/null 2>&1; then
  echo "depth navigation contract accepted an excessive tick limit" >&2
  exit 1
fi
if "$repo_root/worker/lab-contract.py" plan-drive-to \
    "$worker_root/instances/imported" 12345 'Red Zone' 1 2 3 \
    --max-speed 0.5 --arrival-radius 2 --timeout-ticks 900 --depth-camera-id 0 \
    >/dev/null 2>&1; then
  echo "depth navigation contract accepted an invalid camera identity" >&2
  exit 1
fi
recorded_command="$($repo_root/worker/lab-contract.py plan-recorded-route \
  "$worker_root/instances/imported" 12345 54321 --duration-ticks 600)"
python3 - "$recorded_command" <<'PY'
import json
import sys
command = json.loads(sys.argv[1])
assert command["action"] == "play-recorded-route"
assert command["controller_id"] == 12345
assert command["recorder_id"] == 54321
assert command["duration_ticks"] == 600
assert len(command["operation_id"]) == 32
PY
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
if "$repo_root/worker/lab-contract.py" plan-recorded-route \
    "$worker_root/instances/imported" 12345 54321 --duration-ticks 7201 \
    >/dev/null 2>&1; then
  echo "lab recorded-route contract accepted an unbounded duration" >&2
  exit 1
fi
follow_command="$($repo_root/worker/lab-contract.py plan-follow-recorder \
  "$worker_root/instances/imported" 12345 54321 --max-speed 1 --arrival-radius 2 --timeout-ticks 7200)"
python3 - "$follow_command" <<'PY'
import json
import sys
command = json.loads(sys.argv[1])
assert command["action"] == "drive-recorded-route"
assert command["controller_id"] == 12345 and command["recorder_id"] == 54321
assert command["max_speed"] == 1.0 and command["arrival_radius"] == 2.0
assert command["timeout_ticks"] == 7200
PY
rm "$worker_root/instances/imported/.vragecage-pending-command.json"
if "$repo_root/worker/lab-contract.py" plan-follow-recorder \
    "$worker_root/instances/imported" 12345 54321 --max-speed 5.1 --arrival-radius 2 --timeout-ticks 7200 \
    >/dev/null 2>&1; then
  echo "lab follow-recorder contract accepted an excessive speed" >&2
  exit 1
fi
VRAGECAGE_WORKER_ROOT="$worker_root" \
  "$repo_root/worker/lab-contract.py" verify-runtime \
    "$worker_root/instances/imported" "$run_id" >/dev/null
ln -s imported "$worker_root/instances/imported-link"
if "$repo_root/worker/lab-contract.py" verify-runtime \
    "$worker_root/instances/imported-link" "$run_id" >/dev/null 2>&1; then
  echo "lab runtime verification accepted a symlinked instance" >&2
  exit 1
fi
rm "$worker_root/instances/imported-link"
if "$repo_root/worker/lab-contract.py" verify-runtime \
    "$worker_root/instances/imported" wrong-run >/dev/null 2>&1; then
  echo "lab runtime verification accepted a mismatched run identity" >&2
  exit 1
fi
inspection="$($repo_root/worker/fixture-contract.py inspect "$worker_root/instances/imported")"
python3 - "$inspection" <<'PY'
import json
import sys

result = json.loads(sys.argv[1])
assert result["grids"][0]["name"] == "Camera Drone"
assert result["grids"][0]["remote_controls"][0]["entity_id"] == 12345
assert result["grids"][0]["cameras"][0]["entity_id"] == 67890
assert result["gps_targets"] == [{"description": "test target", "name": "Red Zone", "x": 1.0, "y": 2.0, "z": 3.0}]
PY
opt_in="$($repo_root/worker/fixture-contract.py opt-in-voidwright \
  "$worker_root/instances/imported" 12345 --label 'Camera Drone')"
python3 - "$opt_in" "$worker_root/instances/imported" <<'PY'
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

result = json.loads(sys.argv[1])
instance = Path(sys.argv[2])
assert result["status"] == "opted_in"
assert result["edit"]["new_name"] == "[Voidwright] Camera Drone"
assert result["edit"]["new_grid_name"] == "[Voidwright] Camera Drone"
assert len(result["edit_ledger_sha256"]) == 64
assert len(result["edit"]["binary_cache_sha256"]) == 64
tree = ET.parse(instance / "World/SANDBOX_0_0_0_.sbs")
block = tree.getroot().find(".//MyObjectBuilder_CubeBlock")
assert block.findtext("CustomName") == "[Voidwright] Camera Drone"
assert tree.getroot().find(".//MyObjectBuilder_EntityBase/DisplayName").text == "[Voidwright] Camera Drone"
children = [child.tag for child in block]
assert children.index("ComponentContainer") < children.index("CustomName") < children.index("ShowOnHUD")
ledger = json.loads((instance / ".vragecage-fixture-edits.json").read_text())
assert len(ledger["edits"]) == 1
cache_receipt = instance / ledger["edits"][0]["binary_cache_quarantine"]
assert cache_receipt.read_text() == "stale binary cache\n"
assert not (instance / "World/SANDBOX_0_0_0_.sbsB5").exists()
PY
already="$($repo_root/worker/fixture-contract.py opt-in-voidwright \
  "$worker_root/instances/imported" 12345 --label 'Camera Drone')"
python3 - "$already" <<'PY'
import json
import sys
assert json.loads(sys.argv[1])["status"] == "already_opted_in"
PY
command="$($repo_root/worker/lab-contract.py plan-drive \
  "$worker_root/instances/imported" 12345 --distance 2 --max-speed 1 --timeout-ticks 600)"
active="$($repo_root/worker/lab-contract.py activate "$worker_root/instances/imported")"
run_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_id"])' "$active")"
operation_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["command"]["operation_id"])' "$active")"
python3 - "$active" <<'PY'
import json
import sys

active = json.loads(sys.argv[1])
assert len(active["fixture_edit_ledger_sha256"]) == 64
assert len(active["fixture_sector_sha256"]) == 64
assert active["fixture_edit_count"] == 1
PY
if VRAGECAGE_WORKER_ROOT="$worker_root" \
    "$repo_root/worker/import-world.py" invalid-lab "$worker_root/incoming/imported-world" \
      --engine proton --destructive-lab >/dev/null 2>&1; then
  echo "destructive-lab authority accepted the unsupported Proton engine" >&2
  exit 1
fi
ln -s Sandbox.sbc "$worker_root/incoming/imported-world/unsafe-link"
if "$repo_root/bin/vragecage" --host unreachable.invalid stage-world client-unsafe \
    "$worker_root/incoming/imported-world" >/dev/null 2>"$temporary/client-upload-error"; then
  echo "client world upload accepted a symlink" >&2
  exit 1
fi
grep -Fq 'upload contains unsupported filesystem entry: unsafe-link' \
  "$temporary/client-upload-error"
if VRAGECAGE_WORKER_ROOT="$worker_root" \
    "$repo_root/worker/import-world.py" unsafe "$worker_root/incoming/imported-world" \
      --engine magnetar >/dev/null 2>&1; then
  echo "world import accepted a symlink" >&2
  exit 1
fi

stage_receipt="$($repo_root/worker/register-magnetar-mod.py "$magnetar_instance" TestMod "$temporary/mod")"
workshop_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["synthetic_workshop_id"])' "$stage_receipt")"
cache_path="$worker_root/magnetar-steam/steamapps/workshop/content/244850/$workshop_id"
cache_hash_before="$(find "$cache_path" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
python3 - "$worker_root/instances/imported" "$workshop_id" <<'PY'
import sys
from pathlib import Path

instance = Path(sys.argv[1])
workshop_id = sys.argv[2]
entry = (
    f"<ModItem><Name>{workshop_id}.sbm</Name><PublishedFileId>{workshop_id}</PublishedFileId>"
    "<PublishedServiceName>Steam</PublishedServiceName></ModItem>"
).encode()
for name in ("Sandbox.sbc", "Sandbox_config.sbc"):
    path = instance / "World" / name
    raw = path.read_bytes()
    assert raw.count(b"<Mods>") == 1
    path.write_bytes(raw.replace(b"<Mods>", b"<Mods>" + entry, 1))
PY
imported_stage_receipt="$($repo_root/worker/register-magnetar-mod.py \
  "$worker_root/instances/imported" TestMod "$cache_path" --reuse-existing-cache)"
cache_hash_after="$(find "$cache_path" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
test "$cache_hash_before" = "$cache_hash_after"
python3 - "$worker_root/instances/imported" "$imported_stage_receipt" <<'PY'
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

instance = Path(sys.argv[1])
receipt = json.loads(sys.argv[2])
assert receipt["removed_unpublished_world_entries"] == 4
assert receipt["cache_reused_without_writes"] is True
assert receipt["load_world"] == str(instance / "World")
assert len(receipt["sources_sha256"]) == len(receipt["profile_sha256"]) == 64
for name in ("Sandbox.sbc", "Sandbox_config.sbc"):
    checkpoint_path = instance / "World" / name
    checkpoint = checkpoint_path.read_text(encoding="utf-8")
    assert 'xmlns:xsd="http://www.w3.org/2001/XMLSchema"' in checkpoint
    assert 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' in checkpoint
    if name == "Sandbox.sbc":
        assert 'xsi:type="xsd:string"' in checkpoint
    mods = ET.parse(checkpoint_path).getroot().find("Mods")
    assert mods is not None
    entries = [(node.findtext("Name"), node.findtext("PublishedFileId")) for node in mods]
    assert ("TestMod", None) not in entries
    assert ("TestMod", "0") not in entries
    assert (str(receipt["synthetic_workshop_id"]) + ".sbm",
            str(receipt["synthetic_workshop_id"])) not in entries
    assert ("Published", "123") in entries
PY
python3 - "$workshop_id" <<'PY'
import sys

value = int(sys.argv[1])
assert 90_000_000_000_000_000 <= value < 90_100_000_000_000_000
PY
test -f "$worker_root/magnetar-steam/steamapps/workshop/content/244850/$workshop_id/Data/Test.sbc"
test -f "$magnetar_instance/MagnetarConfig/Sources/sources.xml"
test -f "$magnetar_instance/MagnetarConfig/Profiles/Current.xml"
mkdir -p "$worker_root/instances/imported/MagnetarConfig/DevFolder/test-server-plugin-stale/Bin"
printf 'stale' > "$worker_root/instances/imported/MagnetarConfig/DevFolder/test-server-plugin-stale/Bin/plugin.dll"

plugin_source="$worker_root/incoming/test-plugin"
mkdir -p "$plugin_source"
printf '%s\n' 'public sealed class PluginSourceMarker { }' > "$plugin_source/Plugin.cs"
printf '%s\n' '<?xml version="1.0"?><PluginData><Id>test-server-plugin</Id><FriendlyName>Test Server Plugin</FriendlyName></PluginData>' \
  > "$plugin_source/TestPlugin.xml"
printf '%s\n' '{"schema":"vragecage.plugin-contract.v1","expected_log_markers":["Test plugin ready"]}' \
  > "$plugin_source/vragecage.plugin.json"
plugin_receipt="$($repo_root/worker/register-magnetar-plugin.py \
  "$worker_root/instances/imported" test-server-plugin "$plugin_source")"
python3 - "$worker_root/instances/imported" "$plugin_receipt" <<'PY'
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

instance = Path(sys.argv[1])
receipt = json.loads(sys.argv[2])
assert receipt["plugin_id"] == "test-server-plugin"
assert receipt["friendly_name"] == "Test Server Plugin"
assert len(receipt["tree_sha256"]) == 64
assert receipt["invalidated_dev_caches"] == ["test-server-plugin-stale"]
assert (instance / "Plugins/test-server-plugin/Plugin.cs").is_file()
assert not (instance / "MagnetarConfig/DevFolder/test-server-plugin-stale").exists()
sources = ET.parse(instance / "MagnetarConfig/Sources/sources.xml").getroot()
node = next(x for x in sources.find("LocalPluginSources") if x.findtext("Name") == "test-server-plugin")
assert node.findtext("File") == "TestPlugin.xml"
profile = ET.parse(instance / "MagnetarConfig/Profiles/Current.xml").getroot()
assert any(x.findtext("Id") == "test-server-plugin" for x in profile.find("DevFolder"))
PY
python3 - "$worker_root/instances/imported" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

instance = Path(sys.argv[1])
path = instance / "MagnetarConfig/Sources/sources.xml"
tree = ET.parse(path)
sources = tree.getroot().find("LocalPluginSources")
alias = ET.SubElement(sources, "LocalPlugin")
for key, value in (
    ("Name", "stale-alias"),
    ("Folder", str(instance / "Plugins/test-server-plugin")),
    ("File", "TestPlugin.xml"),
    ("Enabled", "true"),
):
    ET.SubElement(alias, key).text = value
tree.write(path)
PY
if "$repo_root/worker/register-magnetar-plugin.py" \
    "$worker_root/instances/imported" test-server-plugin "$plugin_source" --replace >/dev/null 2>&1; then
  echo "Magnetar plugin registrar accepted a duplicate manifest identity" >&2
  exit 1
fi
python3 - "$worker_root/instances/imported" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

path = Path(sys.argv[1]) / "MagnetarConfig/Sources/sources.xml"
tree = ET.parse(path)
sources = tree.getroot().find("LocalPluginSources")
for node in list(sources):
    if node.findtext("Name") == "stale-alias":
        sources.remove(node)
tree.write(path)
PY
if "$repo_root/worker/register-magnetar-plugin.py" \
    "$worker_root/instances/imported" test-server-plugin "$plugin_source" >/dev/null 2>&1; then
  echo "Magnetar plugin registrar replaced a staged plugin without --replace" >&2
  exit 1
fi
ln -s Plugin.cs "$plugin_source/unsafe-link"
if "$repo_root/worker/register-magnetar-plugin.py" \
    "$worker_root/instances/imported" unsafe-plugin "$plugin_source" >/dev/null 2>&1; then
  echo "Magnetar plugin registrar accepted a symlink" >&2
  exit 1
fi
rm "$plugin_source/unsafe-link"

sed -i 's/<Id>test-server-plugin<\//<Id>different-plugin<\//' "$plugin_source/TestPlugin.xml"
if "$repo_root/worker/register-magnetar-plugin.py" \
    "$worker_root/instances/imported" mismatched-plugin "$plugin_source" >/dev/null 2>&1; then
  echo "Magnetar plugin registrar accepted a plugin id that differs from its manifest" >&2
  exit 1
fi
sed -i 's/<Id>different-plugin<\//<Id>test-server-plugin<\//' "$plugin_source/TestPlugin.xml"

printf '%s\n' \
  "2026 [INFO] Loading client mod scripts for $workshop_id" \
  "2026 [INFO] Loading client mod definitions for $workshop_id" \
  '2026 [INFO] TestMod bootstrap complete' \
  '2026 [INFO] Test plugin ready' \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 status=accepted" \
  '2026 [INFO] Voidwright evidence: type=depth-controller operation=ffffffffffffffffffffffffffffffff controllerId=12345 status=preflight-blocked elapsedTicks=40 actuatorDispatch=none reason=stale_activation' \
  > "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
printf '%s\n' \
  '2026 - Thread: 1 -> App Version: 01_210_014' \
  '2026 - Thread: 1 -> Session loaded' \
  '2026 - Thread: 1 -> Game ready...' \
  > "$worker_root/instances/imported/SpaceEngineersDedicated_20261003_000000000.log"
if incomplete_lab_receipt="$($repo_root/worker/magnetar-receipt.py "$worker_root/instances/imported")"; then
  echo "Magnetar receipt passed before the lab operation completed" >&2
  exit 1
fi
python3 - "$incomplete_lab_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_operation_status"] == "accepted"
assert receipt["lab_operation_terminal"] is False
assert not any("ffffffffffffffffffffffffffffffff" in event for event in receipt["lab_operation_events"])
PY
printf '%s\n' \
  '2026 - Thread: 1 -> Saving world - END' \
  '2026 - Thread: 1 -> Steam closed' \
  '2026 - Thread: 1 -> Log Closed' \
  >> "$worker_root/instances/imported/SpaceEngineersDedicated_20261003_000000000.log"
stopped_incomplete_receipt="$($repo_root/worker/magnetar-receipt.py "$worker_root/instances/imported" || true)"
python3 - "$stopped_incomplete_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_operation_event_status"] == "accepted"
assert receipt["lab_operation_status"] == "fixture-stopped-before-terminal"
assert receipt["lab_operation_terminal"] is True
assert receipt["lab_operation_terminal_source"] == "worker-confirmed-clean-shutdown"
assert receipt["passed"] is False
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 status=complete elapsedTicks=1200 displacement=0 speed=0" \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 status=timeout elapsedTicks=1201 displacement=0 speed=0 reason=late_deadline" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
sticky_success_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported")"
python3 - "$sticky_success_receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_status"] == "complete"
assert receipt["lab_operation_terminal"] is True
assert not any("late_deadline" in event for event in receipt["lab_operation_events"])
PY
preflight_active_path="$worker_root/instances/imported/.vragecage-active-run.json"
python3 - "$preflight_active_path" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
active = json.loads(path.read_text())
active["command"]["action"] = "preflight-only"
path.write_text(json.dumps(active, sort_keys=True, separators=(",", ":")) + "\n")
PY
preflight_saved_log="$temporary/preflight-info-log"
cp "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log" "$preflight_saved_log"
sed '/type=preflight-only .*status=complete/d' "$preflight_saved_log" > "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=preflight-only operation=$operation_id controllerId=12345 cameraId=67890 status=timeout elapsedTicks=3600 actuatorDispatch=none reason=preflight_deadline_expired" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
preflight_timeout_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" || true)"
python3 - "$preflight_timeout_receipt" "$operation_id" <<'PY'
import json
import sys
receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_operation_status"] == "timeout"
assert receipt["lab_operation_terminal"] is True
assert receipt["passed"] is False
PY
cp "$preflight_saved_log" "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=preflight-only operation=$operation_id controllerId=12345 cameraId=67890 status=complete elapsedTicks=9 actuatorDispatch=none reason=preflight-only_complete" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=timeout elapsedTicks=3600 actuatorDispatch=none reason=preflight_deadline_expired" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
preflight_only_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported")"
python3 - "$preflight_only_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_command_action"] == "preflight-only"
assert receipt["lab_operation_status"] == "complete"
assert receipt["lab_operation_terminal"] is True
assert receipt["passed"] is True
assert any("type=preflight-only" in event and "actuatorDispatch=none" in event
           for event in receipt["lab_operation_events"])
PY
cp "$preflight_saved_log" "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
python3 - "$preflight_active_path" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
active = json.loads(path.read_text())
active["command"]["action"] = "drive-distance"
path.write_text(json.dumps(active, sort_keys=True, separators=(",", ":")) + "\n")
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=progress elapsedTicks=1201 position=10,20,30 distanceToTargetMeters=4 speedMps=0 actuatorDispatch=none" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
overdue_progress_receipt="$($repo_root/worker/magnetar-receipt.py "$worker_root/instances/imported" || true)"
python3 - "$overdue_progress_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_operation_event_status"] == "progress"
assert receipt["lab_operation_status"] == "fixture-stopped-before-terminal"
assert receipt["lab_operation_terminal"] is True
assert receipt["lab_operation_terminal_source"] == "worker-confirmed-clean-shutdown"
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=timeout elapsedTicks=1202 position=10,20,30 distanceToTargetMeters=4 speedMps=0 actuatorDispatch=none reason=run_tick_limit_reached;_actuator_stop_threw_InvalidOperationException" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
timeout_receipt="$($repo_root/worker/magnetar-receipt.py "$worker_root/instances/imported" || true)"
python3 - "$timeout_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is False
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_operation_status"] == "timeout"
assert receipt["lab_operation_terminal"] is True
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=progress elapsedTicks=1203 position=10,20,30 distanceToTargetMeters=4 speedMps=0 actuatorDispatch=none" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
late_progress_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" || true)"
python3 - "$late_progress_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_operation_status"] == "timeout"
assert receipt["lab_operation_terminal"] is True
assert not any("elapsedTicks=1203" in event for event in receipt["lab_operation_events"])
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=preflight-blocked elapsedTicks=1204 position=10,20,30 distanceToTargetMeters=4 speedMps=0 actuatorDispatch=none reason=mod_preflight_denied" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
preflight_blocked_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" || true)"
python3 - "$preflight_blocked_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is False
assert receipt["lab_operation_id"] == sys.argv[2]
assert receipt["lab_operation_status"] == "preflight-blocked"
assert receipt["lab_operation_terminal"] is True
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=complete elapsedTicks=1205 position=10,20,30 distanceToTargetMeters=0 speedMps=0 actuatorDispatch=none reason=late_false_success" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
failed_terminal_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" || true)"
python3 - "$failed_terminal_receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_status"] == "preflight-blocked"
assert receipt["lab_operation_terminal"] is True
assert not any("late_false_success" in event for event in receipt["lab_operation_events"])
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=progress elapsedTicks=1205 position=10,20,30 distanceToTargetMeters=4 speedMps=0 actuatorDispatch=none" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
blocked_late_progress_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" || true)"
python3 - "$blocked_late_progress_receipt" "$operation_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_status"] == "preflight-blocked"
assert receipt["lab_operation_terminal"] is True
assert not any("elapsedTicks=1205" in event for event in receipt["lab_operation_events"])
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 status=complete elapsedTicks=90 displacement=1 speed=0" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
lab_receipt="$($repo_root/worker/magnetar-receipt.py "$worker_root/instances/imported" || true)"
python3 - "$lab_receipt" "$run_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is False
assert receipt["lab_contract_valid"] is True
assert receipt["lab_run_id"] == sys.argv[2]
assert receipt["lab_operation_status"] == "preflight-blocked"
assert receipt["lab_operation_terminal"] is True
PY
python3 - "$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" <<'PY'
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

active_path = Path(sys.argv[2]) / ".vragecage-active-run.json"
original = active_path.read_bytes()
try:
    for started_at in ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), None, "invalid"):
        active = json.loads(original)
        active["started_at"] = started_at
        active_path.write_text(json.dumps(active))
        result = subprocess.run([sys.argv[1], sys.argv[2]], text=True, capture_output=True)
        receipt = json.loads(result.stdout)
        assert result.returncode == 1
        assert receipt["passed"] is False
        assert receipt["game_ready"] is False
        assert receipt["session_loaded"] is False
        assert receipt["clean_shutdown"] is False
        assert receipt["lab_operation_terminal"] is False
        assert receipt["lab_operation_events"] == []
        assert receipt["logs_updated_since_activation"] == {"game": False, "magnetar": False}
finally:
    active_path.write_bytes(original)
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 status=attitude-limit" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
if attitude_receipt="$($repo_root/worker/magnetar-receipt.py "$worker_root/instances/imported")"; then
  echo "Magnetar receipt passed after the attitude limit stopped guidance" >&2
  exit 1
fi
python3 - "$attitude_receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is False
assert receipt["lab_operation_status"] == "attitude-limit"
assert receipt["lab_operation_terminal"] is True
PY
sed -i '$d' "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 action=drive-to-coordinate-depth status=depth-overspeed elapsedTicks=3" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
depth_overspeed_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" || true)"
python3 - "$depth_overspeed_receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is False
assert receipt["lab_operation_status"] == "depth-overspeed"
assert receipt["lab_operation_terminal"] is True
PY
sed -i '$d' "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=progress elapsedTicks=60 position=10,20,30 distanceToTargetMeters=4 speedMps=0.2" \
  "2026 [INFO] Voidwright evidence: type=depth-controller operation=$operation_id controllerId=12345 cameraId=87654 status=complete elapsedTicks=90 position=10,20,32 distanceToTargetMeters=1.5 speedMps=0" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
depth_controller_receipt="$("$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" || true)"
python3 - "$depth_controller_receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["lab_operation_status"] == "preflight-blocked"
assert receipt["lab_operation_terminal"] is True
assert not any("elapsedTicks=90 position=10,20,32" in event for event in receipt["lab_operation_events"])
PY
sed -i '$d' "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
sensor_instance="$worker_root/instances/sensor-receipt"
cp -a "$worker_root/instances/imported" "$sensor_instance"
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import struct
import sys
from pathlib import Path

instance = Path(sys.argv[1])
operation = "a" * 32
run_id = "b" * 32
for name in (".vragecage-mod-stage.json", ".vragecage-plugin-stage.json", ".vragecage-fixture-edits.json"):
    (instance / name).unlink(missing_ok=True)
imported_path = instance / ".vragecage-world-import.json"
imported = json.loads(imported_path.read_text())
imported["instance"] = instance.name
imported_path.write_text(json.dumps(imported, sort_keys=True, separators=(",", ":")) + "\n")
lab_path = instance / ".vragecage-lab.json"
lab = json.loads(lab_path.read_text())
lab["instance"] = instance.name
lab_path.write_text(json.dumps(lab, sort_keys=True, separators=(",", ":")) + "\n")
quarantine = instance / "Quarantine" / run_id
quarantine.mkdir(parents=True, exist_ok=True)
depth = b"P5\n4 4\n65535\n" + b"\x00\x01" * 16
depth_path = quarantine / f"sensor-{operation}.pgm"
depth_path.write_bytes(depth)
depth_hash = hashlib.sha256(depth).hexdigest()
entity_ids = [67890, 67890] + list(range(70001, 70015)
)
entity_id_data = b"".join(struct.pack("<q", value) for value in entity_ids)
entity_id_path = quarantine / f"sensor-{operation}.i64le"
entity_id_path.write_bytes(entity_id_data)
entity_id_hash = hashlib.sha256(entity_id_data).hexdigest()
normal_data = b"".join(struct.pack("<hhh", 0, 0, 32767) for _ in range(16))
normal_path = quarantine / f"sensor-{operation}.n16le"
normal_path.write_bytes(normal_data)
normal_hash = hashlib.sha256(normal_data).hexdigest()
metadata = {
    "schema": "vragecage.sensor-frame.v1", "operation_id": operation,
    "kind": "depth", "camera_id": 67890, "grid_id": 67890,
    "width": 4, "height": 4, "hit_pixels": 16, "depth_sha256": depth_hash,
    "entity_id_path": f"sensor-{operation}.i64le",
    "entity_id_encoding": "i64le-zero-is-no-entity",
    "entity_id_sha256": entity_id_hash,
    "known_entity_hits": 16, "self_grid_hits": 2,
    "normal_path": f"sensor-{operation}.n16le",
    "normal_encoding": "xyz-i16le-unit32767-zero-is-invalid-or-miss",
    "normal_sha256": normal_hash, "valid_normal_hits": 16, "invalid_normal_hits": 0,
    "natural_gravity_world": [-8.403, -0.978, 4.967],
    "natural_up_world": [0.857, 0.100, -0.506],
    "natural_gravity_magnitude": 9.81, "natural_gravity_interference": 1.0,
    "entity_summaries": [
        {"entity_id": 67890, "hit_count": 2, "display_name": "own-grid", "runtime_type": "MyCubeGrid"},
        *[{"entity_id": value, "hit_count": 1, "display_name": "fixture object", "runtime_type": "MyCubeGrid"}
          for value in range(70001, 70015)],
    ],
}
metadata_path = quarantine / f"sensor-{operation}.json"
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
metadata_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
active = {
    "schema": "vragecage.lab-run.v1", "authority": "destructive-lab",
    "started_at": "2020-01-01T00:00:00+00:00",
    "instance": instance.name, "fixture_id": lab["fixture_id"], "run_id": run_id,
    "quarantine": str(quarantine),
    "contract_sha256": hashlib.sha256(lab_path.read_bytes()).hexdigest(),
    "command": {"action": "sensor-frame", "operation_id": operation},
}
(instance / ".vragecage-active-run.json").write_text(
    json.dumps(active, sort_keys=True, separators=(",", ":")) + "\n"
)
(instance / "MagnetarConfig/info_sensor.log").write_text(
    "2026 [INFO] Voidwright evidence: type=sensor-frame operation=" + operation +
    " cameraId=67890 status=complete width=4 height=4 hits=16 depthSha256=" + depth_hash +
    " entityIdsSha256=" + entity_id_hash +
    " normalSha256=" + normal_hash +
    " metadataSha256=" + metadata_hash + "\n"
)
(instance / "SpaceEngineersDedicated_20261003_999999999.log").write_text(
    "App Version: 01_210_014\nSession loaded\nGame ready...\n"
)
PY
sensor_receipt="$($repo_root/worker/magnetar-receipt.py "$sensor_instance")"
python3 - "$sensor_receipt" <<'PY'
import json
import sys
receipt = json.loads(sys.argv[1])
assert receipt["passed"] is True
assert receipt["sensor_artifact_valid"] is True
assert receipt["sensor_artifact"]["metadata"]["width"] == 4
assert receipt["sensor_artifact"]["entity_ids_valid"] is True
assert receipt["sensor_artifact"]["entity_summaries_valid"] is True
assert receipt["sensor_artifact"]["depth_map_valid"] is True
assert receipt["sensor_artifact"]["surface_normals_valid"] is True
assert receipt["sensor_artifact"]["gravity_reference_valid"] is True
assert receipt["sensor_artifact"]["metadata"]["self_grid_hits"] == 2
PY
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
operation = "a" * 32
metadata_path = root / "Quarantine" / ("b" * 32) / f"sensor-{operation}.json"
metadata = json.loads(metadata_path.read_text())
metadata["natural_up_world"] = [0, 1, 0]
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
new_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path = root / "MagnetarConfig/info_sensor.log"
log = log_path.read_text()
old_hash = log.rsplit("metadataSha256=", 1)[1].strip()
log_path.write_text(log.replace(old_hash, new_hash))
PY
if "$repo_root/worker/magnetar-receipt.py" "$sensor_instance" >/dev/null 2>&1; then
  echo "Magnetar receipt accepted an inconsistent local-gravity reference" >&2
  exit 1
fi
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
operation = "a" * 32
metadata_path = root / "Quarantine" / ("b" * 32) / f"sensor-{operation}.json"
metadata = json.loads(metadata_path.read_text())
metadata["natural_up_world"] = [0.857, 0.100, -0.506]
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
new_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path = root / "MagnetarConfig/info_sensor.log"
log = log_path.read_text()
old_hash = log.rsplit("metadataSha256=", 1)[1].strip()
log_path.write_text(log.replace(old_hash, new_hash))
PY
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
operation = "a" * 32
quarantine = root / "Quarantine" / ("b" * 32)
normal_path = quarantine / f"sensor-{operation}.n16le"
normal_data = bytearray(normal_path.read_bytes())
old_normal_hash = hashlib.sha256(normal_data).hexdigest()
normal_data[:6] = b"\x00" * 6
normal_path.write_bytes(normal_data)
new_normal_hash = hashlib.sha256(normal_data).hexdigest()
metadata_path = quarantine / f"sensor-{operation}.json"
metadata = json.loads(metadata_path.read_text())
metadata["normal_sha256"] = new_normal_hash
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
new_metadata_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path = root / "MagnetarConfig/info_sensor.log"
log = log_path.read_text()
old_metadata_hash = log.rsplit("metadataSha256=", 1)[1].strip()
log = log.replace("normalSha256=" + old_normal_hash, "normalSha256=" + new_normal_hash)
log_path.write_text(log.replace(old_metadata_hash, new_metadata_hash))
PY
if "$repo_root/worker/magnetar-receipt.py" "$sensor_instance" >/dev/null 2>&1; then
  echo "Magnetar receipt accepted a surface-normal raster misaligned with depth hits" >&2
  exit 1
fi
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
operation = "a" * 32
metadata_path = root / "Quarantine" / ("b" * 32) / f"sensor-{operation}.json"
metadata = json.loads(metadata_path.read_text())
metadata["hit_pixels"] = 15
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
bad_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path = root / "MagnetarConfig/info_sensor.log"
log = log_path.read_text()
old_hash = log.rsplit("metadataSha256=", 1)[1].strip()
log_path.write_text(log.replace(old_hash, bad_hash))
PY
if "$repo_root/worker/magnetar-receipt.py" "$sensor_instance" >/dev/null 2>&1; then
  echo "Magnetar receipt accepted a depth hit count that did not match the PGM raster" >&2
  exit 1
fi
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
operation = "a" * 32
metadata_path = root / "Quarantine" / ("b" * 32) / f"sensor-{operation}.json"
metadata = json.loads(metadata_path.read_text())
metadata["hit_pixels"] = 16
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
good_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path = root / "MagnetarConfig/info_sensor.log"
log = log_path.read_text()
old_hash = log.rsplit("metadataSha256=", 1)[1].strip()
log_path.write_text(log.replace(old_hash, good_hash))
PY
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
operation = "a" * 32
metadata_path = root / "Quarantine" / ("b" * 32) / f"sensor-{operation}.json"
metadata = json.loads(metadata_path.read_text())
metadata.pop("entity_summaries")
for field in ("normal_path", "normal_encoding", "normal_sha256", "valid_normal_hits", "invalid_normal_hits"):
    metadata.pop(field, None)
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
new_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path = root / "MagnetarConfig/info_sensor.log"
log = log_path.read_text()
old_hash = log.rsplit("metadataSha256=", 1)[1].strip()
log_path.write_text(log.replace(old_hash, new_hash))
PY
legacy_sensor_receipt="$($repo_root/worker/magnetar-receipt.py "$sensor_instance")"
python3 - "$legacy_sensor_receipt" <<'PY'
import json
import sys
receipt = json.loads(sys.argv[1])
assert receipt["passed"] is True
assert receipt["sensor_artifact_valid"] is True
assert receipt["sensor_artifact"]["entity_ids_valid"] is True
assert receipt["sensor_artifact"]["entity_summaries_valid"] is None
PY
python3 - "$sensor_instance" <<'PY'
import hashlib
import json
import pathlib
sysroot = pathlib.Path(__import__("sys").argv[1])
operation = "a" * 32
quarantine = sysroot / "Quarantine" / ("b" * 32)
metadata_path = quarantine / f"sensor-{operation}.json"
metadata = json.loads(metadata_path.read_text())
metadata["entity_summaries"] = [
    {"entity_id": 67890, "hit_count": 2, "display_name": "own-grid", "runtime_type": "MyCubeGrid"},
    *[{"entity_id": value, "hit_count": 1, "display_name": "fixture object", "runtime_type": "MyCubeGrid"}
      for value in range(70001, 70015)],
]
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
good_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path = sysroot / "MagnetarConfig/info_sensor.log"
log = log_path.read_text()
old_hash = log.rsplit("metadataSha256=", 1)[1].strip()
log_path.write_text(log.replace(old_hash, good_hash))
metadata["entity_summaries"][0]["hit_count"] = 3
metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
bad_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
log_path.write_text(log_path.read_text().replace(good_hash, bad_hash))
PY
if "$repo_root/worker/magnetar-receipt.py" "$sensor_instance" >/dev/null 2>&1; then
  echo "Magnetar receipt accepted entity summaries inconsistent with the per-pixel ID raster" >&2
  exit 1
fi
printf 'tamper' >> "$sensor_instance/Quarantine/$(printf 'b%.0s' {1..32})/sensor-$(printf 'a%.0s' {1..32}).pgm"
if "$repo_root/worker/magnetar-receipt.py" "$sensor_instance" >/dev/null 2>&1; then
  echo "Magnetar receipt accepted a tampered sensor artifact" >&2
  exit 1
fi
printf '\x01' | dd of="$sensor_instance/Quarantine/$(printf 'b%.0s' {1..32})/sensor-$(printf 'a%.0s' {1..32}).i64le" bs=1 seek=0 conv=notrunc status=none
if "$repo_root/worker/magnetar-receipt.py" "$sensor_instance" >/dev/null 2>&1; then
  echo "Magnetar receipt accepted a tampered sensor entity-ID map" >&2
  exit 1
fi
mv "$worker_root/instances/imported/Quarantine/$run_id" \
  "$worker_root/instances/imported/Quarantine/run-backup"
ln -s run-backup "$worker_root/instances/imported/Quarantine/$run_id"
if "$repo_root/worker/magnetar-receipt.py" "$worker_root/instances/imported" >/dev/null 2>&1; then
  echo "Magnetar receipt accepted a symlinked lab quarantine" >&2
  exit 1
fi
rm "$worker_root/instances/imported/Quarantine/$run_id"
mv "$worker_root/instances/imported/Quarantine/run-backup" \
  "$worker_root/instances/imported/Quarantine/$run_id"

mkdir -p "$temporary/fake-bin"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$temporary/fake-bin/systemctl"
chmod +x "$temporary/fake-bin/systemctl"
if PATH="$temporary/fake-bin:$PATH" VRAGECAGE_WORKER_ROOT="$worker_root" \
    "$repo_root/bin/vragecage" _worker register-mod magnetar TestMod "$temporary/mod" \
      --replace >/dev/null 2>"$temporary/active-stage-error"; then
  echo "CLI staged a mod while the instance service was active" >&2
  exit 1
fi
grep -Fq 'refusing to stage a mod while instance is running: magnetar' \
  "$temporary/active-stage-error"
if "$repo_root/worker/register-magnetar-mod.py" "$magnetar_instance" TestMod "$temporary/mod" >/dev/null 2>&1; then
  echo "Magnetar registrar replaced a staged mod without its explicit gate" >&2
  exit 1
fi

cp "$magnetar_instance/MagnetarConfig/Profiles/Current.xml" "$temporary/profile.xml"
sources_before="$(sha256sum "$magnetar_instance/MagnetarConfig/Sources/sources.xml" | cut -d' ' -f1)"
mkdir -p "$temporary/mod-new/Data"
printf '<Definitions changed="true" />\n' > "$temporary/mod-new/Data/Test.sbc"
rm "$magnetar_instance/MagnetarConfig/Profiles/Current.xml"
mkdir "$magnetar_instance/MagnetarConfig/Profiles/Current.xml"
if "$repo_root/worker/register-magnetar-mod.py" "$magnetar_instance" TestMod "$temporary/mod-new" --replace >/dev/null 2>&1; then
  echo "Magnetar registrar did not fail at the injected config fault" >&2
  exit 1
fi
grep -Fxq '<Definitions />' "$worker_root/magnetar-steam/steamapps/workshop/content/244850/$workshop_id/Data/Test.sbc"
test "$(sha256sum "$magnetar_instance/MagnetarConfig/Sources/sources.xml" | cut -d' ' -f1)" = "$sources_before"
rmdir "$magnetar_instance/MagnetarConfig/Profiles/Current.xml"
mv "$temporary/profile.xml" "$magnetar_instance/MagnetarConfig/Profiles/Current.xml"

mkdir -p "$magnetar_instance/MagnetarConfig"
printf '%s\n' \
  "2026 [INFO] Loading client mod scripts for $workshop_id" \
  "2026 [INFO] Loading client mod definitions for $workshop_id" \
  '2026 [INFO] TestMod bootstrap complete' \
  '2026 [INFO] TestMod observation: vehicle=rover state=idle' \
  > "$magnetar_instance/MagnetarConfig/info_20261003_000000.log"
printf '%s\n' \
  '2026 - Thread: 1 -> App Version: 01_210_014' \
  '2026 - Thread: 1 -> Session loaded' \
  '2026 - Thread: 1 -> Game ready...' \
  '2026 - Thread: 1 -> MOD_ERROR: Unrelated Fixture Mod' \
  > "$magnetar_instance/SpaceEngineersDedicated_20261003_000000000.log"
magnetar_receipt="$($repo_root/worker/magnetar-receipt.py "$magnetar_instance")"
python3 - "$magnetar_receipt" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is True
assert receipt["definitions_loaded"] is True
assert receipt["scripts_loaded"] is True
assert receipt["staged_mod_unchanged"] is True
assert receipt["expected_log_markers"] == {"TestMod bootstrap complete": True}
assert receipt["observations"] == ["vehicle=rover state=idle"]
assert receipt["observations_omitted"] == 0
assert receipt["target_fatal_markers"] == []
assert len(receipt["environment_warnings"]) == 1
assert receipt["clean_shutdown"] is False
PY

printf '%s\n' '2026 [ERROR] Failed to build TestMod' >> \
  "$magnetar_instance/MagnetarConfig/info_20261003_000000.log"
if target_failure="$($repo_root/worker/magnetar-receipt.py "$magnetar_instance")"; then
  echo "target mod error produced a passing Magnetar receipt" >&2
  exit 1
fi
python3 - "$target_failure" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is False
assert len(receipt["target_fatal_markers"]) == 1
PY

mkdir -p \
  "$worker_root/dotnet" \
  "$worker_root/magnetar/current" \
  "$worker_root/magnetar-steam/steamapps/common" \
  "$worker_root/server/DedicatedServer64"
touch "$worker_root/dotnet/dotnet"
touch "$worker_root/server/DedicatedServer64/SpaceEngineersDedicated.exe"
chmod +x "$worker_root/dotnet/dotnet"
ln -s "$worker_root/server" \
  "$worker_root/magnetar-steam/steamapps/common/SpaceEngineersDedicatedServer"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "%s\\n" "$@" > "$VRAGECAGE_WORKER_ROOT/magnetar-args"' \
  > "$worker_root/magnetar/current/MagnetarInterim.bin"
chmod +x "$worker_root/magnetar/current/MagnetarInterim.bin"
VRAGECAGE_WORKER_ROOT="$worker_root" VRAGECAGE_ALLOW_NON_LOOPBACK=1 \
  "$repo_root/worker/run-magnetar.sh" magnetar
grep -Fxq "$worker_root/magnetar-steam/steamapps/common/SpaceEngineersDedicatedServer/DedicatedServer64" \
  "$worker_root/magnetar-args"

VRAGECAGE_WORKER_ROOT="$worker_root" VRAGECAGE_ALLOW_NON_LOOPBACK=1 \
VRAGECAGE_LAB_AUTHORITY=destructive-lab VRAGECAGE_LAB_RUN_ID="$run_id" \
VRAGECAGE_LAB_MANIFEST="$worker_root/instances/imported/.vragecage-lab.json" \
VRAGECAGE_QUARANTINE="$worker_root/instances/imported/Quarantine/$run_id" \
  "$repo_root/worker/run-magnetar.sh" imported

python3 "$repo_root/worker/tests/test-passive-telemetry-contract.py"
printf 'worker script tests passed\n'
