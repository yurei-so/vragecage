#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "worker" / "register-magnetar-plugin.py"
CONTRACT = ROOT / "worker" / "passive-telemetry-contract.py"
plugin_id = "voidwright-passive-telemetry"


with tempfile.TemporaryDirectory(prefix="vragecage-passive-stage-") as temporary:
    base = Path(temporary)
    worker = base / "worker"
    instance_name = "read-only-clone"
    instance = worker / "instances" / instance_name
    source = worker / "incoming" / "passive-plugin"
    (instance / "MagnetarConfig" / "Sources").mkdir(parents=True)
    (instance / "MagnetarConfig" / "Profiles").mkdir(parents=True)
    source.mkdir(parents=True)
    (instance / ".vragecage-engine").write_text("magnetar\n")
    (instance / "MagnetarConfig" / "Sources" / "sources.xml").write_text(
        "<Sources><LocalPluginSources /></Sources>")
    (instance / "MagnetarConfig" / "Profiles" / "Current.xml").write_text(
        "<Profile><DevFolder /></Profile>")
    (source / "Voidwright.PassiveTelemetryPlugin.xml").write_text(
        f"<PluginData><Id>{plugin_id}</Id><FriendlyName>Read-only</FriendlyName></PluginData>")
    (source / "Voidwright.PassiveTelemetryPlugin.dll").write_bytes(b"offline test assembly")
    (source / "vragecage.passive-telemetry-plugin.json").write_text(json.dumps({
        "schema": "vragecage.passive-telemetry-plugin.v1",
        "permissionScope": "read_only_observation",
        "pluginId": plugin_id,
        "entryType": "Voidwright.ServerPlugin.DrivingLabPassiveTelemetryPlugin",
        "capabilities": ["player-consented-passive-grid-telemetry"],
        "deniedCapabilities": ["movement", "actuator-control",
                               "programmable-block-script-control", "world-entity-mutation"],
    }))

    fake_bin = base / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 3\n")
    systemctl.chmod(0o755)
    os.environ["PATH"] = str(fake_bin) + os.pathsep + os.environ["PATH"]

    register = runpy.run_path(str(REGISTER), run_name="passive_register_test")
    sys.argv = [str(REGISTER), str(instance), plugin_id, str(source), "--passive-telemetry"]
    assert register["main"]() == 0

    contract_api = runpy.run_path(str(CONTRACT), run_name="passive_contract_test")
    marker, marker_hash = contract_api["verify"](instance)
    assert marker["authority"] == "read_only_observation"
    assert marker["instance"] == instance_name
    assert not (instance / ".vragecage-lab.json").exists()
    assert (instance / ".vragecage-plugin-stage.json").exists() is False
    assert (instance / ".vragecage-passive-telemetry-stage.json").is_file()
    sys.argv = [str(REGISTER), str(instance), "some-other-plugin", str(source)]
    try:
        register["main"]()
        raise AssertionError("read-only observer instance accepted the destructive plugin staging route")
    except SystemExit as error:
        assert "cannot stage a general lab plugin" in str(error)

    run_id = contract_api["activate"](instance)
    active = json.loads((instance / ".vragecage-passive-telemetry-active.json").read_text())
    assert len(run_id) == 32 and active["run_id"] == run_id
    assert active["contract_sha256"] == marker_hash
    assert contract_api["status"](instance)["run_id"] == run_id
    assert (instance / ".vragecage-passive-telemetry-active.json").stat().st_mode & 0o077 == 0

    (instance / "SpaceEngineers-Dedicated.cfg").write_text("<Config><IP>127.0.0.1</IP></Config>")
    worker_runtime = worker / "dotnet"
    worker_runtime.mkdir()
    (worker_runtime / "dotnet").write_text("#!/bin/sh\nexit 0\n")
    (worker_runtime / "dotnet").chmod(0o755)
    server = worker / "server" / "DedicatedServer64"
    server.mkdir(parents=True)
    (server / "SpaceEngineersDedicated.exe").write_text("fixture")
    magnetar = worker / "magnetar" / "current"
    magnetar.mkdir(parents=True)
    args_path = worker / "magnetar-args"
    launcher = magnetar / "MagnetarInterim.bin"
    launcher.write_text("#!/bin/sh\nprintf '%s\\n' \"$VRAGECAGE_PASSIVE_TELEMETRY_AUTHORITY\" \"$VRAGECAGE_PASSIVE_TELEMETRY_RUN_ID\" \"$VRAGECAGE_PASSIVE_TELEMETRY_CONTRACT_SHA256\" > \"$VRAGECAGE_WORKER_ROOT/magnetar-args\"\n")
    launcher.chmod(0o755)
    link_parent = worker / "magnetar-steam" / "steamapps" / "common"
    link_parent.mkdir(parents=True)
    (link_parent / "SpaceEngineersDedicatedServer").symlink_to(worker / "server")
    env = os.environ.copy()
    env["VRAGECAGE_WORKER_ROOT"] = str(worker)
    env["VRAGECAGE_ALLOW_NON_LOOPBACK"] = "1"
    subprocess.run([str(ROOT / "worker" / "run-magnetar.sh"), instance_name], check=True, env=env)
    runtime_values = args_path.read_text().splitlines()
    assert runtime_values[0] == "read_only_observation"
    assert len(runtime_values[1]) == 32 and runtime_values[2] == marker_hash

    package = instance / "Plugins" / plugin_id
    (package / "Voidwright.PassiveTelemetryPlugin.dll").write_bytes(b"tampered")
    try:
        contract_api["verify"](instance)
        raise AssertionError("observer contract accepted a changed plugin tree")
    except SystemExit:
        pass

print("PASS stage only the dedicated passive observer on a stopped Magnetar instance")
print("PASS bind read-only authority to the exact plugin hash without creating destructive-lab authority")
print("PASS activate a fresh run identity and reject plugin tree drift")
