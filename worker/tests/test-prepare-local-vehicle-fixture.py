#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker import prepare_local_vehicle_fixture as fixture


def add_grid(parent: ET.Element, grid_id: int, name: str, blocks: list[ET.Element]) -> ET.Element:
    wrapper = ET.SubElement(parent, "MyObjectBuilder_EntityBase", {fixture.XSI_TYPE: "MyObjectBuilder_CubeGrid"})
    ET.SubElement(wrapper, "EntityId").text = str(grid_id)
    ET.SubElement(wrapper, "DisplayName").text = name
    blocks_node = ET.SubElement(wrapper, "CubeBlocks")
    for block in blocks:
        blocks_node.append(block)
    return wrapper


def block(kind: str, block_id: int, **values: str) -> ET.Element:
    item = ET.Element("MyObjectBuilder_CubeBlock", {fixture.XSI_TYPE: kind})
    ET.SubElement(item, "EntityId").text = str(block_id)
    for key, value in values.items():
        ET.SubElement(item, key).text = value
    return item


def write_world(root: Path) -> None:
    root.mkdir()
    checkpoint = ET.Element("MyObjectBuilder_Checkpoint")
    ET.SubElement(checkpoint, "SessionName").text = "stock"
    ET.SubElement(checkpoint, "WorldId").text = "1"
    ET.SubElement(checkpoint, "CameraController").text = "Entity"
    ET.SubElement(checkpoint, "CameraEntity").text = "999"
    ET.SubElement(checkpoint, "ControlledObject").text = "999"
    controlled = ET.SubElement(checkpoint, "ControlledEntities")
    ET.SubElement(controlled, "dictionary")
    identities = ET.SubElement(checkpoint, "Identities")
    identity = ET.SubElement(identities, "MyObjectBuilder_Identity")
    ET.SubElement(identity, "IdentityId").text = "1234"
    ET.SubElement(identity, "CharacterEntityId").text = "999"
    saved = ET.SubElement(identity, "SavedCharacters")
    ET.SubElement(saved, "long").text = "999"
    respawn = ET.SubElement(identity, "RespawnShips")
    ET.SubElement(respawn, "long").text = "777"
    players = ET.SubElement(checkpoint, "AllPlayersData")
    player_dict = ET.SubElement(players, "dictionary")
    player_item = ET.SubElement(player_dict, "item")
    player_value = ET.SubElement(player_item, "Value")
    ET.SubElement(player_value, "Connected").text = "true"
    ET.SubElement(player_value, "ForceRealPlayer").text = "true"
    camera_data = ET.SubElement(player_value, "EntityCameraData")
    ET.SubElement(camera_data, "CameraControllerSettings", {"EntityId": "999"})
    settings = ET.SubElement(checkpoint, "Settings")
    ET.SubElement(settings, "MaxDrones").text = "5"
    ET.SubElement(settings, "EnvironmentHostility").text = "NORMAL"
    ET.ElementTree(checkpoint).write(root / "Sandbox.sbc", encoding="utf-8", xml_declaration=True)
    config = ET.Element("MyObjectBuilder_WorldConfiguration")
    ET.SubElement(config, "SessionName").text = "stock"
    ET.SubElement(config, "Settings")
    ET.ElementTree(config).write(root / "Sandbox_config.sbc", encoding="utf-8", xml_declaration=True)

    sector = ET.Element("MyObjectBuilder_Sector")
    objects = ET.SubElement(sector, "SectorObjects")
    planet = ET.SubElement(objects, "MyObjectBuilder_EntityBase", {fixture.XSI_TYPE: "MyObjectBuilder_Planet"})
    ET.SubElement(planet, "EntityId").text = "7000000000000000000"
    ET.SubElement(planet, "StorageName").text = "Pertam-Test"

    gravity = block("MyObjectBuilder_GravityGenerator", fixture.GRAVITY_GENERATOR_ID,
                    Enabled="true", GravityAcceleration="3")
    timer = block("MyObjectBuilder_TimerBlock", 4444, Enabled="true")
    cockpit = block("MyObjectBuilder_Cockpit", 5555)
    add_grid(objects, fixture.PLATFORM_GRID_ID, "Landing Platform", [gravity, timer, cockpit])

    root_blocks = []
    top_ids = [9001, 9002, 9003, 9004]
    for block_id, top_id in zip(fixture.WHEEL_IDS, top_ids):
        root_blocks.append(block("MyObjectBuilder_MotorSuspension", block_id,
                                 TopBlockId=str(top_id), SteeringOverride="0.25",
                                 PropulsionOverride="0.4", SteerAngle="0.25", Enabled="true"))
    root_blocks.extend([
        block("MyObjectBuilder_Cockpit", fixture.CONTROLLER_ID),
        block("MyObjectBuilder_BatteryBlock", 8888, Enabled="false", ProducerEnabled="false"),
    ])
    add_grid(objects, fixture.ROVER_GRID_ID, "Hydro Scout Rover", root_blocks)
    for grid_id, top_id in zip(fixture.EXPECTED_WHEEL_GRID_IDS, top_ids):
        add_grid(objects, grid_id, "Wheel", [block("MyObjectBuilder_Wheel", top_id)])

    camera_source = block("MyObjectBuilder_CameraBlock", 6000, Enabled="true")
    add_grid(objects, fixture.DRONE_GRID_ID, "Camera Donor", [camera_source])
    add_grid(objects, 123456789, "Unrelated Patrol Drone", [block("MyObjectBuilder_RemoteControl", 123456790)])
    ET.ElementTree(sector).write(root / "SANDBOX_0_0_0_.sbs", encoding="utf-8", xml_declaration=True)
    (root / "SANDBOX_0_0_0_.sbsB5").write_bytes(b"stale-cache")
    (root / "Pertam-12345d60133.vx2").write_bytes(b"voxel")


class PrepareLocalVehicleFixtureTests(unittest.TestCase):
    def test_keeps_exact_assembly_and_platform_and_resets_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            output = base / "fresh"
            write_world(source)
            source_hash = fixture.tree_receipt(source)[2]
            with patch.object(fixture, "SOURCE_TREE_SHA256", source_hash):
                receipt = fixture.prepare(source, output, "land-local-test-unit")

            self.assertEqual(set(receipt["retained_grids"]), fixture.KEEP_GRID_IDS)
            saved_world_id = ET.parse(output / "Sandbox.sbc").getroot().findtext("WorldId")
            self.assertEqual(saved_world_id, receipt["world_id"])
            self.assertEqual(uuid.UUID(saved_world_id), uuid.uuid5(
                uuid.NAMESPACE_URL, "vragecage:local-vehicle-test:land-local-test-unit"))
            self.assertEqual(receipt["wheel_grid_ids_in_suspension_order"], fixture.EXPECTED_WHEEL_GRID_IDS)
            self.assertFalse(receipt["runtime_ground_contact_verified"])
            self.assertFalse(receipt["runtime_gravity_at_wheels_verified"])
            self.assertFalse(receipt["runtime_camera_forward_alignment_verified"])
            self.assertFalse(receipt["runtime_directional_clearance_verified"])
            self.assertFalse(receipt["runtime_chassis_clearance_verified"])
            self.assertFalse(receipt["runtime_full_assembly_writer_inventory_verified"])
            self.assertFalse(receipt["runtime_zero_command_baseline_verified"])
            self.assertFalse(receipt["runtime_actuator_readbacks_verified"])
            self.assertFalse(receipt["runtime_battery_state_verified"])
            self.assertFalse(receipt["runtime_gravity_generator_power_verified"])
            self.assertFalse(receipt["runtime_bounded_stop_path_verified"])
            self.assertTrue(receipt["saved_player_character_camera_references_reset"])
            self.assertFalse(receipt["inherited_connected_player_binding"])
            self.assertFalse((output / "SANDBOX_0_0_0_.sbsB5").exists())

            sector = ET.parse(output / "SANDBOX_0_0_0_.sbs").getroot()
            grids = fixture.find_grid_entities(sector)
            self.assertEqual(set(grids), fixture.KEEP_GRID_IDS)
            rover_blocks = fixture.grid_blocks(grids[fixture.ROVER_GRID_ID])
            wheel_blocks = [b for b in rover_blocks if b.attrib.get(fixture.XSI_TYPE) == "MyObjectBuilder_MotorSuspension"]
            self.assertEqual(len(wheel_blocks), 4)
            self.assertTrue(all(fixture.child_map(b)["PropulsionOverride"].text == "0" for b in wheel_blocks))
            self.assertTrue(all(fixture.child_map(b)["SteeringOverride"].text == "0" for b in wheel_blocks))
            self.assertTrue(any(fixture.child_map(b).get("CustomName") is not None and
                                fixture.child_map(b)["CustomName"].text == "LocalVehicleTestCamera"
                                for b in rover_blocks))
            platform_types = fixture.type_counts(grids[fixture.PLATFORM_GRID_ID])
            self.assertEqual(platform_types.get("MyObjectBuilder_GravityGenerator"), 1)
            self.assertNotIn("MyObjectBuilder_TimerBlock", platform_types)
            self.assertNotIn("MyObjectBuilder_MotorAdvancedStator", platform_types)

            checkpoint = ET.parse(output / "Sandbox.sbc").getroot()
            values = fixture.child_map(checkpoint)
            self.assertEqual(values["CameraEntity"].text, "0")
            self.assertEqual(values["ControlledObject"].text, "0")
            self.assertEqual(list(values["ControlledEntities"].find("dictionary")), [])
            self.assertEqual(fixture.child_map(values["Settings"])["MaxDrones"].text, "0")
            identity = next(node for node in checkpoint.iter()
                            if node.tag.rsplit("}", 1)[-1] == "MyObjectBuilder_Identity")
            identity_values = fixture.child_map(identity)
            self.assertEqual(identity_values["CharacterEntityId"].text, "0")
            self.assertEqual(list(identity_values["SavedCharacters"]), [])
            self.assertEqual(list(identity_values["RespawnShips"]), [])
            player_values = [node for node in checkpoint.iter()
                             if node.tag.rsplit("}", 1)[-1] in {"Connected", "ForceRealPlayer"}]
            self.assertEqual([node.text for node in player_values], ["false", "false"])
            self.assertFalse(any(node.tag.rsplit("}", 1)[-1] == "EntityCameraData"
                                 for node in checkpoint.iter()))

    def test_refuses_existing_output_and_wrong_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            output = base / "existing"
            write_world(source)
            output.mkdir()
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                fixture.prepare(source, output, "land-local-test-unit")
            output.rmdir()
            with self.assertRaisesRegex(ValueError, "not the reviewed stock Pertam"):
                fixture.prepare(source, output, "land-local-test-unit")
            self.assertFalse(output.exists())

    def test_rejects_missing_exact_wheel_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            output = base / "fresh"
            write_world(source)
            sector_path = source / "SANDBOX_0_0_0_.sbs"
            sector = ET.parse(sector_path).getroot()
            for parent in sector.iter():
                for child in list(parent):
                    if child.attrib.get(fixture.XSI_TYPE) == "MyObjectBuilder_CubeGrid" and \
                            fixture.entity_id(child) == fixture.EXPECTED_WHEEL_GRID_IDS[0]:
                        parent.remove(child)
            ET.ElementTree(sector).write(sector_path, encoding="utf-8", xml_declaration=True)
            source_hash = fixture.tree_receipt(source)[2]
            with patch.object(fixture, "SOURCE_TREE_SHA256", source_hash):
                with self.assertRaisesRegex(ValueError, "missing exact allowed grids"):
                    fixture.prepare(source, output, "land-local-test-unit")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
