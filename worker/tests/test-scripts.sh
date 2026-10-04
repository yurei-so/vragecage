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
printf '<?xml version="1.0"?><MyObjectBuilder_Checkpoint><Mods /></MyObjectBuilder_Checkpoint>\n' \
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
printf '<Checkpoint><Mods><ModItem><Name>TestMod</Name></ModItem><ModItem><Name>Published</Name><PublishedFileId>123</PublishedFileId></ModItem></Mods><Gps><dictionary><item><Key>42</Key><Value><Entries><Entry><name>Red Zone</name><description>test target</description><coords><X>1</X><Y>2</Y><Z>3</Z></coords></Entry></Entries></Value></item></dictionary></Gps></Checkpoint>\n' > "$worker_root/incoming/imported-world/Sandbox.sbc"
printf '<WorldConfiguration><Mods><ModItem><Name>TestMod</Name><PublishedFileId>0</PublishedFileId></ModItem><ModItem><Name>Published</Name><PublishedFileId>123</PublishedFileId></ModItem></Mods></WorldConfiguration>\n' > "$worker_root/incoming/imported-world/Sandbox_config.sbc"
printf '<Sector xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><SectorObjects><MyObjectBuilder_EntityBase xsi:type="MyObjectBuilder_CubeGrid"><EntityId>9001</EntityId><DisplayName>Camera Drone</DisplayName><GridSizeEnum>Small</GridSizeEnum><CubeBlocks><MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_RemoteControl"><EntityId>12345</EntityId><SubtypeName>SmallBlockRemoteControl</SubtypeName></MyObjectBuilder_CubeBlock></CubeBlocks></MyObjectBuilder_EntityBase></SectorObjects></Sector>\n' > "$worker_root/incoming/imported-world/SANDBOX_0_0_0_.sbs"
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
assert receipt["file_count"] == 3
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
command="$($repo_root/worker/lab-contract.py plan-drive \
  "$worker_root/instances/imported" 12345 --distance 2 --max-speed 1 --timeout-ticks 600)"
if "$repo_root/worker/lab-contract.py" plan-drive \
    "$worker_root/instances/imported" 12345 --distance 2 --max-speed 1 --timeout-ticks 600 \
    >/dev/null 2>&1; then
  echo "lab contract accepted a second pending command" >&2
  exit 1
fi
active="$($repo_root/worker/lab-contract.py activate "$worker_root/instances/imported")"
run_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_id"])' "$active")"
operation_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["command"]["operation_id"])' "$active")"
python3 - "$command" "$active" "$worker_root/instances/imported" <<'PY'
import json
import sys
from pathlib import Path

command = json.loads(sys.argv[1])
active = json.loads(sys.argv[2])
instance = Path(sys.argv[3])
assert active["command"] == command
assert len(active["command_sha256"]) == 64
assert not (instance / ".vragecage-pending-command.json").exists()
assert (Path(active["quarantine"]) / "command.json").is_file()
PY
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
assert len(result["edit_ledger_sha256"]) == 64
tree = ET.parse(instance / "World/SANDBOX_0_0_0_.sbs")
block = tree.getroot().find(".//MyObjectBuilder_CubeBlock")
assert block.findtext("CustomName") == "[Voidwright] Camera Drone"
ledger = json.loads((instance / ".vragecage-fixture-edits.json").read_text())
assert len(ledger["edits"]) == 1
PY
already="$($repo_root/worker/fixture-contract.py opt-in-voidwright \
  "$worker_root/instances/imported" 12345 --label 'Camera Drone')"
python3 - "$already" <<'PY'
import json
import sys
assert json.loads(sys.argv[1])["status"] == "already_opted_in"
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
imported_stage_receipt="$($repo_root/worker/register-magnetar-mod.py "$worker_root/instances/imported" TestMod "$temporary/mod" --replace)"
python3 - "$worker_root/instances/imported" "$imported_stage_receipt" <<'PY'
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

instance = Path(sys.argv[1])
receipt = json.loads(sys.argv[2])
assert receipt["removed_unpublished_world_entries"] == 2
for name in ("Sandbox.sbc", "Sandbox_config.sbc"):
    mods = ET.parse(instance / "World" / name).getroot().find("Mods")
    assert mods is not None
    entries = [(node.findtext("Name"), node.findtext("PublishedFileId")) for node in mods]
    assert ("TestMod", None) not in entries
    assert ("TestMod", "0") not in entries
    assert ("Published", "123") in entries
PY
workshop_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["synthetic_workshop_id"])' "$stage_receipt")"
python3 - "$workshop_id" <<'PY'
import sys

value = int(sys.argv[1])
assert 90_000_000_000_000_000 <= value < 90_100_000_000_000_000
PY
test -f "$worker_root/magnetar-steam/steamapps/workshop/content/244850/$workshop_id/Data/Test.sbc"
test -f "$magnetar_instance/MagnetarConfig/Sources/sources.xml"
test -f "$magnetar_instance/MagnetarConfig/Profiles/Current.xml"

plugin_source="$worker_root/incoming/test-plugin"
mkdir -p "$plugin_source"
printf '%s\n' 'public sealed class PluginSourceMarker { }' > "$plugin_source/Plugin.cs"
printf '%s\n' '<?xml version="1.0"?><PluginData><Id>ignored</Id><FriendlyName>Test Server Plugin</FriendlyName></PluginData>' \
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
assert (instance / "Plugins/test-server-plugin/Plugin.cs").is_file()
sources = ET.parse(instance / "MagnetarConfig/Sources/sources.xml").getroot()
node = next(x for x in sources.find("LocalPluginSources") if x.findtext("Name") == "test-server-plugin")
assert node.findtext("File") == "TestPlugin.xml"
profile = ET.parse(instance / "MagnetarConfig/Profiles/Current.xml").getroot()
assert any(x.findtext("Id") == "test-server-plugin" for x in profile.find("DevFolder"))
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

printf '%s\n' \
  "2026 [INFO] Loading client mod scripts for $workshop_id" \
  "2026 [INFO] Loading client mod definitions for $workshop_id" \
  '2026 [INFO] TestMod bootstrap complete' \
  '2026 [INFO] Test plugin ready' \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 status=accepted" \
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
PY
printf '%s\n' \
  "2026 [INFO] Voidwright evidence: type=server-control operation=$operation_id controllerId=12345 status=complete elapsedTicks=90 displacement=1 speed=0" \
  >> "$worker_root/instances/imported/MagnetarConfig/info_20261003_000000.log"
lab_receipt="$($repo_root/worker/magnetar-receipt.py "$worker_root/instances/imported")"
python3 - "$lab_receipt" "$run_id" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
assert receipt["passed"] is True
assert receipt["lab_contract_valid"] is True
assert receipt["lab_run_id"] == sys.argv[2]
assert receipt["lab_operation_status"] == "complete"
assert receipt["lab_operation_terminal"] is True
PY
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

printf 'worker script tests passed\n'
