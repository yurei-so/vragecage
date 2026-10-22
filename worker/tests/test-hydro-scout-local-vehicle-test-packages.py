from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "hydro_scout_local_vehicle_test_packages.py"
SPEC = importlib.util.spec_from_file_location("hydro_scout_local_vehicle_test_packages", MODULE_PATH)
assert SPEC and SPEC.loader
packages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packages)


class HydroScoutPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.instance = self.root / packages.INSTANCE
        (self.instance / "World").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_saved_fixture_passes_serialized_gate(self) -> None:
        source = Path(__file__).resolve().parents[2] / "artifacts/local-vehicle-land-test-20261022-c/World"
        shutil.copytree(source, self.instance / "World", dirs_exist_ok=True)
        packages.verify_serialized_fixture(self.instance)

    def test_changed_sector_hash_is_rejected(self) -> None:
        sector = self.instance / "World/SANDBOX_0_0_0_.sbs"
        sector.write_text("<MyObjectBuilder_Sector />")
        with self.assertRaisesRegex(ValueError, "sector hash"):
            packages.verify_serialized_fixture(self.instance)

    def test_legacy_numeric_world_id_is_rejected_before_launch(self) -> None:
        source = Path(__file__).resolve().parents[2] / "artifacts/local-vehicle-land-test-20261022-c/World"
        shutil.copytree(source, self.instance / "World", dirs_exist_ok=True)
        checkpoint = ET.parse(self.instance / "World/Sandbox.sbc")
        checkpoint.getroot().find("WorldId").text = "5210666839731879836"
        checkpoint.write(self.instance / "World/Sandbox.sbc", encoding="utf-8", xml_declaration=True)
        with self.assertRaisesRegex(ValueError, "valid GUID"):
            packages.verify_serialized_fixture(self.instance)

    def test_read_only_config_passes_and_higher_caps_fail(self) -> None:
        source = Path(__file__).resolve().parents[2] / "artifacts/local-vehicle-land-test-20261022-c/local-vehicle-test-config.json"
        config = json.loads(source.read_text())
        path = self.instance / ".vragecage-local-vehicle-test.json"
        path.write_text(json.dumps(config))
        parsed, digest = packages.verify_config(self.instance)
        self.assertEqual(parsed["mode"], "read-only-baseline")
        self.assertEqual(len(digest), 64)
        config["max_speed_meters_per_second"] = 0.2
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "strict caps"):
            packages.verify_config(self.instance)

    def test_per_instance_magnetar_compatibility_packages_are_hash_bound(self) -> None:
        source = (Path(__file__).resolve().parents[2] /
                  "artifacts/local-vehicle-land-test-20261022-c/packages/magnetar-core-compat")
        target = self.instance / "MagnetarConfig/GitHub"
        shutil.copytree(source, target)
        hashes = packages.verify_magnetar_compatibility_packages(self.instance)
        self.assertEqual(set(hashes), {"DotNetCompat-6d00a29d", "LinuxCompat-68cd6e19"})
        binary = target / "DotNetCompat-6d00a29d/Bin/plugin.dll"
        binary.write_bytes(binary.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "compatibility package differs"):
            packages.verify_magnetar_compatibility_packages(self.instance)

    def test_magnetar_hub_config_is_hash_bound_to_c_plugin_path(self) -> None:
        source = (Path(__file__).resolve().parents[2] /
                  "artifacts/local-vehicle-land-test-20261022-c/packages/magnetar-hub-config/Sources")
        target = self.instance / "MagnetarConfig/Sources"
        shutil.copytree(source, target)
        hashes = packages.verify_magnetar_hub_config(self.instance)
        self.assertEqual(len(hashes["sources_sha256"]), 64)
        (target / "sources.xml").write_text("<SourcesConfig />")
        with self.assertRaisesRegex(ValueError, "package differs"):
            packages.verify_magnetar_hub_config(self.instance)


if __name__ == "__main__":
    unittest.main()
