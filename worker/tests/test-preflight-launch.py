#!/usr/bin/env python3
"""Exercise launcher preflight environment construction without systemd or a game."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import importlib.machinery
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
LOADER = importlib.machinery.SourceFileLoader("vragecage_cli", str(REPO_ROOT / "bin/vragecage"))
SPEC = importlib.util.spec_from_loader("vragecage_cli", LOADER)
assert SPEC is not None and SPEC.loader is not None
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class PreflightLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="vragecage-preflight-launch-")
        self.root = Path(self.temporary.name)
        self.instance = self.root / "instances" / "depth-fixture"
        self.instance.mkdir(parents=True)
        world = self.instance / "World"
        world.mkdir()
        (world / "Sandbox.sbc").write_text("<MyObjectBuilder_Checkpoint><SessionName>test-world</SessionName></MyObjectBuilder_Checkpoint>")
        (self.instance / "SpaceEngineers-Dedicated.cfg").write_text(
            f"<DedicatedServer><IP>0.0.0.0</IP><LoadWorld>{world}</LoadWorld></DedicatedServer>"
        )
        (self.instance / ".vragecage-engine").write_text("magnetar\n")
        (self.instance / ".vragecage-lab.json").write_text("{}\n")
        quarantine = self.instance / "Quarantine" / ("a" * 32)
        quarantine.mkdir(parents=True)
        context_path = quarantine / "preflight-context-v1.txt"
        context_path.write_text("run-bound context\n")
        self.context = {
            "path": str(context_path), "sha256": "a" * 64, "fixture_id": "b" * 32,
        }
        self.command = {
            "action": "drive-to-coordinate-depth", "operation_id": "c" * 32,
            "controller_id": 12345, "depth_camera_id": 67890, "target": [1, 2, 3],
            "max_speed": 0.5, "timeout_ticks": 900, "arrival_radius": 2.0,
            "start_delay_ticks": 0, "reverse": False,
        }
        self.active = {
            "run_id": "a" * 32, "fixture_id": "b" * 32,
            "quarantine": str(quarantine), "preflight_context": self.context,
            "command": self.command,
        }
        self.activation_args: list[list[str]] = []
        self.launch_args: list[list[str]] = []
        self.package_verifier_args: list[list[str]] = []
        CLI.worker_root = lambda: self.root
        CLI.script = lambda name: "/fake/" + name
        def fake_run_checked(argv, env=None):
            if len(argv) > 1 and Path(argv[1]).name == ".hydro_scout_local_vehicle_test_packages.py":
                self.package_verifier_args.append(list(argv))
                return 0
            self.launch_args.append(list(argv))
            return 0
        CLI.run_checked = fake_run_checked

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, expected_operation_id: str | None = None,
             instance: str = "depth-fixture") -> None:
        def fake_run(argv, **kwargs):
            if argv[:3] == ["systemctl", "--user", "is-active"]:
                return SimpleNamespace(returncode=3)
            if argv[0] == "/fake/lab-contract.py":
                self.activation_args.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(self.active), stderr="")
            raise AssertionError(f"unexpected process launch in non-engine test: {argv}")

        args = argparse.Namespace(
            instance=instance, expected_operation_id=expected_operation_id,
            expected_command_sha256="d" * 64 if expected_operation_id else None,
            discovery_only=instance in ("land-local-test-hydro-scout-20261022-b",
                                        "land-local-test-v4r1-blue-20261022-a"),
        )
        with patch.object(CLI.subprocess, "run", side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CLI.command_run(args), 0)

    @staticmethod
    def _setenv(argv: list[str]) -> dict[str, str]:
        result = {}
        for value in argv:
            if value.startswith("--setenv="):
                key, item = value.removeprefix("--setenv=").split("=", 1)
                result[key] = item
        return result

    def test_depth_run_gets_fresh_challenge_and_exact_bound_context(self) -> None:
        self._run(expected_operation_id=self.command["operation_id"])
        self.assertIn("--property=TimeoutStopSec=60", self.launch_args[-1])
        self.assertNotIn("--property=RuntimeMaxSec=60", self.launch_args[-1])
        first = self._setenv(self.launch_args[-1])
        second_run = "e" * 32
        second_quarantine = self.instance / "Quarantine" / second_run
        second_quarantine.mkdir(parents=True)
        second_context_path = second_quarantine / "preflight-context-v1.txt"
        second_context_path.write_text("second run-bound context\n")
        self.active.update({
            "run_id": second_run,
            "quarantine": str(second_quarantine),
            "preflight_context": {
                "path": str(second_context_path), "sha256": "f" * 64,
                "fixture_id": self.active["fixture_id"],
            },
        })
        self._run(expected_operation_id=self.command["operation_id"])
        second = self._setenv(self.launch_args[-1])

        for env, run_id, context in (
            (first, "a" * 32, self.context),
            (second, second_run, self.active["preflight_context"]),
        ):
            self.assertEqual(env["VRAGECAGE_LAB_RUN_ID"], run_id)
            self.assertEqual(env["VRAGECAGE_LAB_OPERATION_ID"], self.command["operation_id"])
            self.assertEqual(env["VRAGECAGE_LAB_FIXTURE_ID"], self.active["fixture_id"])
            self.assertEqual(env["VRAGECAGE_LAB_PREFLIGHT_CONTEXT"], context["path"])
            self.assertEqual(env["VRAGECAGE_LAB_PREFLIGHT_CONTEXT_SHA256"], context["sha256"])
            self.assertRegex(env["VRAGECAGE_LAB_PREFLIGHT_CHALLENGE"], re.compile(r"^[0-9a-f]{32}$"))
            self.assertEqual(env["VRAGECAGE_LAB_MOTION_OWNER"], "voidwright-mod")
        self.assertEqual(first["VRAGECAGE_LAB_RUN_ID"], "a" * 32)
        self.assertEqual(second["VRAGECAGE_LAB_RUN_ID"], second_run)
        self.assertNotEqual(first["VRAGECAGE_LAB_PREFLIGHT_CHALLENGE"],
                            second["VRAGECAGE_LAB_PREFLIGHT_CHALLENGE"])
        for argv in self.activation_args:
            self.assertIn("--expected-operation-id", argv)
            self.assertIn(self.command["operation_id"], argv)
            self.assertIn("--expected-command-sha256", argv)
            self.assertIn("d" * 64, argv)

    def test_direct_maneuver_selects_server_plugin_as_only_actuator_writer(self) -> None:
        self.command.update({
            "action": "direct-maneuver", "grid_id": 456, "camera_id": 67890,
            "segments": [
                {"kind": "forward-distance", "value": 0.5, "max_speed": 0.25, "timeout_ticks": 120},
                {"kind": "stop", "value": 0, "max_speed": 0, "timeout_ticks": 60},
                {"kind": "stationary-hold", "value": 0, "max_speed": 0, "timeout_ticks": 60},
            ],
        })
        self._run(expected_operation_id=self.command["operation_id"])
        env = self._setenv(self.launch_args[-1])
        self.assertEqual(env["VRAGECAGE_LAB_COMMAND"], "direct-maneuver")
        self.assertEqual(env["VRAGECAGE_LAB_MOTION_OWNER"], "voidwright-plugin")

    def test_missing_preflight_context_never_invokes_systemd_run(self) -> None:
        self.active["preflight_context"] = None
        with self.assertRaises(SystemExit):
            self._run()
        self.assertEqual(self.launch_args, [])

    def _prepare_local_vehicle_test(self, instance_name: str = "land-local-test-hydro-scout-20261022-b",
                                   *, source_hash: str = "ef3d19b5f3f1560c1f79e9fad24e0a83668f89ea730f6fd45936c4a4394d0b59",
                                   grid_id: int = 113504086011869676,
                                   controller_id: int = 112711005364517351,
                                   speed: float = 0.05, displacement: float = 0.5,
                                   timeout: int = 1, watchdog: int = 60,
                                   lab_overrides: dict | None = None,
                                   import_overrides: dict | None = None) -> Path:
        instance = self.root / "instances" / instance_name
        if instance.exists():
            shutil.rmtree(instance)
        instance.mkdir(parents=True)
        world = instance / "World"
        fixture_world = REPO_ROOT / "artifacts/local-vehicle-land-test-20261022-b/World"
        shutil.copytree(fixture_world, world)
        (instance / "SpaceEngineers-Dedicated.cfg").write_text(
            f"<DedicatedServer><IP>0.0.0.0</IP><LoadWorld>{world}</LoadWorld></DedicatedServer>"
        )
        (instance / ".vragecage-engine").write_text("magnetar\n")
        lab = {
            "schema": "vragecage.lab-authority.v1", "authority": "destructive-lab",
            "disposable": True, "engine": "magnetar", "instance": instance_name,
            "source_tree_sha256": source_hash,
        }
        lab.update(lab_overrides or {})
        (instance / ".vragecage-lab.json").write_text(json.dumps(lab))
        imported = {
            "schema": "vragecage.world-import.v1", "instance": instance_name,
            "engine": "magnetar", "tree_sha256": source_hash,
        }
        imported.update(import_overrides or {})
        (instance / ".vragecage-world-import.json").write_text(json.dumps(imported))
        v4r1_blue = instance_name == "land-local-test-v4r1-blue-20261022-a"
        camera_id = 91077977706459680 if v4r1_blue else 6902088205773520808
        wheel_ids = ([72448287472244394, 102872810511526201,
                      87663115116726466, 95379321851010785] if v4r1_blue else
                     [135633742662929758, 92672912164488213,
                      129575574301717563, 72699375256551291])
        wheel_grid_ids = ([130825891980829223, 127988954682183794,
                           141480516845941196, 95733545729686414] if v4r1_blue else
                          [83781654658335329, 101822873690952882,
                           90663942980601709, 143998760294003094])
        (instance / ".hydro_scout_local_vehicle_test_packages.py").write_text("# mocked verifier\n")
        (instance / ".vragecage-local-vehicle-test.json").write_text(json.dumps({
            "schema": "vragecage.local-vehicle-test.v1", "instance": instance_name,
            "grid_id": grid_id, "controller_id": controller_id,
            "source_tree_sha256": source_hash, "mode": "read-only-baseline",
            "propulsion_enabled": False, "isolated_clear": False,
            "camera_id": camera_id, "wheel_ids": wheel_ids, "wheel_grid_ids": wheel_grid_ids,
            "max_speed_meters_per_second": speed, "max_displacement_meters": displacement,
            "command_timeout_seconds": timeout, "host_watchdog_seconds": watchdog,
        }))
        local_quarantine = instance / "Quarantine" / self.active["run_id"]
        local_quarantine.mkdir(parents=True)
        local_context = local_quarantine / "preflight-context-v1.txt"
        local_context.write_text("local-test run-bound context\n")
        self.active = {
            **self.active,
            "instance": instance_name,
            "bootstrap_mode": "discovery-only",
            "bootstrap_nonce": "f" * 32,
            "baseline_discovery_run_id": "9" * 32,
            "command": None,
            "quarantine": str(local_quarantine),
            "preflight_context": {
                "path": str(local_context), "sha256": "e" * 64,
                "fixture_id": self.active["fixture_id"],
            },
        }
        return instance

    def _assert_local_vehicle_test_rejected(self, instance_name: str = "land-local-test-hydro-scout-20261022-b") -> None:
        with self.assertRaises(SystemExit):
            self._run(instance=instance_name)
        self.assertEqual(self.launch_args, [])

    def test_local_vehicle_test_accepts_only_pinned_fresh_clone_and_keeps_watchdog(self) -> None:
        instance_name = "land-local-test-hydro-scout-20261022-b"
        instance = self._prepare_local_vehicle_test(instance_name)
        local_quarantine = instance / "Quarantine" / self.active["run_id"]
        local_context = local_quarantine / "preflight-context-v1.txt"

        self._run(instance=instance_name)
        launch = self.launch_args[-1]
        self.assertEqual(launch[launch.index("--unit") + 1], f"vragecage-{instance_name}")
        self.assertIn("--property=RuntimeMaxSec=60", launch)
        self.assertIn("--property=TimeoutStopSec=10", launch)
        self.assertIn("--property=KillMode=control-group", launch)
        self.assertNotIn("--property=TimeoutStopSec=60", launch)
        env = self._setenv(launch)
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST"], "1")
        self.assertEqual(env["VRAGECAGE_LAB_MANIFEST"], str(instance / ".vragecage-lab.json"))
        self.assertEqual(env["VRAGECAGE_LAB_DISCOVERY_NONCE"], "f" * 32)
        self.assertEqual(env["VRAGECAGE_LAB_DISCOVERY_BASELINE_RUN_ID"], "9" * 32)

    def test_hydro_read_only_baseline_launch_is_explicit_and_disables_movement_modes(self) -> None:
        instance_name = "land-local-test-hydro-scout-20261022-b"
        instance = self._prepare_local_vehicle_test(instance_name)
        config_path = instance / ".vragecage-local-vehicle-test.json"
        config = json.loads(config_path.read_text())
        config.update({"mode": "read-only-baseline", "propulsion_enabled": False,
                       "isolated_clear": False})
        config_path.write_text(json.dumps(config))

        self._run(instance=instance_name)
        env = self._setenv(self.launch_args[-1])
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_READ_ONLY_BASELINE"], "true")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_ISOLATED_CLEAR"], "false")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_STEERING_ONLY"], "true")

    def test_v4r1_stationary_brake_only_launch_cannot_authorize_movement(self) -> None:
        instance_name = "land-local-test-v4r1-blue-20261022-a"
        instance = self._prepare_local_vehicle_test(
            instance_name, source_hash="7377fa735e431f7108e57735ff79a3f33aee662ee208e670ee79800d1585fa8c",
            grid_id=105034224363529183, controller_id=83468039688685570)
        config_path = instance / ".vragecage-local-vehicle-test.json"
        config = json.loads(config_path.read_text())
        config["mode"] = "stationary-brake-only"
        config_path.write_text(json.dumps(config))

        with patch.object(CLI, "verify_block_lifecycle_prelaunch"):
            self._run(instance=instance_name)

        env = self._setenv(self.launch_args[-1])
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_READ_ONLY_BASELINE"], "false")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_ISOLATED_CLEAR"], "false")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_STEERING_ONLY"], "true")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_BRAKE_ONLY"], "true")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_CAMERA_ID"], "91077977706459680")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_WHEEL_GRID_IDS"],
                         "130825891980829223,127988954682183794,141480516845941196,95733545729686414")
        receipt = json.loads((Path(self.active["quarantine"]) / "authorization-receipt.json").read_text())
        self.assertEqual(receipt["scope"], "stationary-brake-only")
        self.assertIs(receipt["actuator_commands"], True)
        self.assertIs(receipt["movement_authorized"], False)

    def test_v4r1_read_only_baseline_remains_non_mutating(self) -> None:
        instance_name = "land-local-test-v4r1-blue-20261022-a"
        instance = self._prepare_local_vehicle_test(
            instance_name, source_hash="7377fa735e431f7108e57735ff79a3f33aee662ee208e670ee79800d1585fa8c",
            grid_id=105034224363529183, controller_id=83468039688685570)

        with patch.object(CLI, "verify_block_lifecycle_prelaunch"), \
                patch.object(CLI, "_validate_local_vehicle_test_instrumentation",
                             return_value=91077977706459680):
            self._run(instance=instance_name)

        env = self._setenv(self.launch_args[-1])
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_READ_ONLY_BASELINE"], "true")
        self.assertEqual(env["VRAGECAGE_LOCAL_VEHICLE_TEST_BRAKE_ONLY"], "false")
        receipt = json.loads((Path(self.active["quarantine"]) / "authorization-receipt.json").read_text())
        self.assertEqual(receipt["scope"], "read-only-baseline")
        self.assertIs(receipt["actuator_commands"], False)
        self.assertIs(receipt["movement_authorized"], False)

    def test_hydro_launcher_rejects_restored_propulsion_enabled_isolated_config(self) -> None:
        instance_name = "land-local-test-hydro-scout-20261022-b"
        instance = self._prepare_local_vehicle_test(instance_name)
        config_path = instance / ".vragecage-local-vehicle-test.json"
        config = json.loads(config_path.read_text())
        config.update({"mode": "isolated-clear", "propulsion_enabled": True,
                       "isolated_clear": True})
        config_path.write_text(json.dumps(config))
        self._assert_local_vehicle_test_rejected(instance_name)

    def test_local_vehicle_test_preserves_existing_pinned_fixture(self) -> None:
        instance_name = "local-vehicle-steering-v4-20261021-e"
        source_hash = "6b5a94d32ce0958c53f30b75a928e037413fd8bebdaac7259108717c593ab8f0"
        self._prepare_local_vehicle_test(instance_name, source_hash=source_hash)
        self._assert_local_vehicle_test_rejected(instance_name)

    def test_retired_q_fixture_is_never_admitted_to_local_vehicle_test(self) -> None:
        instance_name = "local-vehicle-steering-v4-20261021-q"
        self._prepare_local_vehicle_test(instance_name)
        self._assert_local_vehicle_test_rejected(instance_name)

    def test_hydro_local_vehicle_test_rejects_missing_config_before_systemd(self) -> None:
        instance = self._prepare_local_vehicle_test()
        (instance / ".vragecage-local-vehicle-test.json").unlink()
        self._assert_local_vehicle_test_rejected()

    def test_local_vehicle_test_rejects_wrong_instance_binding(self) -> None:
        self._prepare_local_vehicle_test("land-local-test-hydro-scout-20261022-c")
        self._assert_local_vehicle_test_rejected("land-local-test-hydro-scout-20261022-c")

    def test_local_vehicle_test_rejects_config_instance_different_from_request(self) -> None:
        instance = self._prepare_local_vehicle_test()
        path = instance / ".vragecage-local-vehicle-test.json"
        config = json.loads(path.read_text())
        config["instance"] = "local-vehicle-steering-v4-20261021-e"
        path.write_text(json.dumps(config))
        self._assert_local_vehicle_test_rejected()

    def test_local_vehicle_test_rejects_extra_config_fields_and_boolean_numeric_limits(self) -> None:
        instance = self._prepare_local_vehicle_test()
        path = instance / ".vragecage-local-vehicle-test.json"
        config = json.loads(path.read_text())
        config["unreviewed"] = True
        path.write_text(json.dumps(config))
        self._assert_local_vehicle_test_rejected()

        config.pop("unreviewed")
        config["max_speed_meters_per_second"] = True
        path.write_text(json.dumps(config))
        self._assert_local_vehicle_test_rejected()

    def test_local_vehicle_test_rejects_wrong_grid_or_controller(self) -> None:
        for field, value in (("grid", 105034224363529184), ("controller", 83468039688685571)):
            with self.subTest(field=field):
                self.launch_args.clear()
                kwargs = {"grid_id": value} if field == "grid" else {"controller_id": value}
                self._prepare_local_vehicle_test(**kwargs)
                self._assert_local_vehicle_test_rejected()

    def test_local_vehicle_test_rejects_wrong_config_or_receipt_source_hash(self) -> None:
        for source, lab_overrides, import_overrides in (
            ("a" * 64, None, None),
            ("b1564710681a6d0bc901ab0437d59f475f62c1fabda7fdd6bd9198306a5982ed",
             {"source_tree_sha256": "a" * 64}, None),
            ("b1564710681a6d0bc901ab0437d59f475f62c1fabda7fdd6bd9198306a5982ed",
             None, {"tree_sha256": "a" * 64}),
        ):
            with self.subTest(source=source, lab=lab_overrides, imported=import_overrides):
                self.launch_args.clear()
                self._prepare_local_vehicle_test(source_hash=source, lab_overrides=lab_overrides,
                                                 import_overrides=import_overrides)
                self._assert_local_vehicle_test_rejected()

    def test_local_vehicle_test_rejects_camera_or_mechanical_pair_mismatch(self) -> None:
        instance = self._prepare_local_vehicle_test()
        config_path = instance / ".vragecage-local-vehicle-test.json"
        config = json.loads(config_path.read_text())
        config["camera_id"] += 1
        config_path.write_text(json.dumps(config))
        self._assert_local_vehicle_test_rejected()

        instance = self._prepare_local_vehicle_test()
        config_path = instance / ".vragecage-local-vehicle-test.json"
        config = json.loads(config_path.read_text())
        config["wheel_grid_ids"][0] += 1
        config_path.write_text(json.dumps(config))
        self._assert_local_vehicle_test_rejected()

        instance = self._prepare_local_vehicle_test()
        sector_path = instance / "World" / "SANDBOX_0_0_0_.sbs"
        sector_path.write_text(sector_path.read_text().replace("83781654658335329", "83781654658335330"))
        self._assert_local_vehicle_test_rejected()

    def test_hydro_prelaunch_rejects_missing_camera_or_controller(self) -> None:
        for block_id in (6902088205773520808, 112711005364517351):
            with self.subTest(block_id=block_id):
                instance = self._prepare_local_vehicle_test()
                sector_path = instance / "World" / "SANDBOX_0_0_0_.sbs"
                root = CLI.ET.parse(sector_path).getroot()
                grid = next(item for item in root.iter("MyObjectBuilder_EntityBase")
                            if item.findtext("EntityId") == "113504086011869676")
                blocks = grid.find("CubeBlocks")
                victim = next(item for item in list(blocks)
                              if item.findtext("EntityId") == str(block_id))
                blocks.remove(victim)
                sector_raw = CLI.ET.tostring(root, encoding="utf-8")
                sector_path.write_bytes(sector_raw)
                self._assert_local_vehicle_test_rejected()

    def test_local_vehicle_test_rejects_non_disposable_or_production_receipt(self) -> None:
        for overrides in ({"disposable": False}, {"authority": "production"}, {"engine": "vanilla"}):
            with self.subTest(overrides=overrides):
                self.launch_args.clear()
                self._prepare_local_vehicle_test(lab_overrides=overrides)
                self._assert_local_vehicle_test_rejected()

    def test_local_vehicle_test_rejects_values_over_each_bound(self) -> None:
        for kwargs in (
            {"speed": 0.200001}, {"displacement": 6.000001},
            {"timeout": 6}, {"watchdog": 61},
        ):
            with self.subTest(kwargs=kwargs):
                self.launch_args.clear()
                self._prepare_local_vehicle_test(**kwargs)
                self._assert_local_vehicle_test_rejected()


if __name__ == "__main__":
    unittest.main(verbosity=2)
