#!/usr/bin/env python3
"""Hash-bound dependency checks for the one pinned q LocalVehicleTest fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

INSTANCE = "local-vehicle-steering-v4-20261021-q"
SOURCE_TREE_SHA256 = "b1564710681a6d0bc901ab0437d59f475f62c1fabda7fdd6bd9198306a5982ed"
VOIDWRIGHT_MOD_TREE_SHA256 = "78632ea6120b33f1e2a9446b95264e58f356f432af527155a28bfbc65a9fbae3"
SERVER_PLUGIN_TREE_SHA256 = "0f8def9fd9dd5428ac57b0d2684077c321fa9a73651e27b1ed9d6e1fb30e5f51"
LOCAL_OPERATOR_PACKAGE_SHA256 = "405a7db26431e2488a8c8b338b3e1c2043d6fa7d549d528c4d701b84f4b5aba5"
LOCAL_OPERATOR_ASSEMBLY_SHA256 = "f50bbac9af61f06eb4e3aa2cbb8caad15e84f88ca47577753205215ca586e8ab"
LOCAL_OPERATOR_SOURCE_SHA256 = "fbc9b72b73bc953bc01f5efc7da28c53a982e22fb32b033beb5f5e44cbf5777e"
EXPECTED_MODS = {
    "1090562767": "097fe6a25c98121fe178b885f0d05569b33208c1d9be26895a067b2e064f5074",
    "2298956701": "3b90a1127a049dbcda015c517c29a598f9407e084908d160baa3c411c352313c",
    "3015328630": "a6174ea15ced2d2a0520f4d869ac4354747707f2ebe1e0726583b985e526d62c",
    "3795893318": "bc040c20c4cad49f7debf6143310384f6c9ed768db84188e9aa22e4533149686",
    "516770964": "0274d7dace4e3bf5db567e9af09643947513e94000b073953fadcf9a35391c9f",
}
SCRIPT_REQUIRED_MODS = set(EXPECTED_MODS) - {"1090562767"}
EXPECTED_GRID_ID = 105034224363529183
EXPECTED_CONTROLLER_ID = 83468039688685570
EXPECTED_CAMERA_ID = 78509476925621216
EXPECTED_WHEEL_IDS = [72448287472244394, 102872810511526201,
                      87663115116726466, 95379321851010785]
EXPECTED_WHEEL_GRID_IDS = [130825891980829223, 127988954682183794,
                           141480516845941196, 95733545729686414]


def loaded_marker_valid(mod_id: str, definitions_loaded: bool, scripts_loaded: bool) -> bool:
    if mod_id not in EXPECTED_MODS:
        return False
    if mod_id in SCRIPT_REQUIRED_MODS:
        return scripts_loaded
    return definitions_loaded


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def verify_local_vehicle_instrumentation(instance: Path, config: dict[str, object]) -> None:
    ledger_path = instance / ".vragecage-diagnostic-instrumentation.json"
    sector_path = instance / "World/SANDBOX_0_0_0_.sbs"
    if (ledger_path.is_symlink() or not ledger_path.is_file() or
            sector_path.is_symlink() or not sector_path.is_file()):
        raise ValueError("q LocalVehicleTest instrumentation ledger or sector is missing or unsafe")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    sector_raw = sector_path.read_bytes()
    if (ledger.get("schema") != "vragecage.diagnostic-instrumentation.v1" or
            ledger.get("instance") != INSTANCE or
            ledger.get("authority") != "disposable-clone-only" or
            ledger.get("source_tree_sha256") != SOURCE_TREE_SHA256 or
            ledger.get("sector_after_sha256") != hashlib.sha256(sector_raw).hexdigest() or
            not isinstance(ledger.get("controller"), dict) or
            ledger["controller"].get("entity_id") != str(EXPECTED_CONTROLLER_ID) or
            ledger["controller"].get("type") != "MyObjectBuilder_Cockpit" or
            ledger["controller"].get("subtype") != "SmallBlockCapCockpit" or
            sorted(str(value) for value in ledger.get("existing_suspension_ids", [])) !=
            sorted(str(value) for value in EXPECTED_WHEEL_IDS)):
        raise ValueError("q LocalVehicleTest instrumentation ledger does not bind the pinned fixture")
    added = ledger.get("added_blocks")
    if not isinstance(added, list) or len(added) != 2:
        raise ValueError("q LocalVehicleTest instrumentation must record exactly the camera and Core")
    cameras = [item for item in added if isinstance(item, dict) and item.get("subtype") == "SmallCameraBlock"]
    cores = [item for item in added if isinstance(item, dict) and
             item.get("subtype") == "VoidwrightNavigationControllerSmall"]
    if len(cameras) != 1 or len(cores) != 1:
        raise ValueError("q LocalVehicleTest instrumentation camera/Core identities are invalid")
    camera, core = cameras[0], cores[0]
    if (camera.get("entity_id") != str(config["camera_id"]) or
            any(item.get("grid_id") != str(EXPECTED_GRID_ID) or item.get("grid_name") != "Blue Truck"
                for item in (camera, core))):
        raise ValueError("q LocalVehicleTest camera is not bound to the instrumented Blue Truck")
    geometry = ledger.get("geometry")
    if (not isinstance(geometry, dict) or
            any(type(geometry.get(key)) not in (int, float) or geometry[key] < 0.05
                for key in ("camera_min_separation_m", "core_min_separation_m",
                            "instrument_pair_min_separation_m"))):
        raise ValueError("q LocalVehicleTest camera/Core clearance receipt is missing or below its margin")

    tree = ET.fromstring(sector_raw)
    xsi_type = "{http://www.w3.org/2001/XMLSchema-instance}type"
    grids = [entity for entity in tree.iter("MyObjectBuilder_EntityBase")
             if entity.get(xsi_type) == "MyObjectBuilder_CubeGrid" and
             entity.findtext("EntityId") == str(EXPECTED_GRID_ID) and
             entity.findtext("DisplayName") == "Blue Truck"]
    if len(grids) != 1:
        raise ValueError("q LocalVehicleTest sector does not contain the unique instrumented Blue Truck")
    blocks_parent = grids[0].find("CubeBlocks")
    blocks = list(blocks_parent) if blocks_parent is not None else []
    if len(blocks) != 87:
        raise ValueError("q LocalVehicleTest Blue Truck block inventory differs from the reviewed fixture")
    all_blocks = {block.findtext("EntityId"): (grid, block)
                  for grid in tree.iter("MyObjectBuilder_EntityBase")
                  for block in grid.findall("./CubeBlocks/*") if block.findtext("EntityId")}
    by_id = {block.findtext("EntityId"): block for block in blocks}
    controller = [block for block in blocks if block.findtext("EntityId") == str(EXPECTED_CONTROLLER_ID) and
                  block.get(xsi_type) == "MyObjectBuilder_Cockpit" and
                  block.findtext("SubtypeName") == "SmallBlockCapCockpit"]
    camera_blocks = [block for block in blocks if block.findtext("EntityId") == camera["entity_id"] and
                     block.get(xsi_type) == "MyObjectBuilder_CameraBlock" and
                     block.findtext("SubtypeName") == "SmallCameraBlock"]
    core_blocks = [block for block in blocks if block.findtext("EntityId") == core["entity_id"] and
                   block.get(xsi_type) == "MyObjectBuilder_UpgradeModule" and
                   block.findtext("SubtypeName") == "VoidwrightNavigationControllerSmall"]
    if len(controller) != 1 or len(camera_blocks) != 1 or len(core_blocks) != 1:
        raise ValueError("q LocalVehicleTest camera, Core, or cockpit is missing from the instrumented sector")
    expected_wheels = dict(zip(map(str, EXPECTED_WHEEL_IDS), map(str, EXPECTED_WHEEL_GRID_IDS)))
    observed_wheels: dict[str, str] = {}
    for block in blocks:
        if block.get(xsi_type) != "MyObjectBuilder_MotorSuspension":
            continue
        top = all_blocks.get(block.findtext("TopBlockId"))
        if top is not None and top[1].get(xsi_type) == "MyObjectBuilder_Wheel":
            observed_wheels[block.findtext("EntityId")] = top[0].findtext("EntityId") or ""
    if observed_wheels != expected_wheels:
        raise ValueError("q LocalVehicleTest four-wheel mechanical grid map differs from the pinned fixture")


def verify_local_vehicle_config(instance: Path, path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("q LocalVehicleTest config missing or unsafe")
    config = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    if not isinstance(config, dict):
        raise ValueError("q LocalVehicleTest config must be a JSON object")
    required = {
        "schema", "instance", "grid_id", "controller_id", "source_tree_sha256", "mode",
        "propulsion_enabled", "isolated_clear", "max_speed_meters_per_second",
        "max_displacement_meters", "command_timeout_seconds", "host_watchdog_seconds",
        "camera_id", "wheel_ids", "wheel_grid_ids",
    }
    optional = {"steering_direction_proof", "current_plugin_tree_sha256"}
    if set(config) - required - optional or not required.issubset(config):
        raise ValueError("q LocalVehicleTest config fields do not match the reviewed schema")
    if (optional.intersection(config) and not optional.issubset(config)):
        raise ValueError("q steering proof and plugin hash must be supplied together")
    if (config.get("schema") != "vragecage.local-vehicle-test.v1" or
            config.get("instance") != INSTANCE or
            type(config.get("grid_id")) is not int or config["grid_id"] != EXPECTED_GRID_ID or
            type(config.get("controller_id")) is not int or
            config["controller_id"] != EXPECTED_CONTROLLER_ID or
            config.get("source_tree_sha256") != SOURCE_TREE_SHA256 or
            config.get("mode") != "read-only-baseline" or
            config.get("propulsion_enabled") is not False or
            config.get("isolated_clear") is not False):
        raise ValueError("q LocalVehicleTest config identity or mode does not match the pinned fixture")
    speed = config.get("max_speed_meters_per_second")
    displacement = config.get("max_displacement_meters")
    timeout = config.get("command_timeout_seconds")
    watchdog = config.get("host_watchdog_seconds")
    if (type(speed) not in (int, float) or not 0 < speed <= 0.2 or
            type(displacement) not in (int, float) or not 0 < displacement <= 6.0 or
            type(timeout) is not int or not 1 <= timeout <= 5 or
            type(watchdog) is not int or watchdog != 60 or
            type(config.get("camera_id")) is not int or config["camera_id"] != EXPECTED_CAMERA_ID or
            config.get("wheel_ids") != EXPECTED_WHEEL_IDS or
            config.get("wheel_grid_ids") != EXPECTED_WHEEL_GRID_IDS or
            any(type(value) is not int for value in config["wheel_ids"] + config["wheel_grid_ids"])):
        raise ValueError("q LocalVehicleTest config limits or instrumentation identities are invalid")
    if optional.issubset(config):
        proof_hash = config.get("current_plugin_tree_sha256")
        if proof_hash != SERVER_PLUGIN_TREE_SHA256:
            raise ValueError("q LocalVehicleTest direction proof package hash is invalid")
    verify_local_vehicle_instrumentation(instance, config)
    return config


def verify_local_operator_artifact(instance: Path, worker_root: Path) -> dict[str, str]:
    receipt_path = instance / ".vragecage-local-vehicle-test-operator.json"
    operator_path = worker_root / "incoming/q-camera-binding-20261021/AgentMcp"
    if (receipt_path.is_symlink() or not receipt_path.is_file() or
            operator_path.is_symlink() or not operator_path.is_dir()):
        raise ValueError("q LocalVehicleTest operator artifact or receipt is missing or unsafe")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    assembly = operator_path / "Voidwright.AgentMcp.dll"
    source_path = operator_path / "Source"
    if (receipt.get("schema") != "vragecage.local-vehicle-test-operator-artifact.v1" or
            receipt.get("instance") != INSTANCE or
            receipt.get("package_relative_path") != "incoming/q-camera-binding-20261021/AgentMcp" or
            receipt.get("package_tree_sha256") != LOCAL_OPERATOR_PACKAGE_SHA256 or
            receipt.get("assembly_sha256") != LOCAL_OPERATOR_ASSEMBLY_SHA256 or
            receipt.get("source_tree_sha256") != LOCAL_OPERATOR_SOURCE_SHA256 or
            assembly.is_symlink() or not assembly.is_file() or
            source_path.is_symlink() or not source_path.is_dir() or
            registrar_tree_sha256(operator_path)[2] != LOCAL_OPERATOR_PACKAGE_SHA256 or
            hashlib.sha256(assembly.read_bytes()).hexdigest() != LOCAL_OPERATOR_ASSEMBLY_SHA256 or
            registrar_tree_sha256(source_path)[2] != LOCAL_OPERATOR_SOURCE_SHA256):
        raise ValueError("q LocalVehicleTest operator artifact or exact source receipt differs from the reviewed build")
    return {"package_tree_sha256": LOCAL_OPERATOR_PACKAGE_SHA256,
            "assembly_sha256": LOCAL_OPERATOR_ASSEMBLY_SHA256,
            "source_tree_sha256": LOCAL_OPERATOR_SOURCE_SHA256}


def tree_sha256(root: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    files = 0
    byte_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"symlink in package: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"unsupported package entry: {path}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        files += 1
        byte_count += len(content)
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content)
    return files, byte_count, digest.hexdigest()


def registrar_tree_sha256(root: Path) -> tuple[int, int, str]:
    """Match register-magnetar-{mod,plugin}.py receipt hashing exactly."""
    digest = hashlib.sha256()
    files = 0
    byte_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"symlink in staged package: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"unsupported staged package entry: {path}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        files += 1
        byte_count += len(content)
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(content)
    return files, byte_count, digest.hexdigest()


def read_mod_ids(sandbox: Path) -> list[str]:
    root = ET.parse(sandbox).getroot()
    mods = root.find(".//Mods")
    if mods is None:
        raise ValueError("Sandbox.sbc has no Mods list")
    values = [item.findtext("PublishedFileId") for item in mods]
    if any(not isinstance(value, str) or not value.isdigit() for value in values):
        raise ValueError("Sandbox.sbc contains an invalid published mod ID")
    return values


def published_mod_receipts(source_world: Path, worker_root: Path) -> list[dict[str, object]]:
    ids = read_mod_ids(source_world / "Sandbox.sbc")
    if len(ids) != len(EXPECTED_MODS) or set(ids) != set(EXPECTED_MODS):
        raise ValueError("q fixture published mod IDs do not match the audited five-mod allowlist")
    cache = worker_root / "magnetar-steam/steamapps/workshop/content/244850"
    receipts = []
    for mod_id in ids:
        path = cache / mod_id
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"q Workshop package is missing or unsafe: {mod_id}")
        files, byte_count, digest = tree_sha256(path)
        if digest != EXPECTED_MODS[mod_id]:
            raise ValueError(f"q Workshop package content hash mismatch: {mod_id}")
        receipts.append({"published_file_id": mod_id, "files": files,
                         "bytes": byte_count, "tree_sha256": digest})
    return receipts


def verify_run(instance: Path, worker_root: Path) -> dict[str, object]:
    if instance.name != INSTANCE:
        raise ValueError("hash-bound q package gate invoked for the wrong instance")
    imported_path = instance / ".vragecage-world-import.json"
    if imported_path.is_symlink() or not imported_path.is_file():
        raise ValueError("q import receipt missing or unsafe")
    imported = json.loads(imported_path.read_text(encoding="utf-8"))
    if (imported.get("instance") != INSTANCE or imported.get("tree_sha256") != SOURCE_TREE_SHA256 or
            imported.get("published_mod_packages") != published_mod_receipts(instance / "World", worker_root)):
        raise ValueError("q import receipt does not bind the pinned source and exact published mod bytes")
    local_mods = instance / ".vragecage-mod-stage.json"
    if local_mods.is_symlink() or not local_mods.is_file():
        raise ValueError("q Voidwright mod stage receipt missing or unsafe")
    mod_stage = json.loads(local_mods.read_text(encoding="utf-8"))
    mod_path = Path(str(mod_stage.get("path", "")))
    mod_cache = worker_root / "magnetar-steam/steamapps/workshop/content/244850/90127687454063603"
    if (mod_stage.get("instance") != INSTANCE or mod_stage.get("name") != "Voidwright" or
            mod_stage.get("synthetic_workshop_id") != 90127687454063603 or
            mod_stage.get("tree_sha256") != VOIDWRIGHT_MOD_TREE_SHA256 or
            mod_path.resolve() != mod_cache.resolve() or
            registrar_tree_sha256(mod_cache)[2] != VOIDWRIGHT_MOD_TREE_SHA256):
        raise ValueError("q Voidwright mod cache or stage receipt differs from the reviewed package")

    config_path = instance / ".vragecage-local-vehicle-test.json"
    verify_local_vehicle_config(instance, config_path)
    operator_artifact = verify_local_operator_artifact(instance, worker_root)
    plugin_receipt_path = instance / ".vragecage-plugin-stage.json"
    plugin_required = True
    plugin_stage = None
    if plugin_receipt_path.exists() or plugin_receipt_path.is_symlink():
        if plugin_receipt_path.is_symlink() or not plugin_receipt_path.is_file():
            raise ValueError("q plugin stage receipt is unsafe")
        plugin_stage = json.loads(plugin_receipt_path.read_text(encoding="utf-8"))
        expected_plugin_path = instance / "Plugins/voidwright-server-plugin"
        if (plugin_stage.get("instance") != INSTANCE or
                plugin_stage.get("plugin_id") != "voidwright-server-plugin" or
                plugin_stage.get("tree_sha256") != SERVER_PLUGIN_TREE_SHA256 or
                Path(str(plugin_stage.get("path", ""))).resolve() != expected_plugin_path.resolve() or
                registrar_tree_sha256(expected_plugin_path)[2] != SERVER_PLUGIN_TREE_SHA256):
            raise ValueError("q plugin content or stage receipt differs from the reviewed package")
    else:
        raise ValueError("q requires the reviewed server-plugin stage receipt")

    return {"schema": "vragecage.local-vehicle-test-package-check.v1", "instance": INSTANCE,
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "published_mod_packages": imported["published_mod_packages"],
            "voidwright_mod_tree_sha256": VOIDWRIGHT_MOD_TREE_SHA256,
            "server_plugin_tree_sha256": SERVER_PLUGIN_TREE_SHA256 if plugin_stage else None,
            "local_vehicle_test_operator": operator_artifact,
            "plugin_stage_required": plugin_required,
            "passed": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance")
    parser.add_argument("worker_root")
    args = parser.parse_args()
    try:
        result = verify_run(Path(args.instance), Path(args.worker_root))
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as error:
        raise SystemExit(f"q package readiness failed: {error}") from error
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
