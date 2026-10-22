#!/usr/bin/env python3
"""Add a camera and Agent Core to one disposable V4 Blue Truck clone only."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import secrets
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"
EXPECTED_SOURCE_SHA256 = "9ae4286ebb6fad94516612cfb083c347ccb197c62276d5658a49d65ede373f8f"
ALLOWED_SOURCE_TREES = {
    EXPECTED_SOURCE_SHA256: (21, 6447291),
    "b1564710681a6d0bc901ab0437d59f475f62c1fabda7fdd6bd9198306a5982ed": (5, 4546251),
}
CORE_SUBTYPE = "VoidwrightNavigationControllerSmall"
CAMERA_SUBTYPE = "SmallCameraBlock"
MIN_CLEARANCE_METERS = 0.05


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def world_tree_hash(root: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    files = 0
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise SystemExit(f"source contains an unsupported filesystem entry: {relative}")
        if path.is_dir():
            continue
        files += 1
        total += path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return files, total, digest.hexdigest()


def vadd(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(x + y for x, y in zip(a, b))


def vsub(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(x - y for x, y in zip(a, b))


def vmul(a: tuple[float, ...], scalar: float) -> tuple[float, ...]:
    return tuple(x * scalar for x in a)


def dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b))


def cross(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def normalized(value: tuple[float, ...]) -> tuple[float, ...]:
    length = math.sqrt(dot(value, value))
    if not math.isfinite(length) or length < 1e-9:
        raise SystemExit("fixture contains an invalid grid orientation")
    return vmul(value, 1.0 / length)


def xml_vec(element: ET.Element | None) -> tuple[float, float, float]:
    if element is None:
        raise SystemExit("fixture grid is missing an orientation vector")
    try:
        value = tuple(float(element.attrib[key]) for key in ("x", "y", "z"))
    except (KeyError, ValueError) as error:
        raise SystemExit("fixture grid has a malformed orientation vector") from error
    if not all(math.isfinite(item) for item in value):
        raise SystemExit("fixture grid has a non-finite orientation vector")
    return value  # type: ignore[return-value]


def direction(name: str) -> tuple[int, int, int]:
    values = {"Right": (1, 0, 0), "Left": (-1, 0, 0),
              "Up": (0, 1, 0), "Down": (0, -1, 0),
              "Backward": (0, 0, 1), "Forward": (0, 0, -1)}
    if name not in values:
        raise SystemExit(f"unsupported block orientation direction: {name}")
    return values[name]


def int_vec(element: ET.Element | None) -> tuple[int, int, int]:
    if element is None:
        raise SystemExit("fixture block is missing Min coordinates")
    try:
        return tuple(int(element.attrib[key]) for key in ("x", "y", "z"))  # type: ignore[return-value]
    except (KeyError, ValueError) as error:
        raise SystemExit("fixture block has malformed coordinates") from error


def cell_range(block: ET.Element) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    low = int_vec(block.find("Min"))
    high = int_vec(block.find("Max")) if block.find("Max") is not None else low
    if any(high[index] < low[index] for index in range(3)):
        raise SystemExit("fixture block has reversed Min/Max coordinates")
    return low, high


def range_cells(low: tuple[int, int, int], high: tuple[int, int, int]):
    for x in range(low[0], high[0] + 1):
        for y in range(low[1], high[1] + 1):
            for z in range(low[2], high[2] + 1):
                yield (x, y, z)


def box_gap(a_center, a_axes, a_half, b_center, b_axes, b_half) -> float:
    """Exact separating-axis margin for two oriented 3D boxes."""
    delta = vsub(a_center, b_center)
    axes = list(a_axes) + list(b_axes)
    for first in a_axes:
        for second in b_axes:
            axis = cross(first, second)
            if dot(axis, axis) > 1e-12:
                axes.append(normalized(axis))
    best = -math.inf
    for axis in axes:
        radius_a = sum(a_half[index] * abs(dot(axis, a_axes[index])) for index in range(3))
        radius_b = sum(b_half[index] * abs(dot(axis, b_axes[index])) for index in range(3))
        best = max(best, abs(dot(delta, axis)) - radius_a - radius_b)
    return best


def block_box(grid: dict, block: ET.Element) -> tuple[tuple[float, ...], tuple[float, ...]]:
    # Wheel/rotor/warhead mount builders in this V4 save can be serialized
    # without Min coordinates; the attached entity has its own CubeGrid and
    # world transform. Represent that mount conservatively around that grid's
    # origin instead of treating the whole fixture as malformed.
    if block.find("Min") is None:
        half = (grid["scale"],) * 3
        return grid["position"], half
    low, high = cell_range(block)
    local_center = tuple((low[index] + high[index]) / 2.0 * grid["scale"] for index in range(3))
    center = grid["position"]
    for axis, coordinate in zip(grid["axes"], local_center):
        center = vadd(center, vmul(axis, coordinate))
    half = tuple((high[index] - low[index] + 1) / 2.0 * grid["scale"] for index in range(3))
    return center, half


def make_grid(entity: ET.Element) -> dict:
    size_name = entity.findtext("GridSizeEnum")
    scale = {"Small": 0.5, "Large": 2.5}.get(size_name)
    if scale is None:
        raise SystemExit(f"unsupported grid size: {size_name}")
    transform = entity.find("PositionAndOrientation")
    if transform is None:
        raise SystemExit("fixture grid lacks PositionAndOrientation")
    position = xml_vec(transform.find("Position"))
    forward = normalized(xml_vec(transform.find("Forward")))
    up = normalized(xml_vec(transform.find("Up")))
    right = normalized(cross(forward, up))
    backward = normalized(cross(right, up))
    axes = (right, up, backward)
    blocks_parent = entity.find("CubeBlocks")
    blocks = list(blocks_parent) if blocks_parent is not None else []
    occupied = set()
    for block in blocks:
        if block.find("Min") is None:
            continue
        low, high = cell_range(block)
        occupied.update(range_cells(low, high))
    if not occupied:
        # A grid made entirely of attached mount builders has no serialized
        # cube-cell coordinates. Its separately serialized grid transform is
        # still available for the conservative obstacle envelope above.
        if not blocks:
            raise SystemExit("fixture grid has no serialized blocks")
        occupied.add((0, 0, 0))
    bounds = tuple((min(cell[index] for cell in occupied), max(cell[index] for cell in occupied))
                   for index in range(3))
    return {"entity": entity, "id": entity.findtext("EntityId"),
            "name": entity.findtext("DisplayName") or entity.findtext("Name") or "",
            "scale": scale, "position": position, "axes": axes,
            "blocks": blocks, "occupied": occupied, "bounds": bounds}


def cell_center(grid: dict, cell: tuple[int, int, int]) -> tuple[float, ...]:
    result = grid["position"]
    for axis, coordinate in zip(grid["axes"], cell):
        result = vadd(result, vmul(axis, coordinate * grid["scale"]))
    return result


def supported_surface_cells(grid: dict, outward: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    cells = grid["occupied"]
    axis = next(index for index, value in enumerate(outward) if value)
    bounds = grid["bounds"][axis]
    outer_coordinate = bounds[1] + 1 if outward[axis] > 0 else bounds[0] - 1
    result = []
    for cell in cells:
        candidate = tuple(cell[index] + outward[index] for index in range(3))
        if candidate not in cells and candidate[axis] == outer_coordinate:
            result.append(candidate)
    return sorted(set(result))


def block_id(block: ET.Element) -> str:
    return block.findtext("EntityId") or ""


def reused_placement_cells(prior: dict, *, expected_hash: str, sector_hash: str,
                           target_id: str, controller_id: str,
                           camera_cells: list[tuple[int, int, int]],
                           core_cells: list[tuple[int, int, int]]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if (prior.get("schema") != "vragecage.diagnostic-instrumentation.v1"
            or prior.get("source_tree_sha256") != expected_hash
            or prior.get("world_import_sha256") != expected_hash
            or prior.get("sector_before_sha256") != sector_hash
            or prior.get("instance") not in ("codex-v4-blue-truck-lifecycle-20261020-i",
                                               "codex-v4-blue-truck-lifecycle-20261020-j")):
        raise SystemExit("reused placement ledger is not bound to this exact pinned source sector")
    prior_blocks = prior.get("added_blocks", [])
    if len(prior_blocks) != 2:
        raise SystemExit("reused placement ledger must contain exactly the camera and Core placement")
    prior_core, prior_camera = prior_blocks
    camera_cell = tuple(prior_camera.get("cell", ()))
    core_cell = tuple(prior_core.get("cell", ()))
    if (prior_core.get("subtype") != CORE_SUBTYPE or prior_camera.get("subtype") != CAMERA_SUBTYPE
            or prior_core.get("grid_id") != target_id
            or prior_camera.get("grid_id") != target_id
            or prior_core.get("entity_id") == prior_camera.get("entity_id")
            or prior.get("controller", {}).get("entity_id") != controller_id
            or camera_cell not in camera_cells or core_cell not in core_cells
            or camera_cell == core_cell):
        raise SystemExit("reused placement ledger does not match this Blue Truck/controller geometry")
    return camera_cell, core_cell


def make_core_block(entity_id: str, cell: tuple[int, int, int], controller: ET.Element) -> ET.Element:
    """Serialize a small UpgradeModule with fields present on game-authored blocks."""
    core = ET.Element("MyObjectBuilder_CubeBlock", {XSI: "MyObjectBuilder_UpgradeModule"})
    ET.SubElement(core, "SubtypeName").text = CORE_SUBTYPE
    ET.SubElement(core, "EntityId").text = entity_id
    ET.SubElement(core, "Min", {key: str(value) for key, value in zip(("x", "y", "z"), cell)})
    ET.SubElement(core, "IntegrityPercent").text = "1"
    ET.SubElement(core, "BuildPercent").text = "1"
    orientation = controller.find("BlockOrientation")
    if orientation is None:
        raise SystemExit("Blue Truck controller lacks BlockOrientation for the Core builder")
    ET.SubElement(core, "BlockOrientation", dict(orientation.attrib))
    color = controller.find("ColorMaskHSV")
    if color is not None:
        ET.SubElement(core, "ColorMaskHSV", dict(color.attrib))
    for field in ("Owner", "BuiltBy", "ShareMode"):
        value = controller.findtext(field)
        if value:
            ET.SubElement(core, field).text = value
    ET.SubElement(core, "CustomName").text = "Voidwright Diagnostic Core"
    ET.SubElement(core, "ShowOnHUD").text = "false"
    ET.SubElement(core, "ShowInTerminal").text = "true"
    ET.SubElement(core, "ShowInToolbarConfig").text = "true"
    ET.SubElement(core, "ShowInInventory").text = "true"
    ET.SubElement(core, "Enabled").text = "true"
    return core


def set_builder_cell(block: ET.Element, cell: tuple[int, int, int]) -> None:
    minimum = block.find("Min")
    if minimum is None:
        minimum = ET.Element("Min")
    minimum.attrib.clear()
    for key, value in zip(("x", "y", "z"), cell):
        minimum.set(key, str(value))
    if minimum in list(block):
        block.remove(minimum)
    # Game-authored camera builders place the legacy Name field before Min;
    # generic cube blocks without Name place Min immediately after EntityId.
    children = list(block)
    name = block.find("Name")
    entity_id = block.find("EntityId")
    anchor = name if name is not None else entity_id
    insertion = children.index(anchor) + 1 if anchor is not None else len(children)
    block.insert(insertion, minimum)


def quarantine_binary_sector_cache(instance: Path, source_root: Path,
                                   backup_dir: Path) -> tuple[str | None, str | None]:
    cache = instance / "World/SANDBOX_0_0_0_.sbsB5"
    source_cache = source_root / "SANDBOX_0_0_0_.sbsB5"
    if cache.is_symlink():
        raise SystemExit("clone binary sector cache must not be a symbolic link")
    if not cache.exists():
        return None, None
    if not cache.is_file() or source_cache.is_symlink() or not source_cache.is_file():
        raise SystemExit("clone and pinned source binary sector caches must be regular files")
    digest = sha256_bytes(cache.read_bytes())
    if digest != sha256_bytes(source_cache.read_bytes()):
        raise SystemExit("clone binary sector cache differs from the pinned source")
    destination = backup_dir / cache.name
    if destination.exists() or destination.is_symlink():
        raise SystemExit("binary sector cache quarantine destination is not available")
    os.replace(cache, destination)
    return digest, str(destination.relative_to(instance))


def parse_clone(instance: Path, source_root: Path, expected_hash: str,
                reuse_ledger_path: Path | None = None) -> tuple[ET.ElementTree, bytes, dict, dict, list[dict]]:
    if instance.is_symlink() or not instance.is_dir():
        raise SystemExit("instrumentation target must be an existing non-symlink clone")
    instance = instance.resolve(strict=True)
    lab_path, import_path = instance / ".vragecage-lab.json", instance / ".vragecage-world-import.json"
    if lab_path.is_symlink() or import_path.is_symlink():
        raise SystemExit("clone authority receipts must not be symbolic links")
    try:
        lab = json.loads(lab_path.read_text(encoding="utf-8"))
        imported = json.loads(import_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"clone authority receipt is unavailable: {error}") from error
    if (lab.get("schema") != "vragecage.lab-authority.v1" or lab.get("authority") != "destructive-lab"
            or lab.get("disposable") is not True or lab.get("engine") != "magnetar"
            or lab.get("instance") != instance.name or imported.get("instance") != instance.name
            or imported.get("engine") != "magnetar" or lab.get("source_tree_sha256") != expected_hash
            or imported.get("tree_sha256") != expected_hash):
        raise SystemExit("clone is not a disposable Magnetar V4 clone of the pinned source")
    source_root = source_root.resolve(strict=True)
    files, size, source_hash = world_tree_hash(source_root)
    expected_receipt = ALLOWED_SOURCE_TREES.get(expected_hash)
    if expected_receipt is None or source_hash != expected_hash or (files, size) != expected_receipt:
        raise SystemExit("pinned Blue Truck baseline no longer matches its trusted import hash")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                       f"vragecage-{instance.name}.service"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("refusing to edit a running disposable clone")
    if (instance / ".vragecage-pending-command.json").exists():
        raise SystemExit("refusing to instrument a clone with a pending command")
    if (instance / ".vragecage-diagnostic-instrumentation.json").exists():
        raise SystemExit("diagnostic instrumentation ledger already exists")
    sector = instance / "World/SANDBOX_0_0_0_.sbs"
    source_sector = source_root / "SANDBOX_0_0_0_.sbs"
    if sector.is_symlink() or source_sector.is_symlink() or not sector.is_file() or not source_sector.is_file():
        raise SystemExit("clone and baseline sector files must be regular files")
    original = sector.read_bytes()
    if sha256_bytes(original) != sha256_bytes(source_sector.read_bytes()):
        raise SystemExit("fresh clone sector differs from the pinned source before instrumentation")
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.ElementTree(ET.fromstring(original, parser=parser))
    entities = [entity for entity in tree.getroot().iter("MyObjectBuilder_EntityBase")
                if entity.get(XSI) == "MyObjectBuilder_CubeGrid"]
    grids = [make_grid(entity) for entity in entities]
    trucks = [grid for grid in grids if grid["name"] == "Blue Truck"]
    if len(trucks) != 1:
        raise SystemExit(f"baseline must contain exactly one Blue Truck; found {len(trucks)}")
    target = trucks[0]
    controllers = [block for block in target["blocks"]
                   if block.get(XSI) in ("MyObjectBuilder_Cockpit", "MyObjectBuilder_RemoteControl")]
    if len(controllers) != 1:
        raise SystemExit(f"Blue Truck must have exactly one ship controller; found {len(controllers)}")
    if any(block.findtext("SubtypeName") in (CORE_SUBTYPE, "VoidwrightNavigationControllerLarge")
           for block in target["blocks"]):
        raise SystemExit("Blue Truck already contains an Agent Core")
    if any(block.get(XSI) == "MyObjectBuilder_CameraBlock" for block in target["blocks"]):
        raise SystemExit("Blue Truck already contains a camera; refusing ambiguous instrumentation")
    camera_templates = [block for grid in grids for block in grid["blocks"]
                        if block.get(XSI) == "MyObjectBuilder_CameraBlock"
                        and block.findtext("SubtypeName") == CAMERA_SUBTYPE]
    if not camera_templates:
        raise SystemExit("pinned world has no compatible camera object-builder template")
    camera_template = copy.deepcopy(camera_templates[0])
    controller_orientation = controllers[0].find("BlockOrientation")
    if controller_orientation is None:
        raise SystemExit("Blue Truck controller lacks a block orientation")
    camera_forward = direction(controller_orientation.get("Forward", ""))
    camera_up = direction(controller_orientation.get("Up", ""))
    if dot(camera_forward, camera_up) != 0:
        raise SystemExit("Blue Truck controller has an invalid forward/up orientation")
    # Core location is the controller's up face; camera location is its forward face.
    camera_cells = supported_surface_cells(target, camera_forward)
    core_cells = supported_surface_cells(target, camera_up)
    if not camera_cells or not core_cells:
        raise SystemExit("Blue Truck has no supported free cell on its forward and upper faces")
    all_ids = {text for text in (element.text for element in tree.getroot().iter("EntityId"))
               if text and text.isdigit()}
    existing_boxes = []
    for grid in grids:
        if grid is target:
            continue
        for block in grid["blocks"]:
            center, half = block_box(grid, block)
            existing_boxes.append((center, grid["axes"], half, grid["name"], grid["id"], block_id(block)))
    candidate_half = (target["scale"] / 2.0,) * 3

    def clearance(cell: tuple[int, int, int]) -> tuple[float, dict | None]:
        center = cell_center(target, cell)
        minimum = math.inf
        nearest = None
        for other_center, other_axes, other_half, grid_name, grid_id, entity_id in existing_boxes:
            gap = box_gap(center, target["axes"], candidate_half,
                          other_center, other_axes, other_half)
            if gap < minimum:
                minimum = gap
                nearest = {"grid": grid_name, "grid_id": grid_id, "block_id": entity_id,
                           "separating_axis_margin_m": round(gap, 6)}
        return minimum, nearest

    if reuse_ledger_path is not None:
        if reuse_ledger_path.is_symlink() or not reuse_ledger_path.is_file():
            raise SystemExit("reused placement ledger must be a regular file")
        try:
            prior = json.loads(reuse_ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"reused placement ledger is unavailable: {error}") from error
        camera_cell, core_cell = reused_placement_cells(
            prior, expected_hash=expected_hash, sector_hash=sha256_bytes(original),
            target_id=target["id"], controller_id=block_id(controllers[0]),
            camera_cells=camera_cells, core_cells=core_cells)
        camera_gap, camera_nearest = clearance(camera_cell)
        core_gap, core_nearest = clearance(core_cell)
        pair_gap = box_gap(cell_center(target, camera_cell), target["axes"], candidate_half,
                           cell_center(target, core_cell), target["axes"], candidate_half)
        if min(camera_gap, core_gap, pair_gap) < MIN_CLEARANCE_METERS:
            raise SystemExit("reused camera/Core placement failed fresh offline clearance validation")
        best = (min(camera_gap, core_gap, pair_gap), camera_cell, core_cell,
                camera_gap, camera_nearest, core_gap, core_nearest, pair_gap)
    else:
        placements = []
        for camera_cell in camera_cells:
            camera_gap, camera_nearest = clearance(camera_cell)
            if camera_gap < MIN_CLEARANCE_METERS:
                continue
            for core_cell in core_cells:
                if core_cell == camera_cell:
                    continue
                core_gap, core_nearest = clearance(core_cell)
                pair_gap = box_gap(cell_center(target, camera_cell), target["axes"], candidate_half,
                                   cell_center(target, core_cell), target["axes"], candidate_half)
                if core_gap < MIN_CLEARANCE_METERS or pair_gap < MIN_CLEARANCE_METERS:
                    continue
                placements.append((min(camera_gap, core_gap, pair_gap), camera_cell, core_cell,
                                   camera_gap, camera_nearest, core_gap, core_nearest, pair_gap))
        if not placements:
            raise SystemExit("no supported camera/Core cell pair clears every existing world block by 5 cm")
        placements.sort(key=lambda item: (item[0], item[3], item[5]), reverse=True)
        best = placements[0]
    grid = target["entity"]
    blocks_parent = grid.find("CubeBlocks")
    if blocks_parent is None:
        raise SystemExit("Blue Truck is missing its CubeBlocks element")
    grid_size = len(target["blocks"])
    core_number_in_grid = 1 + sum(
        block.findtext("SubtypeName") == CORE_SUBTYPE for block in target["blocks"]
    )
    camera_number_in_grid = 1 + sum(
        block.findtext("SubtypeName") == CAMERA_SUBTYPE for block in target["blocks"]
    )
    if grid.find("NextBlockId") is not None:
        next_id_node = grid.find("NextBlockId")
        try:
            next_id = int(next_id_node.text or "0")
        except ValueError as error:
            raise SystemExit("Blue Truck NextBlockId is malformed") from error
    else:
        next_id_node = None
        next_id = grid_size + 1
    next_id_before = next_id_node.text if next_id_node is not None else None
    new_ids = []
    while len(new_ids) < 2:
        # Space Engineers object IDs in the pinned V4 and working V2 fixtures
        # occupy [2^56, 2^57); the adjacent high range contains owner identities.
        value = str((1 << 56) + secrets.randbelow((1 << 56) - 1))
        if value not in all_ids and value not in new_ids:
            new_ids.append(value)
    camera_cell, core_cell = best[1], best[2]

    def set_orientation(block: ET.Element) -> None:
        orientation = block.find("BlockOrientation")
        if orientation is None:
            orientation = ET.Element("BlockOrientation")
            children = list(block)
            index = next((i for i, child in enumerate(children) if child.tag in ("ColorMaskHSV", "SkinSubtypeId", "Owner")), len(children))
            block.insert(index, orientation)
        orientation.attrib.clear()
        orientation.set("Forward", controller_orientation.get("Forward", ""))
        orientation.set("Up", controller_orientation.get("Up", ""))

    camera = camera_template
    camera.set(XSI, "MyObjectBuilder_CameraBlock")
    camera.find("SubtypeName").text = CAMERA_SUBTYPE  # type: ignore[union-attr]
    camera.find("EntityId").text = new_ids[0]  # type: ignore[union-attr]
    if camera.find("Name") is None:
        ET.SubElement(camera, "Name").text = new_ids[0]
    else:
        camera.find("Name").text = new_ids[0]  # type: ignore[union-attr]
    set_builder_cell(camera, camera_cell)
    set_orientation(camera)
    custom_name = camera.find("CustomName")
    if custom_name is None:
        custom_name = ET.SubElement(camera, "CustomName")
    custom_name.text = "Voidwright Diagnostic Camera"
    for field in ("Owner", "BuiltBy"):
        source_value = controllers[0].findtext(field)
        if source_value:
            node = camera.find(field)
            if node is None:
                node = ET.SubElement(camera, field)
            node.text = source_value

    core = make_core_block(new_ids[1], core_cell, controllers[0])
    camera_number = camera.find("NumberInGrid")
    if camera_number is None:
        camera_number = ET.SubElement(camera, "NumberInGrid")
    camera_number.text = str(camera_number_in_grid)
    if camera.find("Enabled") is None:
        ET.SubElement(camera, "Enabled").text = "true"
    else:
        camera.find("Enabled").text = "true"  # type: ignore[union-attr]
    blocks_parent.append(core)
    blocks_parent.append(camera)
    if next_id_node is not None:
        next_id_node.text = str(next_id + 2)

    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    output = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)
    # Verify removing just the two inserted objects restores the original parsed tree.
    verify_parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    verify_root = ET.fromstring(output, parser=verify_parser)
    verify_grid = next(entity for entity in verify_root.iter("MyObjectBuilder_EntityBase")
                       if entity.get(XSI) == "MyObjectBuilder_CubeGrid"
                       and entity.findtext("EntityId") == target["id"])
    verify_blocks = verify_grid.find("CubeBlocks")
    assert verify_blocks is not None
    found = {element.findtext("EntityId"): element for element in list(verify_blocks)
             if element.findtext("EntityId") in new_ids}
    if len(found) != 2:
        raise SystemExit("instrumented block verification failed")
    for element_id, subtype in ((new_ids[0], CAMERA_SUBTYPE), (new_ids[1], CORE_SUBTYPE)):
        if found[element_id].findtext("SubtypeName") != subtype:
            raise SystemExit("instrumented block subtype verification failed")
    for element_id in new_ids:
        verify_blocks.remove(found[element_id])
    if next_id_before is not None:
        restored_next_id = verify_grid.find("NextBlockId")
        if restored_next_id is None:
            raise SystemExit("instrumentation verification lost NextBlockId")
        restored_next_id.text = next_id_before
    canonical_before = ET.tostring(ET.fromstring(original, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))))
    canonical_after = ET.tostring(verify_root)
    if canonical_before != canonical_after:
        raise SystemExit("instrumentation changed source XML beyond the two intended blocks")

    return tree, original, lab, imported, grids, {
        "grid_id": target["id"], "grid_name": target["name"],
        "controller_id": block_id(controllers[0]),
        "controller_type": controllers[0].get(XSI),
        "controller_subtype": controllers[0].findtext("SubtypeName") or "",
        "controller_orientation": {"Forward": controller_orientation.get("Forward"),
                                   "Up": controller_orientation.get("Up")},
        "camera_id": new_ids[0], "camera_cell": camera_cell,
        "camera_world_position_m": tuple(round(value, 6) for value in cell_center(target, camera_cell)),
        "camera_nearest_obstacle": best[4], "camera_min_separation_m": round(best[3], 6),
        "core_id": new_ids[1], "core_cell": core_cell,
        "core_world_position_m": tuple(round(value, 6) for value in cell_center(target, core_cell)),
        "core_nearest_obstacle": best[6], "core_min_separation_m": round(best[5], 6),
        "instrument_pair_min_separation_m": round(best[7], 6),
        "positioning_geometry": "OBB separating-axis collision check against every block in every other grid",
        "min_clearance_m": MIN_CLEARANCE_METERS,
        "grid_size_m": target["scale"],
        "existing_blue_truck_blocks": len(target["blocks"]),
        "core_number_in_grid": core_number_in_grid,
        "camera_number_in_grid": camera_number_in_grid,
        "next_block_id_before": next_id_before,
        "next_block_id_after": str(next_id + 2) if next_id_before is not None else None,
        "existing_blue_truck_suspensions": [block_id(block) for block in target["blocks"]
                                             if block.get(XSI) == "MyObjectBuilder_MotorSuspension"],
    }


def apply_edit(instance: Path, original: bytes, output: bytes, lab: dict, imported: dict,
               source_root: Path, expected_hash: str, plan: dict) -> dict:
    edit_id = secrets.token_hex(16)
    backup_dir = instance / "Quarantine" / ("diagnostic-instrumentation-" + edit_id)
    if backup_dir.is_symlink() or backup_dir.exists():
        raise SystemExit("diagnostic instrumentation backup path is not available")
    backup_dir.mkdir(mode=0o700, parents=True)
    backup = backup_dir / "SANDBOX_0_0_0_.sbs.before"
    with backup.open("xb") as stream:
        os.chmod(backup, 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    sector = instance / "World/SANDBOX_0_0_0_.sbs"
    if sha256_bytes(sector.read_bytes()) != sha256_bytes(original):
        raise SystemExit("clone sector changed during instrumentation preparation")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".diagnostic-instrumentation-", dir=sector.parent)
    binary_cache_sha256 = None
    binary_cache_quarantine = None
    try:
        binary_cache_sha256, binary_cache_quarantine = quarantine_binary_sector_cache(
            instance, source_root, backup_dir)
        os.fchmod(descriptor, sector.stat().st_mode & 0o777)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, sector)
    except Exception:
        if binary_cache_quarantine is not None:
            os.replace(instance / binary_cache_quarantine, sector.with_name(sector.name + "B5"))
        elif binary_cache_sha256 is not None:
            os.replace(backup_dir / "SANDBOX_0_0_0_.sbsB5", sector.with_name(sector.name + "B5"))
        raise
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    ledger = {
        "schema": "vragecage.diagnostic-instrumentation.v1",
        "instance": instance.name,
        "authority": "disposable-clone-only",
        "fixture_id": lab.get("fixture_id"),
        "source_tree_sha256": expected_hash,
        "source_root_read_only": str(source_root.resolve(strict=True)),
        "world_import_sha256": imported.get("tree_sha256"),
        "edit_id": edit_id,
        "action": "add-one-agent-core-and-one-same-grid-diagnostic-camera",
        "sector_before_sha256": sha256_bytes(original),
        "sector_after_sha256": sha256_bytes(output),
        "binary_cache_sha256": binary_cache_sha256,
        "binary_cache_quarantine": binary_cache_quarantine,
        "backup_relative_path": backup.relative_to(instance).as_posix(),
        "backup_sha256": sha256_bytes(original),
        "next_block_id_before": plan["next_block_id_before"],
        "next_block_id_after": plan["next_block_id_after"],
        "added_blocks": [
            {"entity_id": plan["core_id"], "subtype": CORE_SUBTYPE,
             "grid_id": plan["grid_id"], "grid_name": plan["grid_name"],
             "cell": plan["core_cell"], "world_position_m": plan["core_world_position_m"],
             "number_in_grid": plan["core_number_in_grid"]},
            {"entity_id": plan["camera_id"], "subtype": CAMERA_SUBTYPE,
             "grid_id": plan["grid_id"], "grid_name": plan["grid_name"],
             "cell": plan["camera_cell"], "world_position_m": plan["camera_world_position_m"],
             "aligned_with_controller": plan["controller_orientation"],
             "number_in_grid": plan["camera_number_in_grid"]},
        ],
        "controller": {"entity_id": plan["controller_id"], "type": plan["controller_type"],
                       "subtype": plan["controller_subtype"]},
        "existing_suspension_ids": plan["existing_blue_truck_suspensions"],
        "geometry": {"method": plan["positioning_geometry"],
                     "minimum_clearance_m": plan["min_clearance_m"],
                     "camera_min_separation_m": plan["camera_min_separation_m"],
                     "core_min_separation_m": plan["core_min_separation_m"],
                     "instrument_pair_min_separation_m": plan["instrument_pair_min_separation_m"],
                     "camera_nearest_obstacle": plan["camera_nearest_obstacle"],
                     "core_nearest_obstacle": plan["core_nearest_obstacle"]},
    }
    ledger_path = instance / ".vragecage-diagnostic-instrumentation.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".diagnostic-ledger-", dir=instance)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(ledger, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, ledger_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--reuse-placement-ledger", type=Path,
                        help="reuse cells from a hash-bound prior ledger, with fresh clearance validation")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="read-only geometry plan")
    mode.add_argument("--apply", action="store_true", help="edit this disposable clone and record a rollback ledger")
    args = parser.parse_args()
    if args.expected_source_sha256 not in ALLOWED_SOURCE_TREES:
        raise SystemExit("instrumentation helper is pinned to the reviewed V4 baseline hash")
    tree, original, lab, imported, _grids, plan = parse_clone(
        args.instance, args.source_root, args.expected_source_sha256, args.reuse_placement_ledger)
    if args.plan:
        print(json.dumps({"status": "plan-ready", **plan}, sort_keys=True, separators=(",", ":")))
        return 0
    output = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)
    ledger = apply_edit(args.instance.resolve(strict=True), original, output, lab, imported,
                        args.source_root, args.expected_source_sha256, plan)
    print(json.dumps({"status": "instrumented", "ledger": str(args.instance.resolve(strict=True) / ".vragecage-diagnostic-instrumentation.json"),
                      "ledger_sha256": sha256_bytes((args.instance.resolve(strict=True) / ".vragecage-diagnostic-instrumentation.json").read_bytes()),
                      "edit_id": ledger["edit_id"], **plan}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
