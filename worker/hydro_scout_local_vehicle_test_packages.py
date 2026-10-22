#!/usr/bin/env python3
"""Fail-closed package/readiness checks for the one read-only Hydro Scout run."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

INSTANCE = "land-local-test-hydro-scout-20261022-c"
SOURCE_TREE_SHA256 = "33400d8eb2a43e555856a3d48c035a784c6fa8f5453d524782372c28df9eeff4"
SOURCE_SECTOR_SHA256 = "2c2cbc70fc474b5d6868645f901d76d9bf77abc4dd2dae95e7d3c7ce725bd228"
WORLD_ID = "a92efd86-1448-5d5d-aa9a-83c6623b9f88"
SERVER_PLUGIN_TREE_SHA256 = "9043f0c6186014561633414b146999a6887704435935d2829bab5354136adc64"
DOTNET_COMPAT_TREE_SHA256 = "d5e27cf5b4681c09acd4a78637a6ff2dfb989084d023679c5756a2a3c1473557"
LINUX_COMPAT_TREE_SHA256 = "437dd4badf02edecfc0072567a73e0bc4cf84670d5476c185c5e36c5803a6ea7"
MAGNETAR_HUB_METADATA_SHA256 = "7ebded674a537d92a73715f18bd361070e286afb8c235bb233d8fc10e1f86a89"
MAGNETAR_SOURCES_CONFIG_SHA256 = "3caee4c785202cc90861fc022da615ae8c35f9fb47e8f5f80bc76aba1ae64f79"
GRID_IDS = {143344737461122310, 113504086011869676, 83781654658335329,
            101822873690952882, 90663942980601709, 143998760294003094}
GRID_ID = 113504086011869676
CONTROLLER_ID = 112711005364517351
CAMERA_ID = 6902088205773520808
WHEEL_IDS = [135633742662929758, 92672912164488213,
             129575574301717563, 72699375256551291]
WHEEL_GRID_IDS = [83781654658335329, 101822873690952882,
                  90663942980601709, 143998760294003094]
GRAVITY_ID = 86965797009458869


def parse_json(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe receipt: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"receipt must be an object: {path.name}")
    return value


def registrar_tree_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"missing or unsafe package directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"symlink in package: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def import_tree_sha256(root: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    count = size = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"symlink in imported world: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        count += 1
        size += len(content)
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content)
    return count, size, digest.hexdigest()


def verify_config(instance: Path) -> tuple[dict[str, object], str]:
    path = instance / ".vragecage-local-vehicle-test.json"
    config = parse_json(path)
    required = {
        "schema", "instance", "grid_id", "controller_id", "source_tree_sha256", "mode",
        "propulsion_enabled", "isolated_clear", "max_speed_meters_per_second",
        "max_displacement_meters", "command_timeout_seconds", "host_watchdog_seconds",
        "camera_id", "wheel_ids", "wheel_grid_ids",
    }
    if set(config) != required:
        raise ValueError("Hydro Scout config fields differ from the exact read-only schema")
    if (config.get("schema") != "vragecage.local-vehicle-test.v1" or
            config.get("instance") != INSTANCE or config.get("grid_id") != GRID_ID or
            config.get("controller_id") != CONTROLLER_ID or
            config.get("source_tree_sha256") != SOURCE_TREE_SHA256 or
            config.get("mode") != "read-only-baseline" or
            config.get("propulsion_enabled") is not False or config.get("isolated_clear") is not False or
            config.get("camera_id") != CAMERA_ID or config.get("wheel_ids") != WHEEL_IDS or
            config.get("wheel_grid_ids") != WHEEL_GRID_IDS):
        raise ValueError("Hydro Scout config is not bound to the exact read-only fixture")
    speed = config.get("max_speed_meters_per_second")
    displacement = config.get("max_displacement_meters")
    if (type(speed) not in (int, float) or not math.isfinite(speed) or not 0 < speed <= 0.05 or
            type(displacement) not in (int, float) or not math.isfinite(displacement) or
            not 0 < displacement <= 0.5 or config.get("command_timeout_seconds") != 1 or
            config.get("host_watchdog_seconds") != 60):
        raise ValueError("Hydro Scout config exceeds the read-only run's strict caps")
    return config, hashlib.sha256(path.read_bytes()).hexdigest()


def verify_serialized_fixture(instance: Path) -> None:
    checkpoint_path = instance / "World" / "Sandbox.sbc"
    if checkpoint_path.is_symlink() or not checkpoint_path.is_file():
        raise ValueError("Hydro Scout checkpoint is missing or unsafe")
    try:
        checkpoint = ET.parse(checkpoint_path).getroot()
        parsed_world_id = str(uuid.UUID(checkpoint.findtext("WorldId", "")))
    except (OSError, ET.ParseError, ValueError) as error:
        raise ValueError("Hydro Scout checkpoint WorldId is not a valid GUID") from error
    if (parsed_world_id != WORLD_ID or
            checkpoint.findtext("SessionName") != "Local Vehicle Test " + INSTANCE):
        raise ValueError("Hydro Scout checkpoint name or GUID differs from the exact candidate")
    sector_path = instance / "World" / "SANDBOX_0_0_0_.sbs"
    if sector_path.is_symlink() or not sector_path.is_file():
        raise ValueError("Hydro Scout sector is missing or unsafe")
    raw = sector_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SECTOR_SHA256:
        raise ValueError("Hydro Scout sector hash differs from the reviewed candidate")
    root = ET.fromstring(raw)
    xsi = "{http://www.w3.org/2001/XMLSchema-instance}type"
    entities = [node for node in root.iter("MyObjectBuilder_EntityBase")]
    grids = [node for node in entities if node.get(xsi) == "MyObjectBuilder_CubeGrid"]
    if {int(node.findtext("EntityId", "0")) for node in grids} != GRID_IDS or len(grids) != len(GRID_IDS):
        raise ValueError("Hydro Scout grid inventory differs from the reviewed six-grid fixture")
    if sum(node.get(xsi) == "MyObjectBuilder_Planet" for node in entities) != 1 or len(entities) != 7:
        raise ValueError("Hydro Scout sector contains an unexpected non-grid entity")
    by_id = {int(node.findtext("EntityId", "0")): node for node in grids}
    rover = by_id[GRID_ID]
    rover_block_node = rover.find("CubeBlocks")
    blocks = list(rover_block_node) if rover_block_node is not None else []
    if sum(block.get(xsi) == "MyObjectBuilder_Cockpit" and
           int(block.findtext("EntityId", "0")) == CONTROLLER_ID for block in blocks) != 1:
        raise ValueError("Hydro Scout cockpit/controller identity differs from the reviewed fixture")
    if sum(block.get(xsi) == "MyObjectBuilder_CameraBlock" and
           int(block.findtext("EntityId", "0")) == CAMERA_ID for block in blocks) != 1:
        raise ValueError("Hydro Scout camera identity differs from the reviewed fixture")
    all_blocks = {int(block.findtext("EntityId", "0")): (grid, block)
                  for grid in grids for block in grid.findall("./CubeBlocks/*")}
    observed = {}
    for block in blocks:
        if block.get(xsi) != "MyObjectBuilder_MotorSuspension":
            continue
        wheel_id = int(block.findtext("EntityId", "0"))
        top = all_blocks.get(int(block.findtext("TopBlockId", "0")))
        if top is not None:
            observed[wheel_id] = int(top[0].findtext("EntityId", "0"))
        for field in ("PropulsionOverride", "SteeringOverride", "SteerAngle"):
            text = block.findtext(field)
            if text is not None and (not math.isfinite(float(text)) or abs(float(text)) > 1e-7):
                raise ValueError("Hydro Scout serialized wheel override is nonzero")
    if observed != dict(zip(WHEEL_IDS, WHEEL_GRID_IDS)):
        raise ValueError("Hydro Scout suspension-to-wheel-grid mapping differs from the reviewed fixture")
    platform = by_id[143344737461122310]
    platform_block_node = platform.find("CubeBlocks")
    platform_blocks = list(platform_block_node) if platform_block_node is not None else []
    gravity = [block for block in platform_blocks
               if int(block.findtext("EntityId", "0")) == GRAVITY_ID and
               block.get(xsi) == "MyObjectBuilder_GravityGenerator" and
               block.findtext("Enabled", "").lower() == "true"]
    if len(gravity) != 1:
        raise ValueError("Hydro Scout platform gravity generator identity/state differs from saved source")


def verify_magnetar_compatibility_packages(instance: Path) -> dict[str, str]:
    """Require the exact per-instance Magnetar runtime plugins needed to boot."""
    root = instance / "MagnetarConfig" / "GitHub"
    expected = {
        "DotNetCompat-6d00a29d": DOTNET_COMPAT_TREE_SHA256,
        "LinuxCompat-68cd6e19": LINUX_COMPAT_TREE_SHA256,
    }
    observed: dict[str, str] = {}
    for name, digest in expected.items():
        package = root / name
        actual = registrar_tree_sha256(package)
        if actual != digest:
            raise ValueError(f"Hydro Scout per-instance Magnetar compatibility package differs: {name}")
        manifest_path = package / "manifest.xml"
        try:
            manifest = ET.parse(manifest_path).getroot()
        except (OSError, ET.ParseError) as error:
            raise ValueError(f"Hydro Scout Magnetar compatibility manifest is invalid: {name}") from error
        if manifest.findtext("PulsarVersion") != "2.4.2.4" or manifest.findtext("GameVersion") != "1.210.14":
            raise ValueError(f"Hydro Scout Magnetar compatibility version differs: {name}")
        if name.startswith("LinuxCompat-"):
            expected_bin = str((Path.home() / ".local/share/vragecage-worker/instances" /
                                INSTANCE / "MagnetarConfig/GitHub" / name / "Bin").resolve())
            paths = [node.text for node in manifest.findall("./Assets/Asset/Path")]
            if len(paths) != 2 or any(path != expected_bin for path in paths):
                raise ValueError("Hydro Scout Linux compatibility manifest escapes the exact instance cache")
        observed[name] = actual
    return observed


def verify_magnetar_hub_config(instance: Path) -> dict[str, str]:
    sources = instance / "MagnetarConfig/Sources/sources.xml"
    hub = instance / "MagnetarConfig/Sources/Hubs/CometWorks-magnetar-hub.bin"
    if any(path.is_symlink() or not path.is_file() for path in (sources, hub)):
        raise ValueError("Hydro Scout per-instance MagnetarHub configuration is missing or unsafe")
    sources_hash = hashlib.sha256(sources.read_bytes()).hexdigest()
    hub_hash = hashlib.sha256(hub.read_bytes()).hexdigest()
    if sources_hash != MAGNETAR_SOURCES_CONFIG_SHA256 or hub_hash != MAGNETAR_HUB_METADATA_SHA256:
        raise ValueError("Hydro Scout per-instance MagnetarHub package differs from the reviewed cache")
    try:
        root = ET.parse(sources).getroot()
    except ET.ParseError as error:
        raise ValueError("Hydro Scout MagnetarHub sources configuration is invalid") from error
    hubs = root.findall("./RemoteHubSources/RemoteHub")
    plugins = root.findall("./LocalPluginSources/LocalPlugin")
    if (len(hubs) != 1 or hubs[0].findtext("Name") != "MagnetarHub" or
            hubs[0].findtext("Repo") != "CometWorks/magnetar-hub" or
            hubs[0].findtext("Enabled", "").lower() != "true" or
            hubs[0].findtext("Trusted", "").lower() != "true" or
            len(plugins) != 1 or plugins[0].findtext("Name") != "voidwright-server-plugin" or
            plugins[0].findtext("Folder") != str(Path.home() / ".local/share/vragecage-worker/instances" /
                                                  INSTANCE / "Plugins/voidwright-server-plugin") or
            plugins[0].findtext("Enabled", "").lower() != "true"):
        raise ValueError("Hydro Scout MagnetarHub config is not scoped to its exact staged server plugin")
    return {"sources_sha256": sources_hash, "hub_metadata_sha256": hub_hash}


def verify_run(instance: Path, worker_root: Path) -> dict[str, object]:
    if instance.name != INSTANCE:
        raise ValueError("Hydro Scout package verifier invoked for the wrong instance")
    if not SERVER_PLUGIN_TREE_SHA256:
        raise ValueError("exact Hydro Scout package hashes have not been recorded")
    imported = parse_json(instance / ".vragecage-world-import.json")
    lab = parse_json(instance / ".vragecage-lab.json")
    config, config_hash = verify_config(instance)
    if (imported.get("schema") != "vragecage.world-import.v1" or
            imported.get("engine") != "magnetar" or imported.get("instance") != INSTANCE or
            imported.get("tree_sha256") != SOURCE_TREE_SHA256 or
            lab.get("schema") != "vragecage.lab-authority.v1" or
            lab.get("authority") != "destructive-lab" or lab.get("disposable") is not True or
            lab.get("engine") != "magnetar" or lab.get("instance") != INSTANCE or
            lab.get("source_tree_sha256") != SOURCE_TREE_SHA256):
        raise ValueError("Hydro Scout import or disposable-lab receipt does not match the exact candidate")
    verify_serialized_fixture(instance)
    compatibility_hashes = verify_magnetar_compatibility_packages(instance)
    hub_hashes = verify_magnetar_hub_config(instance)

    plugin_receipt = parse_json(instance / ".vragecage-plugin-stage.json")
    plugin_path = instance / "Plugins" / "voidwright-server-plugin"
    plugin_hash = registrar_tree_sha256(plugin_path)
    if (plugin_receipt.get("schema") != "vragecage.plugin-stage.v1" or
            plugin_receipt.get("instance") != INSTANCE or
            plugin_receipt.get("plugin_id") != "voidwright-server-plugin" or
            Path(str(plugin_receipt.get("path", ""))).resolve() != plugin_path.resolve() or
            plugin_hash != SERVER_PLUGIN_TREE_SHA256 or plugin_receipt.get("tree_sha256") != plugin_hash):
        raise ValueError("Hydro Scout server plugin package or stage receipt differs from the reviewed build")

    return {"schema": "vragecage.hydro-scout-local-vehicle-test-package-check.v1",
            "instance": INSTANCE, "source_tree_sha256": SOURCE_TREE_SHA256,
            "source_sector_sha256": SOURCE_SECTOR_SHA256,
            "server_plugin_tree_sha256": plugin_hash,
            "magnetar_compatibility_tree_sha256": compatibility_hashes,
            "magnetar_hub_config_sha256": hub_hashes,
            "local_vehicle_test_config_sha256": config_hash,
            "passed": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance", type=Path)
    parser.add_argument("worker_root", type=Path)
    args = parser.parse_args()
    try:
        result = verify_run(args.instance, args.worker_root)
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError, json.JSONDecodeError) as error:
        raise SystemExit(f"Hydro Scout package readiness failed: {error}") from error
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
