#!/usr/bin/env python3
"""Build a fresh offline LAND candidate from a reviewed planetary vehicle design.

This tool only edits a copied save. It does not claim that the vehicle is on
terrain. The new clone requires live no-command ground-support, clearance,
writer-audit, actuator-readback, and stop evidence before any command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from worker import prepare_local_vehicle_fixture as common

XSI_TYPE = common.XSI_TYPE
XSI = common.XSI
WRITER_TYPES = common.WRITER_BLOCK_TYPES - {"MyObjectBuilder_MotorSuspension"}


def tree_receipt(root: Path) -> tuple[int, int, str]:
    return common.tree_receipt(root)


def _child(element: ET.Element, name: str) -> ET.Element:
    node = common.child_map(element).get(name)
    if node is None:
        raise ValueError(f"saved entity is missing {name}")
    return node


def _xyz(node: ET.Element) -> tuple[float, float, float]:
    return tuple(float(node.attrib[axis]) for axis in ("x", "y", "z"))


def _unit(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = sum(v * v for v in value) ** 0.5
    if length < 1e-9:
        raise ValueError("cannot normalize a zero direction")
    return tuple(v / length for v in value)


def _dot(a, b): return sum(x * y for x, y in zip(a, b))
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _basis(forward, up):
    f = _unit(forward)
    u = _unit(up)
    right = _unit(_cross(f, u))
    f = _unit(_cross(u, right))
    # Matrices are stored by rows, with right/up/backward as their columns.
    backward = tuple(-v for v in f)
    return tuple((right[i], u[i], backward[i]) for i in range(3))


def _transpose(m): return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))
def _mul(a, b): return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))
def _apply(m, v): return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))


def _quat(m):
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = (trace + 1.0) ** 0.5 * 2
        return ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s,
                (m[1][0] - m[0][1]) / s, 0.25 * s)
    i = max(range(3), key=lambda index: m[index][index])
    if i == 0:
        s = (1.0 + m[0][0] - m[1][1] - m[2][2]) ** 0.5 * 2
        return (0.25 * s, (m[0][1] + m[1][0]) / s,
                (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    if i == 1:
        s = (1.0 + m[1][1] - m[0][0] - m[2][2]) ** 0.5 * 2
        return ((m[0][1] + m[1][0]) / s, 0.25 * s,
                (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    s = (1.0 + m[2][2] - m[0][0] - m[1][1]) ** 0.5 * 2
    return ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s,
            0.25 * s, (m[1][0] - m[0][1]) / s)


def _grid_pose(grid: ET.Element):
    pose = _child(grid, "PositionAndOrientation")
    position = _xyz(_child(pose, "Position"))
    forward = _xyz(_child(pose, "Forward"))
    up = _xyz(_child(pose, "Up"))
    return position, _basis(forward, up)


def _set_grid_pose(grid: ET.Element, position, basis) -> None:
    pose = _child(grid, "PositionAndOrientation")
    pos_node = _child(pose, "Position")
    forward_node = _child(pose, "Forward")
    up_node = _child(pose, "Up")
    right = tuple(basis[i][0] for i in range(3))
    up = tuple(basis[i][1] for i in range(3))
    backward = tuple(basis[i][2] for i in range(3))
    forward = tuple(-v for v in backward)
    for node, values in ((pos_node, position), (forward_node, forward), (up_node, up)):
        for axis, value in zip(("x", "y", "z"), values):
            node.set(axis, format(value, ".17g"))
    q = _quat(basis)
    orientation = _child(pose, "Orientation")
    for axis, value in zip(("X", "Y", "Z", "W"), q):
        _child(orientation, axis).text = format(value, ".17g")


def prepare_from_planet_base(base_world: Path, design_world: Path, donor_receipt: Path,
                             output: Path, instance: str, planet_generator: str = "EarthLike") -> dict:
    """Compose a fresh stock-planet world and transplant only the vehicle design."""
    base_world, design_world, output = base_world.resolve(), design_world.resolve(), output.resolve()
    donor_receipt = donor_receipt.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite existing output: {output}")
    donor, design_sector, design_grids = validate_donor(design_world, donor_receipt)
    base_count, base_size, base_hash = tree_receipt(base_world)
    required = ("Sandbox.sbc", "Sandbox_config.sbc", "SANDBOX_0_0_0_.sbs")
    if any(not (base_world / name).is_file() for name in required):
        raise ValueError("planet base is not a complete saved-world scenario")
    sector = ET.parse(base_world / "SANDBOX_0_0_0_.sbs").getroot()
    planets = [node for node in sector.iter() if node.tag.rsplit("}", 1)[-1] == "MyObjectBuilder_EntityBase" and
               node.attrib.get(XSI_TYPE) == "MyObjectBuilder_Planet"]
    matching_planets = [node for node in planets
                        if (common.child_map(node).get("PlanetGenerator") is not None and
                            common.child_map(node)["PlanetGenerator"].text == planet_generator)]
    if len(matching_planets) != 1:
        raise ValueError("planet base must contain exactly one " + planet_generator + " planet")
    planet = matching_planets[0]
    storage = _child(planet, "StorageName").text or ""
    if Path(storage).name != storage or not (base_world / (storage + ".vx2")).is_file():
        raise ValueError("fresh base planet voxel storage is missing")
    planet_pose = _child(planet, "PositionAndOrientation")
    center = _xyz(_child(planet_pose, "Position"))
    max_hill = float(_child(planet, "MaximumHillRadius").text)
    if not (max_hill > 0):
        raise ValueError("planet maximum hill radius is invalid")

    # Use the metadata's conservative outer surface radius for a near-terrain
    # candidate position. Live contact is still UNKNOWN until the new instance
    # reports all four wheel contacts and gravity on its no-command baseline.
    target_up = (1.0, 0.0, 0.0)
    target_forward = (0.0, 0.0, -1.0)
    target_basis = _basis(target_forward, target_up)
    target_position = tuple(center[i] + target_up[i] * (max_hill + 2.0) for i in range(3))
    chassis_id = donor["_chassis_id"]
    chassis_position, chassis_basis = _grid_pose(design_grids[chassis_id])
    rotation = _mul(target_basis, _transpose(chassis_basis))

    objects = common.child_map(sector).get("SectorObjects")
    if objects is None:
        raise ValueError("planet base sector has no SectorObjects container")
    for parent in sector.iter():
        for entity in list(parent):
            if entity.tag.rsplit("}", 1)[-1] == "MyObjectBuilder_EntityBase" and entity is not planet:
                parent.remove(entity)
    inserted_ids: list[int] = []
    removed_writers: list[dict[str, str | int]] = []
    keep_grids = {chassis_id, *donor["_wheel_grid_ids"]}
    for grid_id in sorted(keep_grids):
        grid = ET.fromstring(ET.tostring(design_grids[grid_id], encoding="utf-8"))
        inserted_ids.append(grid_id)
        container = common.child_map(grid).get("CubeBlocks")
        if container is None:
            raise ValueError("design grid is missing its block inventory")
        for block in list(container):
            block_type = block.attrib.get(XSI_TYPE, "")
            if block_type in WRITER_TYPES:
                block_id = _block_entity_id(block)
                removed_writers.append({"grid_id": grid_id, "block_id": block_id or 0,
                                        "block_type": block_type})
                container.remove(block)
            elif block_type == "MyObjectBuilder_MotorSuspension":
                values = common.child_map(block)
                for name in ("SteeringOverride", "PropulsionOverride", "SteerAngle"):
                    if name in values:
                        values[name].text = "0"
                for name in ("Brake", "Handbrake"):
                    if name in values:
                        values[name].text = "false"
        for name in ("LinearVelocity", "AngularVelocity"):
            velocity = common.child_map(grid).get(name)
            if velocity is not None:
                for axis in ("x", "y", "z"):
                    if axis in velocity.attrib:
                        velocity.set(axis, "0")
        old_position, old_basis = _grid_pose(grid)
        relative = tuple(old_position[i] - chassis_position[i] for i in range(3))
        local = _apply(_transpose(chassis_basis), relative)
        new_position = tuple(target_position[i] + _apply(target_basis, local)[i] for i in range(3))
        _set_grid_pose(grid, new_position, _mul(rotation, old_basis))
        objects.append(grid)

    checkpoint = ET.parse(base_world / "Sandbox.sbc").getroot()
    config = ET.parse(base_world / "Sandbox_config.sbc").getroot()
    world_id = common.reset_world_identity(checkpoint, config, instance)
    staging = output.parent / (output.name + ".tmp")
    if staging.exists():
        raise ValueError(f"refusing to overwrite existing staging directory: {staging}")
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(base_world, staging)
        for cached in staging.glob("SANDBOX_0_0_0_.sbsB*"):
            cached.unlink()
        for voxel in staging.glob("*.vx2"):
            if voxel.name != storage + ".vx2":
                voxel.unlink()
        ET.ElementTree(sector).write(staging / "SANDBOX_0_0_0_.sbs", encoding="utf-8", xml_declaration=True)
        ET.ElementTree(checkpoint).write(staging / "Sandbox.sbc", encoding="utf-8", xml_declaration=True)
        ET.ElementTree(config).write(staging / "Sandbox_config.sbc", encoding="utf-8", xml_declaration=True)
        staged_sector = ET.parse(staging / "SANDBOX_0_0_0_.sbs").getroot()
        staged_grids = common.find_grid_entities(staged_sector)
        if set(staged_grids) != keep_grids:
            raise ValueError("fresh stock-planet world does not contain exactly the copied assembly")
        _, output_size, output_hash = tree_receipt(staging)
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    receipt = {
        "schema": "voidwright.planetary-land-candidate-prep.v1",
        "instance": instance,
        "base_world_tree_sha256": base_hash,
        "base_world_files": base_count,
        "base_world_bytes": base_size,
        "design_world_tree_sha256": donor["source_tree_sha256"],
        "design_receipt_sha256": hashlib.sha256(donor_receipt.read_bytes()).hexdigest(),
        "output_path": str(output),
        "output_tree_sha256": output_hash,
        "output_sector_sha256": hashlib.sha256((output / "SANDBOX_0_0_0_.sbs").read_bytes()).hexdigest(),
        "output_bytes": output_size,
        "world_id": world_id,
        "planet_entity_id": int(_child(planet, "EntityId").text),
        "planet_storage": storage,
        "planet_generator": planet_generator,
        "candidate_position_rule": "planet maximum hill radius plus 2m; terrain patch and wheel support UNKNOWN",
        "chassis_grid_id": donor["_chassis_id"],
        "controller_id": donor["_controller_id"],
        "wheel_ids": donor["_wheel_ids"],
        "wheel_grid_ids": donor["_wheel_grid_ids"],
        "retained_vehicle_grid_ids": sorted(inserted_ids),
        "removed_competing_writer_blocks": removed_writers,
        "saved_velocities_and_wheel_overrides_reset": True,
        "ground_contact_status": "UNKNOWN_PENDING_LIVE_NO_COMMAND_BASELINE",
        "fresh_runtime_clearance_verified": False,
        "fresh_runtime_writer_inventory_verified": False,
        "fresh_runtime_zero_command_baseline_verified": False,
        "fresh_runtime_actuator_readbacks_verified": False,
        "fresh_runtime_stop_path_verified": False,
        "active_world_start": False,
        "actuator_commands_issued": False,
        "movement_authorized": False,
    }
    output.with_name(output.name + ".fixture.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _required(value: dict, key: str):
    if key not in value:
        raise ValueError(f"donor receipt is missing {key}")
    return value[key]


def _block_entity_id(block: ET.Element) -> int | None:
    node = common.child_map(block).get("EntityId")
    if node is None or not (node.text or "").strip().isdigit():
        return None
    return int(node.text)


def validate_donor(source: Path, donor_receipt: Path) -> tuple[dict, ET.Element, dict[int, ET.Element]]:
    data = json.loads(donor_receipt.read_text(encoding="utf-8"))
    if data.get("schema") != "voidwright.vehicle-design-donor.v1":
        raise ValueError("unsupported vehicle design receipt schema")
    _, _, source_hash = tree_receipt(source)
    if source_hash != _required(data, "source_tree_sha256"):
        raise ValueError("vehicle design world hash does not match its receipt")
    if data.get("ground_contact_status") != "UNKNOWN":
        raise ValueError("source design receipt must not claim ground contact for the fresh candidate")

    required = ("Sandbox.sbc", "Sandbox_config.sbc", "SANDBOX_0_0_0_.sbs")
    if any(not (source / name).is_file() for name in required):
        raise ValueError("donor is not a complete saved world")
    sector = ET.parse(source / "SANDBOX_0_0_0_.sbs").getroot()
    grids = common.find_grid_entities(sector)
    chassis_id = int(_required(data, "chassis_grid_id"))
    controller_id = int(_required(data, "controller_id"))
    wheel_ids = [int(value) for value in _required(data, "wheel_ids")]
    wheel_grid_ids = [int(value) for value in _required(data, "wheel_grid_ids")]
    if len(wheel_ids) != 4 or len(wheel_grid_ids) != 4 or len(set(wheel_ids)) != 4 or len(set(wheel_grid_ids)) != 4:
        raise ValueError("donor must bind four unique suspension blocks and four unique wheel grids")
    if chassis_id not in grids or any(grid_id not in grids for grid_id in wheel_grid_ids):
        raise ValueError("donor is missing the exact chassis or one of its four wheel subgrids")
    chassis_blocks = common.grid_blocks(grids[chassis_id])
    if controller_id not in [_block_entity_id(block) for block in chassis_blocks]:
        raise ValueError("bound controller is not a block on the chassis grid")
    suspensions = [b for b in chassis_blocks if b.attrib.get(XSI_TYPE) == "MyObjectBuilder_MotorSuspension"]
    if [common.entity_id(b) for b in suspensions] != wheel_ids:
        raise ValueError("suspension entity map differs from the donor receipt")
    top_ids = common.top_block_links(grids[chassis_id])
    top_by_grid: dict[int, int] = {}
    for grid_id in wheel_grid_ids:
        wheel_blocks = [b for b in common.grid_blocks(grids[grid_id])
                        if b.attrib.get(XSI_TYPE) == "MyObjectBuilder_Wheel"]
        if len(wheel_blocks) != 1:
            raise ValueError("each wheel subgrid must contain exactly one wheel block")
        top_by_grid[grid_id] = common.entity_id(wheel_blocks[0])
    if set(top_ids) != set(top_by_grid.values()) or len(top_ids) != 4:
        raise ValueError("suspension TopBlockId links do not bind exactly to the four wheel subgrids")

    data["_chassis_id"] = chassis_id
    data["_controller_id"] = controller_id
    data["_wheel_ids"] = wheel_ids
    data["_wheel_grid_ids"] = wheel_grid_ids
    return data, sector, grids


def prepare(base_world: Path, design_world: Path, donor_receipt: Path,
            output: Path, instance: str, planet_generator: str = "EarthLike") -> dict:
    return prepare_from_planet_base(base_world, design_world, donor_receipt, output, instance, planet_generator)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_world", type=Path, help="fresh stock planet scenario used as the world base")
    parser.add_argument("design_world", type=Path, help="saved world used only to extract the exact vehicle assembly")
    parser.add_argument("donor_receipt", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--planet-generator", default="EarthLike")
    args = parser.parse_args()
    if not args.instance or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in args.instance):
        raise SystemExit("invalid instance name")
    try:
        print(json.dumps(prepare(args.base_world, args.design_world, args.donor_receipt,
                                 args.output, args.instance, args.planet_generator), sort_keys=True))
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
