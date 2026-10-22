#!/usr/bin/env python3
"""Serializer-shape checks for V4 disposable lifecycle instrumentation."""

import importlib.machinery
import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "instrument-v4-target.py"
SPEC = importlib.util.spec_from_loader(
    "instrument_v4_target", importlib.machinery.SourceFileLoader("instrument_v4_target", str(SCRIPT)))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InstrumentedBuilderTests(unittest.TestCase):
    def test_core_serialization_matches_game_authored_upgrade_module_fields(self):
        controller = ET.fromstring('''<MyObjectBuilder_CubeBlock>
          <BlockOrientation Forward="Backward" Up="Up" />
          <ColorMaskHSV x="0" y="-0.8" z="-0.13" />
          <Owner>144115188075855895</Owner><BuiltBy>144115188075855895</BuiltBy>
          <ShareMode>Faction</ShareMode>
        </MyObjectBuilder_CubeBlock>''')
        block = MODULE.make_core_block("127135949041986091", (-2, 7, -2), controller)
        self.assertEqual(block.get(MODULE.XSI), "MyObjectBuilder_UpgradeModule")
        self.assertEqual([child.tag for child in block], [
            "SubtypeName", "EntityId", "Min", "IntegrityPercent", "BuildPercent",
            "BlockOrientation", "ColorMaskHSV", "Owner", "BuiltBy", "ShareMode",
            "CustomName", "ShowOnHUD", "ShowInTerminal", "ShowInToolbarConfig",
            "ShowInInventory", "Enabled",
        ])
        self.assertIsNone(block.find("Name"))
        self.assertIsNone(block.find("NumberInGrid"))
        self.assertEqual(block.findtext("IntegrityPercent"), "1")
        self.assertEqual(block.findtext("BuildPercent"), "1")
        self.assertEqual(block.find("BlockOrientation").attrib, {"Forward": "Backward", "Up": "Up"})
        self.assertEqual(block.find("ColorMaskHSV").attrib, {"x": "0", "y": "-0.8", "z": "-0.13"})
        self.assertEqual(block.findtext("Owner"), "144115188075855895")
        self.assertEqual(block.findtext("BuiltBy"), "144115188075855895")
        self.assertEqual(block.findtext("ShareMode"), "Faction")
        self.assertEqual(block.findtext("CustomName"), "Voidwright Diagnostic Core")

    def test_camera_uses_base_game_small_camera_subtype(self):
        self.assertEqual(MODULE.CAMERA_SUBTYPE, "SmallCameraBlock")

    def test_camera_min_follows_entity_id_and_name(self):
        camera = ET.fromstring('''<MyObjectBuilder_CubeBlock>
          <SubtypeName>SmallCameraBlock</SubtypeName><EntityId>1</EntityId><Name>1</Name>
          <Min x="0" y="0" z="0"/><BlockOrientation Forward="Backward" Up="Up"/>
        </MyObjectBuilder_CubeBlock>''')
        MODULE.set_builder_cell(camera, (3, 4, 5))
        self.assertEqual([child.tag for child in camera],
                         ["SubtypeName", "EntityId", "Name", "Min", "BlockOrientation"])
        self.assertEqual(camera.find("Min").attrib, {"x": "3", "y": "4", "z": "5"})

    def test_reused_placement_is_bound_to_source_sector_grid_and_controller(self):
        prior = {
            "schema": "vragecage.diagnostic-instrumentation.v1",
            "instance": "codex-v4-blue-truck-lifecycle-20261020-j",
            "source_tree_sha256": MODULE.EXPECTED_SOURCE_SHA256,
            "world_import_sha256": MODULE.EXPECTED_SOURCE_SHA256,
            "sector_before_sha256": "sector-hash",
            "controller": {"entity_id": "controller-id"},
            "added_blocks": [
                {"subtype": MODULE.CORE_SUBTYPE, "entity_id": "core-id", "grid_id": "grid-id",
                 "cell": [-2, 7, -2]},
                {"subtype": MODULE.CAMERA_SUBTYPE, "entity_id": "camera-id", "grid_id": "grid-id",
                 "cell": [-2, 4, 3]},
            ],
        }
        self.assertEqual(MODULE.reused_placement_cells(
            prior, expected_hash=MODULE.EXPECTED_SOURCE_SHA256, sector_hash="sector-hash",
            target_id="grid-id", controller_id="controller-id",
            camera_cells=[(-2, 4, 3)], core_cells=[(-2, 7, -2)]),
            ((-2, 4, 3), (-2, 7, -2)))
        with self.assertRaises(SystemExit):
            MODULE.reused_placement_cells(
                prior, expected_hash=MODULE.EXPECTED_SOURCE_SHA256, sector_hash="other-hash",
                target_id="grid-id", controller_id="controller-id",
                camera_cells=[(-2, 4, 3)], core_cells=[(-2, 7, -2)])

    def test_binary_sector_cache_is_hash_checked_and_preserved_in_quarantine(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instance = root / "clone"
            source = root / "source"
            backup = instance / "Quarantine" / "edit"
            (instance / "World").mkdir(parents=True)
            source.mkdir()
            backup.mkdir(parents=True)
            payload = b"pinned binary sector cache"
            cache = instance / "World/SANDBOX_0_0_0_.sbsB5"
            cache.write_bytes(payload)
            (source / cache.name).write_bytes(payload)
            digest, relative = MODULE.quarantine_binary_sector_cache(instance, source, backup)
            self.assertEqual(digest, MODULE.sha256_bytes(payload))
            self.assertEqual(relative, "Quarantine/edit/SANDBOX_0_0_0_.sbsB5")
            self.assertFalse(cache.exists())
            self.assertEqual((instance / relative).read_bytes(), payload)

            cache.write_bytes(b"unexpected cache")
            with self.assertRaises(SystemExit):
                MODULE.quarantine_binary_sector_cache(instance, source, backup)


if __name__ == "__main__":
    unittest.main()
