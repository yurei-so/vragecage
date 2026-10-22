#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker import prepare_grounded_land_fixture as fixture


def add_block(parent: ET.Element, kind: str, entity_id: int, **values: str) -> None:
    block = ET.SubElement(parent, "MyObjectBuilder_CubeBlock", {fixture.XSI_TYPE: kind})
    ET.SubElement(block, "EntityId").text = str(entity_id)
    for key, value in values.items():
        ET.SubElement(block, key).text = value


def add_grid(parent: ET.Element, grid_id: int, name: str, blocks: list[tuple[str, int, dict[str, str]]]) -> None:
    wrapper = ET.SubElement(parent, "MyObjectBuilder_EntityBase", {fixture.XSI_TYPE: "MyObjectBuilder_CubeGrid"})
    ET.SubElement(wrapper, "EntityId").text = str(grid_id)
    ET.SubElement(wrapper, "DisplayName").text = name
    ET.SubElement(wrapper, "LinearVelocity", {"x": "2", "y": "0", "z": "0"})
    ET.SubElement(wrapper, "AngularVelocity", {"x": "0", "y": "1", "z": "0"})
    pose = ET.SubElement(wrapper, "PositionAndOrientation")
    ET.SubElement(pose, "Position", {"x": "2", "y": "3", "z": "4"})
    ET.SubElement(pose, "Forward", {"x": "0", "y": "0", "z": "-1"})
    ET.SubElement(pose, "Up", {"x": "0", "y": "1", "z": "0"})
    orientation = ET.SubElement(pose, "Orientation")
    for key, value in zip(("X", "Y", "Z", "W"), ("0", "0", "0", "1")):
        ET.SubElement(orientation, key).text = value
    container = ET.SubElement(wrapper, "CubeBlocks")
    for kind, entity_id, values in blocks:
        add_block(container, kind, entity_id, **values)


def create_donor(root: Path) -> tuple[Path, Path]:
    root.mkdir()
    checkpoint = ET.Element("MyObjectBuilder_Checkpoint")
    ET.SubElement(checkpoint, "SessionName").text = "Grounded donor"
    ET.SubElement(checkpoint, "WorldId").text = "1"
    ET.SubElement(checkpoint, "CameraEntity").text = "99"
    ET.SubElement(checkpoint, "ControlledObject").text = "99"
    ET.ElementTree(checkpoint).write(root / "Sandbox.sbc", encoding="utf-8", xml_declaration=True)
    config = ET.Element("MyObjectBuilder_WorldConfiguration")
    ET.SubElement(config, "SessionName").text = "Grounded donor"
    ET.SubElement(config, "Settings")
    ET.ElementTree(config).write(root / "Sandbox_config.sbc", encoding="utf-8", xml_declaration=True)
    sector = ET.Element("MyObjectBuilder_Sector")
    objects = ET.SubElement(sector, "SectorObjects")
    planet = ET.SubElement(objects, "MyObjectBuilder_EntityBase", {fixture.XSI_TYPE: "MyObjectBuilder_Planet"})
    ET.SubElement(planet, "EntityId").text = "900000000000000001"
    ET.SubElement(planet, "StorageName").text = "EarthLikeFixture"
    ET.SubElement(planet, "PlanetGenerator").text = "EarthLike"
    ET.SubElement(planet, "MaximumHillRadius").text = "100"
    planet_pose = ET.SubElement(planet, "PositionAndOrientation")
    ET.SubElement(planet_pose, "Position", {"x": "10", "y": "20", "z": "30"})
    ET.SubElement(planet_pose, "Forward", {"x": "0", "y": "0", "z": "-1"})
    ET.SubElement(planet_pose, "Up", {"x": "0", "y": "1", "z": "0"})
    orientation = ET.SubElement(planet_pose, "Orientation")
    for key, value in zip(("X", "Y", "Z", "W"), ("0", "0", "0", "1")):
        ET.SubElement(orientation, key).text = value
    add_grid(objects, 100, "Rover", [
        ("MyObjectBuilder_MotorSuspension", 201, {"TopBlockId": "301", "PropulsionOverride": "0.2", "SteeringOverride": "0.1", "SteerAngle": "0.1", "Brake": "true"}),
        ("MyObjectBuilder_MotorSuspension", 202, {"TopBlockId": "302", "PropulsionOverride": "0.2", "SteeringOverride": "0.1", "SteerAngle": "0.1", "Brake": "true"}),
        ("MyObjectBuilder_MotorSuspension", 203, {"TopBlockId": "303", "PropulsionOverride": "0.2", "SteeringOverride": "0.1", "SteerAngle": "0.1", "Brake": "true"}),
        ("MyObjectBuilder_MotorSuspension", 204, {"TopBlockId": "304", "PropulsionOverride": "0.2", "SteeringOverride": "0.1", "SteerAngle": "0.1", "Brake": "true"}),
        ("MyObjectBuilder_Cockpit", 205, {}),
        ("MyObjectBuilder_CameraBlock", 206, {}),
        ("MyObjectBuilder_RemoteControl", 207, {}),
    ])
    for index, grid_id in enumerate((110, 120, 130, 140), 1):
        add_grid(objects, grid_id, "Wheel", [("MyObjectBuilder_Wheel", 300 + index, {})])
    add_grid(objects, 777, "Unrelated", [("MyObjectBuilder_RemoteControl", 778, {})])
    ET.ElementTree(sector).write(root / "SANDBOX_0_0_0_.sbs", encoding="utf-8", xml_declaration=True)
    (root / "EarthLikeFixture.vx2").write_bytes(b"test-ground-storage")
    (root / "unrelated.vx2").write_bytes(b"must-be-removed")
    return root, root.parent / "donor.json"


def create_stock_planet_base(root: Path) -> Path:
    world, _ = create_donor(root)
    sector_path = world / "SANDBOX_0_0_0_.sbs"
    sector = ET.parse(sector_path).getroot()
    for parent in sector.iter():
        for entity in list(parent):
            if entity.tag.rsplit("}", 1)[-1] == "MyObjectBuilder_EntityBase" and \
                    entity.attrib.get(fixture.XSI_TYPE) == "MyObjectBuilder_CubeGrid":
                parent.remove(entity)
            elif entity.tag.rsplit("}", 1)[-1] == "MyObjectBuilder_EntityBase" and \
                    entity.attrib.get(fixture.XSI_TYPE) == "MyObjectBuilder_Planet":
                values = fixture.common.child_map(entity)
                values["EntityId"].text = "900000000000000002"
                values["StorageName"].text = "BasePlanet"
                values["PlanetGenerator"].text = "EarthLike"
    ET.ElementTree(sector).write(sector_path, encoding="utf-8", xml_declaration=True)
    (world / "EarthLikeFixture.vx2").rename(world / "BasePlanet.vx2")
    (world / "unrelated.vx2").unlink()
    return world


class PrepareGroundedLandFixtureTests(unittest.TestCase):
    def write_receipt(self, source: Path, receipt: Path) -> None:
        _, _, source_hash = fixture.tree_receipt(source)
        data = {
            "schema": "voidwright.vehicle-design-donor.v1",
            "source_tree_sha256": source_hash,
            "chassis_grid_id": 100,
            "controller_id": 205,
            "wheel_ids": [201, 202, 203, 204],
            "wheel_grid_ids": [110, 120, 130, 140],
            "ground_contact_status": "UNKNOWN",
        }
        receipt.write_text(json.dumps(data), encoding="utf-8")

    def test_builds_new_identity_and_keeps_only_planet_and_exact_wheel_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, donor_receipt = create_donor(base / "source")
            self.write_receipt(source, donor_receipt)
            planet_base = create_stock_planet_base(base / "stock-base")
            output = base / "fresh"
            result = fixture.prepare(planet_base, source, donor_receipt, output, "land-candidate-unit")
            self.assertFalse(result["active_world_start"])
            self.assertFalse(result["movement_authorized"])
            self.assertEqual(result["ground_contact_status"], "UNKNOWN_PENDING_LIVE_NO_COMMAND_BASELINE")
            self.assertEqual(result["planet_entity_id"], 900000000000000002)
            self.assertEqual(result["planet_storage"], "BasePlanet")
            self.assertEqual(result["removed_competing_writer_blocks"][0]["block_type"],
                             "MyObjectBuilder_RemoteControl")
            self.assertEqual([p.name for p in output.glob("*.vx2")], ["BasePlanet.vx2"])
            self.assertNotEqual(result["world_id"], "1")
            sector = ET.parse(output / "SANDBOX_0_0_0_.sbs").getroot()
            self.assertEqual(set(fixture.common.find_grid_entities(sector)), {100, 110, 120, 130, 140})
            chassis = fixture.common.find_grid_entities(sector)[100]
            self.assertEqual(fixture.common.child_map(chassis)["LinearVelocity"].attrib["x"], "0")
            self.assertEqual(fixture.common.child_map(chassis)["AngularVelocity"].attrib["y"], "0")
            for suspension in fixture.common.grid_blocks(chassis)[:4]:
                values = fixture.common.child_map(suspension)
                self.assertEqual(values["PropulsionOverride"].text, "0")
                self.assertEqual(values["SteeringOverride"].text, "0")
                self.assertEqual(values["SteerAngle"].text, "0")
                self.assertEqual(values["Brake"].text, "false")

    def test_candidate_keeps_contact_unknown_and_requires_live_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, donor_receipt = create_donor(base / "source")
            self.write_receipt(source, donor_receipt)
            planet_base = create_stock_planet_base(base / "stock-base")
            result = fixture.prepare(planet_base, source, donor_receipt, base / "fresh", "land-candidate-unit")
            self.assertEqual(result["ground_contact_status"], "UNKNOWN_PENDING_LIVE_NO_COMMAND_BASELINE")
            self.assertFalse(result["movement_authorized"])

    def test_refuses_stale_donor_hash_and_ground_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, donor_receipt = create_donor(base / "source")
            self.write_receipt(source, donor_receipt)
            data = json.loads(donor_receipt.read_text())
            data["ground_contact_status"] = "VERIFIED"
            donor_receipt.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "must not claim ground contact"):
                fixture.prepare(create_stock_planet_base(base / "stock-base"), source, donor_receipt,
                                base / "fresh", "land-candidate-unit")

    def test_refuses_design_source_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, donor_receipt = create_donor(base / "source")
            self.write_receipt(source, donor_receipt)
            (source / "EarthLikeFixture.vx2").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "does not match its receipt"):
                fixture.prepare(create_stock_planet_base(base / "stock-base"), source, donor_receipt,
                                base / "fresh", "land-candidate-unit")


if __name__ == "__main__":
    unittest.main()
