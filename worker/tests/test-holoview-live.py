from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "prepare-holoview-live.py"
SPEC = importlib.util.spec_from_file_location("prepare_holoview_live", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class LiveBridgePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        scripts = self.source / "Data/Scripts/HoloView3D"
        scripts.mkdir(parents=True)
        (scripts / "BoundedHoloCamSession.cs").write_text("VrageCageTickVirtualCapture();\n", encoding="utf-8")
        (scripts / "BoundedHoloCamWorker.cs").write_text(
            "public int VirtualIndex = -1; VrageCageCompleteVirtualFrame(job);\n", encoding="utf-8")
        (scripts / "VirtualCapture.cs").write_text("static proof pose list", encoding="utf-8")
        (self.source / "modinfo.sbmi").write_text("fixture", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_replaces_only_virtual_partial_and_preserves_physical_pipeline(self):
        target = self.root / "prepared"
        result = MODULE.prepare(self.source, target)
        self.assertTrue((target / "Data/Scripts/HoloView3D/VirtualCapture.cs").is_file())
        self.assertEqual((target / "Data/Scripts/HoloView3D/BoundedHoloCamSession.cs").read_text(),
                         "VrageCageTickVirtualCapture();\n")
        self.assertEqual((target / "Data/Scripts/HoloView3D/BoundedHoloCamWorker.cs").read_text(),
                         "public int VirtualIndex = -1; VrageCageCompleteVirtualFrame(job);\n")
        text = (target / "Data/Scripts/HoloView3D/VirtualCapture.cs").read_text()
        self.assertIn("void VrageCageTickVirtualCapture()", text)
        self.assertIn("job.Cam = cam", text)
        self.assertIn("source_grid_ids", text)
        self.assertIn("renderer_update_counter", text)
        self.assertEqual(result["replaced_file"], "Data/Scripts/HoloView3D/VirtualCapture.cs")

    def test_refuses_uninstrumented_or_overwrite_source(self):
        target = self.root / "prepared"
        (self.source / "Data/Scripts/HoloView3D/BoundedHoloCamSession.cs").write_text("no hook", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "update hook"):
            MODULE.prepare(self.source, target)
        self.assertFalse(target.exists())

    def test_refuses_existing_destination(self):
        target = self.root / "prepared"
        target.mkdir()
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            MODULE.prepare(self.source, target)


if __name__ == "__main__":
    unittest.main()
