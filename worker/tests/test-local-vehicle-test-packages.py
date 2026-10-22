#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "local_vehicle_test_packages.py"
SPEC = importlib.util.spec_from_file_location("local_vehicle_test_packages", MODULE)
PACKAGES = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PACKAGES)


class LocalVehicleTestPackageTests(unittest.TestCase):
    def test_script_bearing_mods_require_script_markers(self) -> None:
        self.assertEqual(PACKAGES.SCRIPT_REQUIRED_MODS,
                         set(PACKAGES.EXPECTED_MODS) - {"1090562767"})
        for mod_id in PACKAGES.SCRIPT_REQUIRED_MODS:
            self.assertFalse(PACKAGES.loaded_marker_valid(mod_id, True, False))
            self.assertTrue(PACKAGES.loaded_marker_valid(mod_id, False, True))
        self.assertTrue(PACKAGES.loaded_marker_valid("1090562767", True, False))
        self.assertFalse(PACKAGES.loaded_marker_valid("1090562767", False, True))
        self.assertFalse(PACKAGES.loaded_marker_valid("unknown", True, True))

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.instance = self.root / "instances" / PACKAGES.INSTANCE
        self.worker = self.root
        self.instance.mkdir(parents=True)
        (self.instance / "World").mkdir()
        cache = self.worker / "magnetar-steam/steamapps/workshop/content/244850"
        self.mod_hashes = {}
        mods = ET.Element("Sandbox")
        mods_node = ET.SubElement(mods, "Mods")
        for mod_id in PACKAGES.EXPECTED_MODS:
            ET.SubElement(ET.SubElement(mods_node, "ModItem"), "PublishedFileId").text = mod_id
            package = cache / mod_id
            package.mkdir(parents=True)
            (package / "payload.bin").write_bytes(("mod-" + mod_id).encode())
            self.mod_hashes[mod_id] = PACKAGES.tree_sha256(package)[2]
        (self.instance / "World/Sandbox.sbc").write_bytes(ET.tostring(mods))
        self.old_mod_hashes = PACKAGES.EXPECTED_MODS.copy()
        self.old_source_hash = PACKAGES.SOURCE_TREE_SHA256
        self.old_voidwright_hash = PACKAGES.VOIDWRIGHT_MOD_TREE_SHA256
        self.old_plugin_hash = PACKAGES.SERVER_PLUGIN_TREE_SHA256
        self.old_operator_hashes = (PACKAGES.LOCAL_OPERATOR_PACKAGE_SHA256,
                                    PACKAGES.LOCAL_OPERATOR_ASSEMBLY_SHA256,
                                    PACKAGES.LOCAL_OPERATOR_SOURCE_SHA256)
        PACKAGES.EXPECTED_MODS.clear()
        PACKAGES.EXPECTED_MODS.update(self.mod_hashes)
        PACKAGES.SOURCE_TREE_SHA256 = "f" * 64
        PACKAGES.VOIDWRIGHT_MOD_TREE_SHA256 = "e" * 64
        PACKAGES.SERVER_PLUGIN_TREE_SHA256 = "d" * 64
        self.camera_id = PACKAGES.EXPECTED_CAMERA_ID
        self.core_id = 90000000000000002
        self.write_instrumentation()
        packages = PACKAGES.published_mod_receipts(self.instance / "World", self.worker)
        (self.instance / ".vragecage-world-import.json").write_text(json.dumps({
            "instance": PACKAGES.INSTANCE,
            "tree_sha256": PACKAGES.SOURCE_TREE_SHA256,
            "published_mod_packages": packages,
        }))
        self.voidwright_cache = cache / "90127687454063603"
        (self.voidwright_cache / "Data").mkdir(parents=True)
        (self.voidwright_cache / "Data/mod.sbc").write_text("voidwright")
        void_hash = PACKAGES.registrar_tree_sha256(self.voidwright_cache)[2]
        PACKAGES.VOIDWRIGHT_MOD_TREE_SHA256 = void_hash
        (self.instance / ".vragecage-mod-stage.json").write_text(json.dumps({
            "instance": PACKAGES.INSTANCE, "name": "Voidwright",
            "synthetic_workshop_id": 90127687454063603,
            "path": str(self.voidwright_cache), "tree_sha256": void_hash,
        }))
        self.plugin_path = self.instance / "Plugins/voidwright-server-plugin"
        self.plugin_path.mkdir(parents=True)
        (self.plugin_path / "manifest.xml").write_text("plugin")
        plugin_hash = PACKAGES.registrar_tree_sha256(self.plugin_path)[2]
        PACKAGES.SERVER_PLUGIN_TREE_SHA256 = plugin_hash
        self.write_plugin_stage()
        self.operator_path = self.worker / "incoming/q-camera-binding-20261021/AgentMcp"
        source_path = self.operator_path / "Source"
        source_path.mkdir(parents=True)
        (self.operator_path / "Voidwright.AgentMcp.dll").write_bytes(b"operator-assembly")
        (source_path / "LocalVehicleTestOperator.cs").write_text("q operator source")
        (source_path / "LocalVehicleTestControl.cs").write_text("q camera policy source")
        PACKAGES.LOCAL_OPERATOR_PACKAGE_SHA256 = PACKAGES.registrar_tree_sha256(self.operator_path)[2]
        PACKAGES.LOCAL_OPERATOR_ASSEMBLY_SHA256 = hashlib.sha256(
            (self.operator_path / "Voidwright.AgentMcp.dll").read_bytes()).hexdigest()
        PACKAGES.LOCAL_OPERATOR_SOURCE_SHA256 = PACKAGES.registrar_tree_sha256(source_path)[2]
        (self.instance / ".vragecage-local-vehicle-test-operator.json").write_text(json.dumps({
            "schema": "vragecage.local-vehicle-test-operator-artifact.v1",
            "instance": PACKAGES.INSTANCE,
            "package_relative_path": "incoming/q-camera-binding-20261021/AgentMcp",
            "package_tree_sha256": PACKAGES.LOCAL_OPERATOR_PACKAGE_SHA256,
            "assembly_sha256": PACKAGES.LOCAL_OPERATOR_ASSEMBLY_SHA256,
            "source_tree_sha256": PACKAGES.LOCAL_OPERATOR_SOURCE_SHA256,
        }))
        self.config_path = self.instance / ".vragecage-local-vehicle-test.json"
        self.write_local_config()

    def write_local_config(self, **overrides: object) -> None:
        config = {
            "schema": "vragecage.local-vehicle-test.v1",
            "instance": PACKAGES.INSTANCE,
            "grid_id": PACKAGES.EXPECTED_GRID_ID,
            "controller_id": PACKAGES.EXPECTED_CONTROLLER_ID,
            "source_tree_sha256": PACKAGES.SOURCE_TREE_SHA256,
            "mode": "read-only-baseline",
            "propulsion_enabled": False,
            "isolated_clear": False,
            "max_speed_meters_per_second": 0.05,
            "max_displacement_meters": 0.5,
            "command_timeout_seconds": 1,
            "host_watchdog_seconds": 60,
            "camera_id": self.camera_id,
            "wheel_ids": PACKAGES.EXPECTED_WHEEL_IDS,
            "wheel_grid_ids": PACKAGES.EXPECTED_WHEEL_GRID_IDS,
        }
        config.update(overrides)
        self.config_path.write_text(json.dumps(config))

    def write_plugin_stage(self, **overrides: object) -> None:
        stage = {
            "instance": PACKAGES.INSTANCE,
            "plugin_id": "voidwright-server-plugin",
            "path": str(self.plugin_path),
            "tree_sha256": PACKAGES.registrar_tree_sha256(self.plugin_path)[2],
        }
        stage.update(overrides)
        (self.instance / ".vragecage-plugin-stage.json").write_text(json.dumps(stage))

    def write_instrumentation(self) -> None:
        xsi = "{http://www.w3.org/2001/XMLSchema-instance}type"
        root = ET.Element("Sector")
        grid = ET.SubElement(root, "MyObjectBuilder_EntityBase", {xsi: "MyObjectBuilder_CubeGrid"})
        ET.SubElement(grid, "EntityId").text = str(PACKAGES.EXPECTED_GRID_ID)
        ET.SubElement(grid, "DisplayName").text = "Blue Truck"
        blocks = ET.SubElement(grid, "CubeBlocks")

        def add_block(parent: ET.Element, entity_id: int, kind: str, subtype: str,
                      top_block_id: int | None = None) -> ET.Element:
            block = ET.SubElement(parent, "MyObjectBuilder_CubeBlock", {xsi: kind})
            ET.SubElement(block, "EntityId").text = str(entity_id)
            ET.SubElement(block, "SubtypeName").text = subtype
            if top_block_id is not None:
                ET.SubElement(block, "TopBlockId").text = str(top_block_id)
            return block

        add_block(blocks, PACKAGES.EXPECTED_CONTROLLER_ID, "MyObjectBuilder_Cockpit", "SmallBlockCapCockpit")
        add_block(blocks, self.camera_id, "MyObjectBuilder_CameraBlock", "SmallCameraBlock")
        add_block(blocks, self.core_id, "MyObjectBuilder_UpgradeModule", "VoidwrightNavigationControllerSmall")
        rotor_ids = [93000000000000000 + index for index in range(4)]
        for wheel_id, rotor_id in zip(PACKAGES.EXPECTED_WHEEL_IDS, rotor_ids):
            add_block(blocks, wheel_id, "MyObjectBuilder_MotorSuspension", "OffroadWheel", rotor_id)
        for index in range(80):
            add_block(blocks, 94000000000000000 + index, "MyObjectBuilder_CubeBlock", "FixtureBlock")
        for index, (wheel_id, grid_id) in enumerate(zip(rotor_ids, PACKAGES.EXPECTED_WHEEL_GRID_IDS)):
            subgrid = ET.SubElement(root, "MyObjectBuilder_EntityBase", {xsi: "MyObjectBuilder_CubeGrid"})
            ET.SubElement(subgrid, "EntityId").text = str(grid_id)
            ET.SubElement(subgrid, "DisplayName").text = f"WheelGrid-{index}"
            sub_blocks = ET.SubElement(subgrid, "CubeBlocks")
            add_block(sub_blocks, wheel_id, "MyObjectBuilder_Wheel", "OffroadSmallRealWheel2x2")
        raw = ET.tostring(root, encoding="utf-8")
        (self.instance / "World/SANDBOX_0_0_0_.sbs").write_bytes(raw)
        (self.instance / ".vragecage-diagnostic-instrumentation.json").write_text(json.dumps({
            "schema": "vragecage.diagnostic-instrumentation.v1",
            "instance": PACKAGES.INSTANCE,
            "authority": "disposable-clone-only",
            "source_tree_sha256": PACKAGES.SOURCE_TREE_SHA256,
            "sector_after_sha256": hashlib.sha256(raw).hexdigest(),
            "controller": {"entity_id": str(PACKAGES.EXPECTED_CONTROLLER_ID),
                           "type": "MyObjectBuilder_Cockpit", "subtype": "SmallBlockCapCockpit"},
            "existing_suspension_ids": list(map(str, PACKAGES.EXPECTED_WHEEL_IDS)),
            "added_blocks": [
                {"entity_id": str(self.core_id), "subtype": "VoidwrightNavigationControllerSmall",
                 "grid_id": str(PACKAGES.EXPECTED_GRID_ID), "grid_name": "Blue Truck"},
                {"entity_id": str(self.camera_id), "subtype": "SmallCameraBlock",
                 "grid_id": str(PACKAGES.EXPECTED_GRID_ID), "grid_name": "Blue Truck"},
            ],
            "geometry": {"camera_min_separation_m": 0.293039,
                         "core_min_separation_m": 1.35734,
                         "instrument_pair_min_separation_m": 2.5},
        }))

    def tearDown(self) -> None:
        PACKAGES.EXPECTED_MODS.clear()
        PACKAGES.EXPECTED_MODS.update(self.old_mod_hashes)
        PACKAGES.SOURCE_TREE_SHA256 = self.old_source_hash
        PACKAGES.VOIDWRIGHT_MOD_TREE_SHA256 = self.old_voidwright_hash
        PACKAGES.SERVER_PLUGIN_TREE_SHA256 = self.old_plugin_hash
        (PACKAGES.LOCAL_OPERATOR_PACKAGE_SHA256, PACKAGES.LOCAL_OPERATOR_ASSEMBLY_SHA256,
         PACKAGES.LOCAL_OPERATOR_SOURCE_SHA256) = self.old_operator_hashes
        self.temporary.cleanup()

    def test_exact_published_mod_bytes_and_voidwright_package_pass(self) -> None:
        self.assertNotEqual(PACKAGES.tree_sha256(self.voidwright_cache)[2],
                            PACKAGES.registrar_tree_sha256(self.voidwright_cache)[2])
        receipt = PACKAGES.verify_run(self.instance, self.worker)
        self.assertTrue(receipt["passed"])
        self.assertEqual(len(receipt["published_mod_packages"]), 5)
        self.assertEqual(receipt["server_plugin_tree_sha256"], PACKAGES.SERVER_PLUGIN_TREE_SHA256)
        self.assertTrue(receipt["plugin_stage_required"])

    def test_changed_dependency_bytes_fail_closed(self) -> None:
        target = self.worker / "magnetar-steam/steamapps/workshop/content/244850/1090562767/payload.bin"
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            PACKAGES.verify_run(self.instance, self.worker)

    def test_local_vehicle_config_requires_exact_plugin_stage(self) -> None:
        self.config_path.unlink()
        with self.assertRaisesRegex(ValueError, "config missing or unsafe"):
            PACKAGES.verify_run(self.instance, self.worker)
        self.write_local_config()
        plugin_receipt = self.instance / ".vragecage-plugin-stage.json"
        plugin_receipt.unlink()
        with self.assertRaisesRegex(ValueError, "requires the reviewed server-plugin"):
            PACKAGES.verify_run(self.instance, self.worker)
        self.write_plugin_stage()
        self.assertTrue(PACKAGES.verify_run(self.instance, self.worker)["passed"])

    def test_q_local_config_rejects_unbound_camera_or_wrong_mode(self) -> None:
        self.write_local_config(camera_id=0)
        with self.assertRaisesRegex(ValueError, "instrumentation identities"):
            PACKAGES.verify_run(self.instance, self.worker)
        self.write_local_config(camera_id=PACKAGES.EXPECTED_CAMERA_ID + 1)
        with self.assertRaisesRegex(ValueError, "instrumentation identities"):
            PACKAGES.verify_run(self.instance, self.worker)
        self.write_local_config(mode="stationary-steering-only")
        with self.assertRaisesRegex(ValueError, "identity or mode"):
            PACKAGES.verify_run(self.instance, self.worker)

    def test_q_read_only_baseline_config_is_exact_and_disables_propulsion(self) -> None:
        self.write_local_config(mode="read-only-baseline", propulsion_enabled=False,
                               isolated_clear=False)
        receipt = PACKAGES.verify_run(self.instance, self.worker)
        self.assertTrue(receipt["passed"])
        self.write_local_config(mode="read-only-baseline", propulsion_enabled=True,
                               isolated_clear=False)
        with self.assertRaisesRegex(ValueError, "identity or mode"):
            PACKAGES.verify_run(self.instance, self.worker)

    def test_q_old_isolated_clear_config_is_rejected(self) -> None:
        self.write_local_config(mode="isolated-clear", propulsion_enabled=True,
                               isolated_clear=True)
        with self.assertRaisesRegex(ValueError, "identity or mode"):
            PACKAGES.verify_run(self.instance, self.worker)

    def test_q_plugin_hash_mismatch_fails_closed(self) -> None:
        self.write_plugin_stage(tree_sha256="a" * 64)
        with self.assertRaisesRegex(ValueError, "differs from the reviewed package"):
            PACKAGES.verify_run(self.instance, self.worker)

    def test_q_local_operator_hash_receipt_is_required_and_content_bound(self) -> None:
        receipt = self.instance / ".vragecage-local-vehicle-test-operator.json"
        receipt.unlink()
        with self.assertRaisesRegex(ValueError, "operator artifact or receipt is missing"):
            PACKAGES.verify_run(self.instance, self.worker)
        self.write_local_config()
        self.setUpOperatorReceipt()
        self.assertTrue(PACKAGES.verify_run(self.instance, self.worker)["passed"])
        (self.operator_path / "Voidwright.AgentMcp.dll").write_bytes(b"wrong operator")
        with self.assertRaisesRegex(ValueError, "operator artifact or exact source receipt differs"):
            PACKAGES.verify_run(self.instance, self.worker)

    def setUpOperatorReceipt(self) -> None:
        source_path = self.operator_path / "Source"
        (self.instance / ".vragecage-local-vehicle-test-operator.json").write_text(json.dumps({
            "schema": "vragecage.local-vehicle-test-operator-artifact.v1",
            "instance": PACKAGES.INSTANCE,
            "package_relative_path": "incoming/q-camera-binding-20261021/AgentMcp",
            "package_tree_sha256": PACKAGES.LOCAL_OPERATOR_PACKAGE_SHA256,
            "assembly_sha256": PACKAGES.LOCAL_OPERATOR_ASSEMBLY_SHA256,
            "source_tree_sha256": PACKAGES.LOCAL_OPERATOR_SOURCE_SHA256,
        }))

    def test_q_instrumentation_requires_camera_core_and_exact_wheel_map(self) -> None:
        for entity_id in (self.camera_id, self.core_id):
            with self.subTest(entity_id=entity_id):
                self.write_instrumentation()
                tree = ET.parse(self.instance / "World/SANDBOX_0_0_0_.sbs")
                grid = next(item for item in tree.getroot().iter("MyObjectBuilder_EntityBase")
                            if item.findtext("EntityId") == str(PACKAGES.EXPECTED_GRID_ID))
                blocks = grid.find("CubeBlocks")
                victim = next(item for item in list(blocks)
                              if item.findtext("EntityId") == str(entity_id))
                blocks.remove(victim)
                raw = ET.tostring(tree.getroot(), encoding="utf-8")
                sector = self.instance / "World/SANDBOX_0_0_0_.sbs"
                sector.write_bytes(raw)
                ledger_path = self.instance / ".vragecage-diagnostic-instrumentation.json"
                ledger = json.loads(ledger_path.read_text())
                ledger["sector_after_sha256"] = hashlib.sha256(raw).hexdigest()
                ledger_path.write_text(json.dumps(ledger))
                with self.assertRaisesRegex(ValueError, "block inventory differs|camera, Core, or cockpit is missing"):
                    PACKAGES.verify_run(self.instance, self.worker)

        self.write_instrumentation()
        tree = ET.parse(self.instance / "World/SANDBOX_0_0_0_.sbs")
        grid = next(item for item in tree.getroot().iter("MyObjectBuilder_EntityBase")
                    if item.findtext("EntityId") == str(PACKAGES.EXPECTED_GRID_ID))
        wheel = next(item for item in grid.findall("./CubeBlocks/*")
                     if item.findtext("EntityId") == str(PACKAGES.EXPECTED_WHEEL_IDS[0]))
        wheel.find("TopBlockId").text = "99999999999999999"
        raw = ET.tostring(tree.getroot(), encoding="utf-8")
        sector.write_bytes(raw)
        ledger = json.loads(ledger_path.read_text())
        ledger["sector_after_sha256"] = hashlib.sha256(raw).hexdigest()
        ledger_path.write_text(json.dumps(ledger))
        with self.assertRaisesRegex(ValueError, "wheel mechanical grid map"):
            PACKAGES.verify_run(self.instance, self.worker)

    def test_helper_format_cannot_spoof_registrar_stage_receipt(self) -> None:
        stage_path = self.instance / ".vragecage-mod-stage.json"
        stage = json.loads(stage_path.read_text())
        stage["tree_sha256"] = PACKAGES.tree_sha256(self.voidwright_cache)[2]
        stage_path.write_text(json.dumps(stage))
        with self.assertRaisesRegex(ValueError, "differs from the reviewed package"):
            PACKAGES.verify_run(self.instance, self.worker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
