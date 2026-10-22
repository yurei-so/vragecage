#!/usr/bin/env python3
"""Contract tests for the bounded virtual HoloView request and receipt verifier."""

from __future__ import annotations

import base64
import hashlib
import json
import runpy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTRUMENT = runpy.run_path(str(ROOT / "instrument-holoview-virtual.py"))
VERIFY = runpy.run_path(str(ROOT / "verify-holoview-virtual.py"))["verify"]


class VirtualRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pose = {
            "origin": [1, 2, 3], "forward": [0, 0, -1], "up": [0, 1, 0], "fov_degrees": 60,
        }

    def test_valid_pose_and_explicit_rejection(self) -> None:
        self.assertEqual(INSTRUMENT["normalize_pose"](self.pose)["origin"], (1.0, 2.0, 3.0))
        for changed in (
            {**self.pose, "forward": [0, 0, 0]},
            {**self.pose, "up": [0, 0, 1]},
            {**self.pose, "fov_degrees": 180},
            {**self.pose, "camera_id": 42},
            {**self.pose, "origin": [float("nan"), 0, 0]},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                INSTRUMENT["normalize_pose"](changed)

    def test_template_has_no_unresolved_fields(self) -> None:
        request = json.loads((ROOT / "tests/fixtures/virtual-v2r3-two-pose.json").read_text())
        request["poses"] = [INSTRUMENT["normalize_pose"](pose) for pose in request["poses"]]
        rendered = INSTRUMENT["build_template"](request)
        self.assertNotIn("@@", rendered)
        self.assertIn("VrageCageVirtualRequestId", rendered)
        self.assertNotIn("SHA256", rendered)

    def test_template_accepts_three_independent_camera_poses(self) -> None:
        request = json.loads((ROOT / "tests/fixtures/virtual-v2r3-two-pose.json").read_text())
        request["poses"].append({
            "origin": [2, 3, 4], "forward": [0, 0, -1], "up": [0, 1, 0], "fov_degrees": 55,
        })
        request["poses"] = [INSTRUMENT["normalize_pose"](pose) for pose in request["poses"]]
        rendered = INSTRUMENT["build_template"](request)
        self.assertEqual(rendered.count("new Vector3D("), 9)


class VirtualReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "Saves/world/Storage/123.sbm_HoloView3D"
        self.folder.mkdir(parents=True)
        self.request = {
            "world_name": "world", "request_id": "req", "capture_id": "cap",
            "width": 16, "height": 16,
            "poses": [
                {"origin": [0, 0, 0], "forward": [0, 0, -1], "up": [0, 1, 0], "fov_degrees": 60},
                {"origin": [1, 0, 0], "forward": [0, 0, -1], "up": [0, 1, 0], "fov_degrees": 60},
            ],
        }
        self.log = self.root / "game.log"
        markers = []
        for index, pose in enumerate(self.request["poses"]):
            stem = f"vragecage-virtual-cap-{index}"
            image = bytearray((3, 5, 9, 255) * 256)
            for pixel in range(120):
                image[pixel * 4] = 100 + index
                image[pixel * 4 + 1] = 30 + pixel % 100
                image[pixel * 4 + 2] = 40 + pixel % 80
            (self.folder / f"{stem}.rgba64.txt").write_bytes(base64.b64encode(image))
            metadata = {
                "schema": "vragecage.virtual-holoview.v1", "source_kind": "virtual",
                "request_id": "req", "world_name_observed": "world", "pose_index": index,
                "snapshot_tick": 60 + index, "width": 16, "height": 16,
                "fov_degrees": 60, "geometry_primitives": 20,
                **{key: pose[key] for key in ("origin", "forward", "up")},
            }
            (self.folder / f"{stem}.json").write_text(json.dumps(metadata))
            markers.append(f"[VRageCage] virtual HoloView capture=cap request=req frame={index} tick={60 + index}")
        self.log.write_text("\n".join(markers))
        self.terminal = {
            "schema": "vragecage.lab-terminal-receipt.v1", "run_id": "run123",
            "receipt": {
                "lab_contract_valid": True, "lab_authority": "destructive-lab",
                "clean_shutdown": True, "game_ready": True, "scripts_loaded": True,
                "lab_run_id": "run123", "staged_mod_unchanged": True,
                "staged_mod": {"synthetic_workshop_id": 123},
                "game_log": str(self.log), "game_log_sha256": hashlib.sha256(self.log.read_bytes()).hexdigest(),
                "quarantine_run": "Quarantine/run123",
            },
        }
        run_path = self.root / "Quarantine/run123/run.json"
        run_path.parent.mkdir(parents=True)
        run_path.write_text(json.dumps({
            "run_id": "run123", "instance": self.root.name,
            "started_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        }))

    def test_two_pose_receipt_and_hashes(self) -> None:
        result = VERIFY(self.folder, self.request, self.terminal, "terminalhash")
        self.assertTrue(result["passed"])
        self.assertEqual(result["pairwise_changed_pixels"]["0-1"], 120)
        self.assertNotEqual(result["frames"][0]["rgba_sha256"], result["frames"][1]["rgba_sha256"])

    def test_rejects_pose_mismatch(self) -> None:
        self.request["poses"][0]["origin"] = [100, 0, 0]
        with self.assertRaisesRegex(ValueError, "origin differs"):
            VERIFY(self.folder, self.request, self.terminal, "terminalhash")

    def test_rejects_cross_run_log(self) -> None:
        self.terminal["receipt"]["game_log_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "game log hash mismatch"):
            VERIFY(self.folder, self.request, self.terminal, "terminalhash")

    def test_rejects_blank_or_replayed_images(self) -> None:
        first = self.folder / "vragecage-virtual-cap-0.rgba64.txt"
        second = self.folder / "vragecage-virtual-cap-1.rgba64.txt"
        second.write_bytes(first.read_bytes())
        with self.assertRaisesRegex(ValueError, "identical images"):
            VERIFY(self.folder, self.request, self.terminal, "terminalhash")

    def test_rejects_transparent_black_frame(self) -> None:
        first = self.folder / "vragecage-virtual-cap-0.rgba64.txt"
        first.write_bytes(base64.b64encode(bytes(16 * 16 * 4)))
        with self.assertRaisesRegex(ValueError, "transparent, blank"):
            VERIFY(self.folder, self.request, self.terminal, "terminalhash")


if __name__ == "__main__":
    unittest.main()
