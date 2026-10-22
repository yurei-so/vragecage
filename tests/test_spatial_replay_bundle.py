import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "worker" / "spatial-replay-bundle.py"
SPEC = importlib.util.spec_from_file_location("spatial_replay_bundle", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SpatialReplayBundleTests(unittest.TestCase):
    def test_blue_truck_bundle_preserves_evidence_and_marks_gaps(self):
        source = MODULE.SOURCE_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            manifest = MODULE.build_bundle(source, out)
            samples = [json.loads(line) for line in (out / "samples.ndjson").read_text().splitlines()]
            events = [json.loads(line) for line in (out / "events.ndjson").read_text().splitlines()]
            self.assertEqual(manifest["schema"], "vragecage.spatial-replay.v1")
            self.assertEqual(len(samples), 4)
            self.assertTrue(all(s["orientation_xyzw"] is None for s in samples))
            self.assertTrue(all(s["simulation_tick"] is None for s in samples))
            self.assertTrue(all(s["linear_velocity_mps"] is None for s in samples))
            self.assertIsNone(manifest["measurement_summary"]["safe-zone-enabled"]["continuous_path_length_m"])
            self.assertGreater(manifest["measurement_summary"]["safe-zone-disabled"]["net_runtime_displacement_m"], 0)
            self.assertTrue(any(e["event_type"] == "POSITION_DISCONTINUITY" for e in events))
            self.assertTrue(any(e["event_type"] == "SAFE_ZONE_RELATION_DERIVED" for e in events))
            self.assertFalse(any(e["event_type"] == "SAFE_ZONE_BOUNDARY_CROSSING" for e in events))
            self.assertTrue(all(isinstance(e["source_ref"], dict) for e in events))
            self.assertEqual(len({e["event_id"] for e in events}), len(events))
            self.assertEqual(manifest["world_identity"]["session_name"], "test_suite_world_v4")
            for evidence in manifest["source_artifacts"].values():
                copied = out / evidence["bundle_path"]
                self.assertTrue(copied.is_file())
                self.assertEqual(MODULE.sha256(copied), evidence["sha256"])
            viewer = (out / "index.html").read_text()
            self.assertIn("POSITION_DISCONTINUITY", viewer)
            self.assertIn("Integrated path", viewer)
            self.assertIn("unobserved interval", viewer)

    def test_refuses_to_overwrite_existing_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            out.mkdir()
            (out / "keep.txt").write_text("untouched")
            with self.assertRaises(FileExistsError):
                MODULE.build_bundle(MODULE.SOURCE_ROOT, out)
            self.assertEqual((out / "keep.txt").read_text(), "untouched")

    def test_parses_run_log_wheel_states_without_fabricating_ticks(self):
        sample = (
            "2026-10-21 18:15:12.032 - Thread:   1 ->  Voidwright local test idle: "
            "grid=105034224363529183 controller=83468039688685570 "
            "position=8424.180962259758,8723.715090808311,-15770.303031311225 "
            "linearSpeed=1.880869967213828 angularSpeed=0.8762725049689931 "
            "steeringOrientationKnown=True wheelReadbacks=99:working=True,functional=True,"
            "override=0,angle=0,propulsion=0,brake=True,handbrake=True writerAudit=grid=x\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "run.log"
            log.write_text(sample)
            records = MODULE.parse_idle_log(log, "run", "condition", "evidence/run.log")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["position_m"][0], 8424.180962259758)
        self.assertEqual(records[0]["source_wall_time"], "2026-10-21 18:15:12.032")
        self.assertIsNone(records[0]["simulation_tick"])
        self.assertEqual(records[0]["actuator_states"][0]["block_id"], "99")
        self.assertEqual(records[0]["actuator_states"][0]["observed_properties"]["brake"], "True")
        self.assertEqual(records[0]["actuator_states"][0]["observed_properties"]["propulsion"], "0")

    def test_log_event_parser_reports_marker_coverage_without_copying_unmatched_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "run.log"
            log.write_text(
                "2026-10-21 18:15:12.032 - Thread: 1 -> Voidwright local test preflight passed: run=x\n"
                "unrelated engine detail\n"
            )
            events = MODULE.parse_log_events(log, "run", "condition", "evidence/run.log")
        self.assertEqual([event["event_type"] for event in events], ["PREFLIGHT_PASSED", "LOG_PARSE_COVERAGE"])
        coverage = events[-1]
        self.assertEqual(coverage["details"]["total_lines"], 2)
        self.assertEqual(coverage["details"]["recognized_marker_lines"], 1)
        self.assertEqual(coverage["details"]["unclassified_lines"], 1)
        self.assertIsNone(coverage["source_ref"]["line"])
        self.assertIsNone(coverage["raw_source_line"])


if __name__ == "__main__":
    unittest.main()
