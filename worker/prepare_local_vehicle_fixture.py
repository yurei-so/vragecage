#!/usr/bin/env python3
"""Prepare the one reviewed Hydro Scout LocalVehicleTest clone offline.

This is intentionally bound to the installed Pertam Orbiter donor and exact
platform/vehicle entity IDs. It edits a copied save only; it never starts the
game or dispatches actuator commands.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
import uuid
from pathlib import Path


SOURCE_TREE_SHA256 = "eb397bdba978e73b7f7a1ef2c7ae811ffb81f55d6c6c50a1971fc1db981c9b34"
PLATFORM_GRID_ID = 143344737461122310
GRAVITY_GENERATOR_ID = 86965797009458869
ROVER_GRID_ID = 113504086011869676
CONTROLLER_ID = 112711005364517351
WHEEL_IDS = [135633742662929758, 92672912164488213, 129575574301717563, 72699375256551291]
EXPECTED_WHEEL_GRID_IDS = [83781654658335329, 101822873690952882, 90663942980601709, 143998760294003094]
DRONE_GRID_ID = 105676215510686055
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPE = "{" + XSI + "}type"
KEEP_GRID_IDS = {PLATFORM_GRID_ID, ROVER_GRID_ID, *EXPECTED_WHEEL_GRID_IDS}
PLATFORM_BLOCKS_TO_REMOVE = {
    "MyObjectBuilder_TimerBlock",
    "MyObjectBuilder_MotorAdvancedStator",
    "MyObjectBuilder_Cockpit",
    "MyObjectBuilder_ButtonPanel",
    "MyObjectBuilder_TextPanel",
    "MyObjectBuilder_LCDPanelsBlock",
    "MyObjectBuilder_LaserAntenna",
    "MyObjectBuilder_RadioAntenna",
    "MyObjectBuilder_ShipConnector",
    "MyObjectBuilder_RemoteControl",
    "MyObjectBuilder_MyProgrammableBlock",
    "MyObjectBuilder_EventControllerBlock",
}
WRITER_BLOCK_TYPES = {
    "MyObjectBuilder_TimerBlock",
    "MyObjectBuilder_MotorAdvancedStator",
    "MyObjectBuilder_MotorStator",
    "MyObjectBuilder_MotorRotor",
    "MyObjectBuilder_MotorAdvancedRotor",
    "MyObjectBuilder_MotorSuspension",
    "MyObjectBuilder_MyProgrammableBlock",
    "MyObjectBuilder_EventControllerBlock",
    "MyObjectBuilder_RemoteControl",
    "MyObjectBuilder_FlightMovementBlock",
    "MyObjectBuilder_BasicMissionBlock",
    "MyObjectBuilder_PathRecorderBlock",
    "MyObjectBuilder_OffensiveCombatBlock",
    "MyObjectBuilder_DefensiveCombatBlock",
}


def tree_receipt(root: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    count = 0
    size = 0
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"world contains symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"world contains unsupported file: {relative}")
        data = path.read_bytes()
        count += 1
        size += len(data)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
    return count, size, digest.hexdigest()


def child_map(element: ET.Element) -> dict[str, ET.Element]:
    return {child.tag.rsplit("}", 1)[-1]: child for child in element}


def entity_id(element: ET.Element) -> int:
    value = child_map(element).get("EntityId")
    if value is None or not (value.text or "").isdigit():
        raise ValueError("entity is missing an integer EntityId")
    return int(value.text)


def find_grid_entities(sector: ET.Element) -> dict[int, ET.Element]:
    result: dict[int, ET.Element] = {}
    for wrapper in sector.iter():
        if wrapper.tag.rsplit("}", 1)[-1] != "MyObjectBuilder_EntityBase":
            continue
        if wrapper.attrib.get(XSI_TYPE) != "MyObjectBuilder_CubeGrid":
            continue
        grid_id = entity_id(wrapper)
        if grid_id in result:
            raise ValueError(f"duplicate grid id: {grid_id}")
        result[grid_id] = wrapper
    return result


def grid_blocks(grid: ET.Element) -> list[ET.Element]:
    blocks = child_map(grid).get("CubeBlocks")
    return list(blocks) if blocks is not None else []


def type_counts(grid: ET.Element) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in grid_blocks(grid):
        block_type = block.attrib.get(XSI_TYPE, "")
        counts[block_type] = counts.get(block_type, 0) + 1
    return counts


def top_block_links(grid: ET.Element) -> list[int]:
    links: list[int] = []
    for block in grid_blocks(grid):
        if block.attrib.get(XSI_TYPE) != "MyObjectBuilder_MotorSuspension":
            continue
        top = child_map(block).get("TopBlockId")
        if top is None or not (top.text or "").isdigit():
            raise ValueError("rover suspension has no valid TopBlockId")
        links.append(int(top.text))
    return links


def reset_grid(grid: ET.Element, *, rover: bool) -> None:
    for name in ("LinearVelocity", "AngularVelocity"):
        value = child_map(grid).get(name)
        if value is not None:
            for axis in ("x", "y", "z"):
                if axis in value.attrib:
                    value.set(axis, "0")
    if not rover:
        return
    for block in grid_blocks(grid):
        if block.attrib.get(XSI_TYPE) != "MyObjectBuilder_MotorSuspension":
            continue
        values = child_map(block)
        for name in ("SteeringOverride", "PropulsionOverride", "SteerAngle"):
            if name in values:
                values[name].text = "0"
        if "Enabled" in values:
            values["Enabled"].text = "true"
    for block in grid_blocks(grid):
        if block.attrib.get(XSI_TYPE) == "MyObjectBuilder_BatteryBlock":
            values = child_map(block)
            if "Enabled" in values:
                values["Enabled"].text = "true"
            if "ProducerEnabled" in values:
                values["ProducerEnabled"].text = "true"


def pick_camera_template(grids: dict[int, ET.Element]) -> ET.Element:
    drone = grids.get(DRONE_GRID_ID)
    if drone is None:
        raise ValueError("stock camera donor grid is missing")
    cameras = [b for b in grid_blocks(drone) if b.attrib.get(XSI_TYPE) == "MyObjectBuilder_CameraBlock"]
    if len(cameras) != 1:
        raise ValueError("stock camera donor must contain exactly one camera")
    return cameras[0]


def add_test_camera(rover: ET.Element, template: ET.Element, camera_id: int) -> None:
    blocks = child_map(rover).get("CubeBlocks")
    if blocks is None:
        raise ValueError("rover has no CubeBlocks container")
    cloned = ET.fromstring(ET.tostring(template, encoding="utf-8"))
    values = child_map(cloned)
    values["EntityId"].text = str(camera_id)
    for name, text in (("Name", str(camera_id)), ("CustomName", "LocalVehicleTestCamera"),
                       ("Owner", "0"), ("Enabled", "true")):
        node = values.get(name)
        if node is None:
            node = ET.SubElement(cloned, name)
        node.text = text
    # Place above the saved cockpit. The cloned block orientation is not
    # accepted as proof of alignment; runtime forward-vector verification is
    # required before any clearance result can be used.
    min_node = values.get("Min")
    if min_node is None:
        min_node = ET.SubElement(cloned, "Min")
    for key, value in (("x", 0), ("y", 6), ("z", -5)):
        min_node.set(key, str(value))
    occupied = set()
    for block in grid_blocks(rover):
        min_block = child_map(block).get("Min")
        if min_block is not None and all(axis in min_block.attrib for axis in ("x", "y", "z")):
            occupied.add(tuple(int(min_block.attrib[axis]) for axis in ("x", "y", "z")))
    if (0, 6, -5) in occupied:
        raise ValueError("camera mount cell above cockpit is already occupied")
    blocks.append(cloned)


def reset_world_identity(checkpoint: ET.Element, config: ET.Element, instance: str) -> str:
    world_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "vragecage:local-vehicle-test:" + instance))
    for root in (checkpoint, config):
        values = child_map(root)
        if "SessionName" in values:
            values["SessionName"].text = "Local Vehicle Test " + instance
        settings = values.get("Settings")
        if settings is not None:
            for item in list(settings):
                key = item.tag.rsplit("}", 1)[-1]
                if key == "MaxDrones":
                    item.text = "0"
                elif key == "EnvironmentHostility":
                    item.text = "SAFE"
                elif key in ("EnableWolfs", "EnableSpiders", "EnableRandomEncounters"):
                    item.text = "false"
    values = child_map(checkpoint)
    if "WorldId" in values:
        values["WorldId"].text = world_id
    for name in ("CameraEntity", "ControlledObject"):
        if name in values:
            values[name].text = "0"
    if "CameraController" in values:
        values["CameraController"].text = "Spectator"
    if "ControlledEntities" in values:
        controlled = values["ControlledEntities"]
        dictionary = next((child for child in controlled if child.tag.rsplit("}", 1)[-1] == "dictionary"), None)
        if dictionary is None:
            dictionary = ET.SubElement(controlled, "dictionary")
        for child in list(dictionary):
            dictionary.remove(child)
        for child in list(controlled):
            if child is not dictionary:
                controlled.remove(child)
    # The donor save contains a character that is removed with its source
    # grids. Clear all saved character/respawn references and inherited player
    # connection/camera state so the clone starts without a bound human.
    for identity in checkpoint.iter():
        if identity.tag.rsplit("}", 1)[-1] != "MyObjectBuilder_Identity":
            continue
        identity_values = child_map(identity)
        character = identity_values.get("CharacterEntityId")
        if character is not None:
            character.text = "0"
        for collection_name in ("SavedCharacters", "RespawnShips"):
            collection = identity_values.get(collection_name)
            if collection is not None:
                for child in list(collection):
                    collection.remove(child)
    for players in checkpoint.iter():
        if players.tag.rsplit("}", 1)[-1] != "AllPlayersData":
            continue
        for node in players.iter():
            if node.tag.rsplit("}", 1)[-1] == "Connected":
                node.text = "false"
            elif node.tag.rsplit("}", 1)[-1] == "ForceRealPlayer":
                node.text = "false"
        for camera_data in list(players.iter()):
            if camera_data.tag.rsplit("}", 1)[-1] == "EntityCameraData":
                parent = next((candidate for candidate in players.iter()
                               if camera_data in list(candidate)), None)
                if parent is not None:
                    parent.remove(camera_data)
    return world_id


def prepare(source: Path, output: Path, instance: str) -> dict:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite existing output: {output}")
    required = ("Sandbox.sbc", "Sandbox_config.sbc", "SANDBOX_0_0_0_.sbs")
    if any(not (source / name).is_file() for name in required):
        raise ValueError("source is not a complete saved world")
    source_count, source_size, source_hash = tree_receipt(source)
    if source_hash != SOURCE_TREE_SHA256:
        raise ValueError("source tree is not the reviewed stock Pertam Orbiter save")

    sector_path = source / "SANDBOX_0_0_0_.sbs"
    sector = ET.parse(sector_path).getroot()
    grids = find_grid_entities(sector)
    missing = KEEP_GRID_IDS - set(grids)
    if missing:
        raise ValueError("source is missing exact allowed grids: " + ",".join(map(str, sorted(missing))))
    rover = grids[ROVER_GRID_ID]
    root_blocks = grid_blocks(rover)
    suspensions = [b for b in root_blocks if b.attrib.get(XSI_TYPE) == "MyObjectBuilder_MotorSuspension"]
    if len(suspensions) != 4 or [entity_id(b) for b in suspensions] != WHEEL_IDS:
        raise ValueError("rover suspension identity map differs from the reviewed donor")
    links = top_block_links(rover)
    wheel_grid_by_block: dict[int, int] = {}
    for grid_id in EXPECTED_WHEEL_GRID_IDS:
        found = [entity_id(block) for block in grid_blocks(grids[grid_id])
                 if block.attrib.get(XSI_TYPE) == "MyObjectBuilder_Wheel"]
        if len(found) != 1:
            raise ValueError(f"wheel top grid {grid_id} must contain exactly one wheel")
        wheel_grid_by_block[found[0]] = grid_id
    expected_map = [wheel_grid_by_block.get(link) for link in links]
    if set(expected_map) != set(EXPECTED_WHEEL_GRID_IDS) or len(set(expected_map)) != 4:
        raise ValueError("four suspension top blocks do not map to four exact wheel grids")

    platform = grids[PLATFORM_GRID_ID]
    gravity = [b for b in grid_blocks(platform)
               if b.attrib.get(XSI_TYPE) == "MyObjectBuilder_GravityGenerator" and entity_id(b) == GRAVITY_GENERATOR_ID]
    if len(gravity) != 1:
        raise ValueError("platform does not contain the reviewed gravity generator")
    gravity_values = child_map(gravity[0])
    if gravity_values.get("Enabled") is None or gravity_values["Enabled"].text != "true":
        raise ValueError("gravity generator is not enabled in the donor")
    if gravity_values.get("GravityAcceleration") is None or abs(float(gravity_values["GravityAcceleration"].text) - 3.0) > 1e-5:
        raise ValueError("gravity generator acceleration differs from the reviewed donor")

    camera_template = pick_camera_template(grids)
    max_id = max(int(node.text) for node in sector.iter()
                 if node.tag.rsplit("}", 1)[-1] == "EntityId" and (node.text or "").isdigit())
    camera_id = max_id + 1
    if camera_id >= (1 << 63):
        raise ValueError("no safe positive entity ID remains for test camera")
    reset_grid(rover, rover=True)
    add_test_camera(rover, camera_template, camera_id)

    # The platform's timer and advanced stator are unrelated control surfaces.
    removed_platform_blocks: list[dict[str, str]] = []
    platform_container = child_map(platform).get("CubeBlocks")
    assert platform_container is not None
    for block in list(platform_container):
        block_type = block.attrib.get(XSI_TYPE, "")
        if block_type in PLATFORM_BLOCKS_TO_REMOVE:
            values = child_map(block)
            removed_platform_blocks.append({
                "type": block_type,
                "entity_id": values.get("EntityId").text if values.get("EntityId") is not None else "",
                "name": values.get("CustomName").text if values.get("CustomName") is not None else "",
            })
            platform_container.remove(block)

    retained_entity_ids: list[int] = []
    for parent in sector.iter():
        for wrapper in list(parent):
            if wrapper.tag.rsplit("}", 1)[-1] != "MyObjectBuilder_EntityBase":
                continue
            kind = wrapper.attrib.get(XSI_TYPE, "")
            if kind == "MyObjectBuilder_CubeGrid":
                if entity_id(wrapper) not in KEEP_GRID_IDS:
                    parent.remove(wrapper)
                else:
                    retained_entity_ids.append(entity_id(wrapper))
            elif kind == "MyObjectBuilder_Planet":
                retained_entity_ids.append(entity_id(wrapper))
            else:
                parent.remove(wrapper)

    # The source checkpoint has a saved spectator character. Remove its stale
    # camera/control binding so the clone opens with no human or bot in control.
    checkpoint_path = source / "Sandbox.sbc"
    config_path = source / "Sandbox_config.sbc"
    checkpoint = ET.parse(checkpoint_path).getroot()
    config = ET.parse(config_path).getroot()
    world_id = reset_world_identity(checkpoint, config, instance)

    staging = output.parent / (output.name + ".tmp")
    if staging.exists():
        raise ValueError(f"refusing to overwrite existing staging directory: {staging}")
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, staging)
        # Stale binary sector cache would restore removed grids; never copy it.
        for cached in staging.glob("SANDBOX_0_0_0_.sbsB*"):
            cached.unlink()
        ET.ElementTree(sector).write(staging / "SANDBOX_0_0_0_.sbs", encoding="utf-8", xml_declaration=True)
        ET.ElementTree(checkpoint).write(staging / "Sandbox.sbc", encoding="utf-8", xml_declaration=True)
        ET.ElementTree(config).write(staging / "Sandbox_config.sbc", encoding="utf-8", xml_declaration=True)
        result_count, result_size, result_hash = tree_receipt(staging)
        # Reparse after serialization and audit the assembly, writer inventory,
        # and platform generator against the exact staged bytes.
        staged_sector = ET.parse(staging / "SANDBOX_0_0_0_.sbs").getroot()
        staged_grids = find_grid_entities(staged_sector)
        if set(staged_grids) != KEEP_GRID_IDS:
            raise ValueError("serialized sector does not contain exactly the five rover grids and platform")
        if len(top_block_links(staged_grids[ROVER_GRID_ID])) != 4:
            raise ValueError("serialized rover does not retain four suspension links")
        forbidden = WRITER_BLOCK_TYPES - {"MyObjectBuilder_MotorSuspension"}
        unexpected = [(gid, block_type) for gid in KEEP_GRID_IDS for block_type in type_counts(staged_grids[gid])
                      if block_type in forbidden]
        if unexpected:
            raise ValueError("retained assembly contains an unrelated writer block: " + repr(unexpected))
        platform_counts = type_counts(staged_grids[PLATFORM_GRID_ID])
        if platform_counts.get("MyObjectBuilder_GravityGenerator") != 1:
            raise ValueError("serialized platform lost its gravity generator")
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    sector_hash = hashlib.sha256((output / "SANDBOX_0_0_0_.sbs").read_bytes()).hexdigest()
    receipt = {
        "schema": "voidwright.local-vehicle-fixture-prep.v1",
        "instance": instance,
        "source_path": str(source),
        "output_path": str(output),
        "source": {
            "files": source_count,
            "bytes": source_size,
            "tree_sha256": source_hash,
            "sector_sha256": hashlib.sha256(sector_path.read_bytes()).hexdigest(),
        },
        "output": {
            "files": result_count,
            "bytes": result_size,
            "tree_sha256": result_hash,
            "sector_sha256": sector_hash,
        },
        "retained_grids": sorted(KEEP_GRID_IDS),
        "platform_grid_id": PLATFORM_GRID_ID,
        "gravity_generator_id": GRAVITY_GENERATOR_ID,
        "rover_grid_id": ROVER_GRID_ID,
        "controller_id": CONTROLLER_ID,
        "camera_id": camera_id,
        "world_id": world_id,
        "wheel_ids": WHEEL_IDS,
        "wheel_grid_ids_in_suspension_order": expected_map,
        "removed_platform_blocks": removed_platform_blocks,
        "active_world_start": False,
        "actuator_commands_issued": False,
        "runtime_ground_contact_verified": False,
        "runtime_gravity_at_wheels_verified": False,
        "runtime_camera_forward_alignment_verified": False,
        "runtime_directional_clearance_verified": False,
        "runtime_chassis_clearance_verified": False,
        "runtime_full_assembly_writer_inventory_verified": False,
        "runtime_zero_command_baseline_verified": False,
        "runtime_actuator_readbacks_verified": False,
        "runtime_battery_state_verified": False,
        "runtime_gravity_generator_power_verified": False,
        "runtime_bounded_stop_path_verified": False,
        "saved_player_character_camera_references_reset": True,
        "inherited_connected_player_binding": False,
    }
    manifest_path = output.with_name(output.name + ".fixture.json")
    manifest_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="unmodified installed Pertam Orbiter save")
    parser.add_argument("output", type=Path, help="new isolated save-source directory")
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    if not args.instance or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in args.instance):
        raise SystemExit("invalid instance name")
    try:
        receipt = prepare(args.source, args.output, args.instance)
    except (OSError, ValueError, ET.ParseError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
