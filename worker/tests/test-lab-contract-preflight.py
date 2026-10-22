#!/usr/bin/env python3
"""Exercise fixture-snapshot binding without starting an engine instance."""

from __future__ import annotations

import hashlib
import copy
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOADER = importlib.machinery.SourceFileLoader(
    "vragecage_lab_contract", str(REPO_ROOT / "worker/lab-contract.py")
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None and SPEC.loader is not None
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)


class FixtureSnapshotPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="vragecage-fixture-snapshot-")
        self.instance = Path(self.temporary.name) / "instances" / "fixture-copy"
        world = self.instance / "World"
        world.mkdir(parents=True)
        self.sector = world / "SANDBOX_0_0_0_.sbs"
        self.sector.write_text('''<MyObjectBuilder_Sector xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Entities><MyObjectBuilder_EntityBase xsi:type="MyObjectBuilder_CubeGrid">
    <EntityId>45678</EntityId><DisplayName>Blue Truck</DisplayName><CubeBlocks>
      <MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_Cockpit"><EntityId>12345</EntityId></MyObjectBuilder_CubeBlock>
      <MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_CameraBlock"><EntityId>67890</EntityId></MyObjectBuilder_CubeBlock>
    </CubeBlocks>
  </MyObjectBuilder_EntityBase></Entities>
</MyObjectBuilder_Sector>''', encoding="utf-8")
        self.discovery_time = datetime.now(timezone.utc)
        (world / "Sandbox.sbc").write_text(
            "<MyObjectBuilder_Checkpoint><SessionName>test_suite_world_v4</SessionName></MyObjectBuilder_Checkpoint>",
            encoding="utf-8",
        )
        self.discovery_path = (self.instance / "Saves/test_suite_world_v4/Storage/VRage.Game/"
                               "agent-core-discovery-v1.json")
        self.discovery_path.parent.mkdir(parents=True)
        self.discovery = {
            "schemaVersion": 1, "scope": "physical_grid_group",
            "capturedAtUtc": self.discovery_time.isoformat(), "capturedSimulationTick": 900,
            "runId": "d" * 32, "agentsTruncated": False,
            "agents": [{
                "coreEntityId": 11111, "anchorGridEntityId": 45678,
                "anchorGridName": "Blue Truck", "physicalGridCountObserved": 1,
                "physicalGridCountIncluded": 1, "physicalGridInventoryComplete": True,
                "typedBlockInventoryComplete": True, "mechanicalConnectionInventoryComplete": True,
                "blocksObserved": 3, "physicalGrids": [{
                    "entityId": 45678, "name": "Blue Truck", "blockCountObserved": 3,
                    "blocks": [
                        {"gridEntityId": 45678, "entityId": 12345, "typedKind": "controller"},
                        {"gridEntityId": 45678, "entityId": 67890, "typedKind": "camera"},
                        {"gridEntityId": 45678, "entityId": 77777, "typedKind": "suspension",
                         "hasSuspensionState": True, "steeringEnabled": True,
                         "propulsionEnabled": True, "invertSteer": False,
                         "invertPropulsion": False},
                    ],
                }],
            }],
        }
        self.write_discovery()
        world_text = str(world.resolve())
        (self.instance / "SpaceEngineers-Dedicated.cfg").write_text(
            f"<DedicatedServer><LoadWorld>{world_text}</LoadWorld></DedicatedServer>",
            encoding="utf-8",
        )
        source_hash = "a" * 64
        fixture_id = "b" * 32
        run_id = "c" * 32
        contract = {
            "schema": "vragecage.lab-authority.v1", "authority": "destructive-lab",
            "disposable": True, "instance": self.instance.name, "engine": "magnetar",
            "source_tree_sha256": source_hash, "fixture_id": fixture_id,
            "quarantine": "Quarantine",
        }
        (self.instance / ".vragecage-world-import.json").write_text(json.dumps({
            "instance": self.instance.name, "tree_sha256": source_hash, "engine": "magnetar",
        }), encoding="utf-8")
        LAB.atomic_write(self.instance / LAB.LAB_FILE, contract)
        run_dir = self.instance / "Quarantine" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
        self.active = {
            "schema": "vragecage.lab-run.v1", "authority": "destructive-lab",
            "fixture_id": fixture_id, "instance": self.instance.name, "run_id": run_id,
            "quarantine": str(run_dir.resolve()),
            "contract_sha256": hashlib.sha256(LAB.canonical(contract)).hexdigest(),
            "fixture_sector_sha256": hashlib.sha256(self.sector.read_bytes()).hexdigest(),
            "fixture_edit_count": 0,
        }
        LAB.atomic_write(self.instance / LAB.ACTIVE_FILE, self.active)

    def write_discovery(self, value=None) -> None:
        self.discovery_path.write_text(json.dumps(self.discovery if value is None else value), encoding="utf-8")

    def test_live_discovery_resolves_exact_complete_grid_binding(self) -> None:
        binding = LAB.discover_direct_target(self.instance, self.discovery_time)
        self.assertEqual((binding["core_id"], binding["grid_id"], binding["controller_id"], binding["camera_id"]),
                         (11111, 45678, 12345, 67890))
        self.assertEqual(binding["suspension_ids"], [77777])
        self.assertEqual(binding["run_id"], "d" * 32)
        self.assertEqual(binding["tick"], 900)
        self.assertEqual(len(binding["fingerprint"]), 64)

    def test_direct_target_accepts_exact_same_grid_cockpit_controller(self) -> None:
        namespace = "{http://www.w3.org/2001/XMLSchema-instance}type"
        tree = LAB.ET.parse(self.sector)
        controller = next(node for node in tree.getroot().iter()
                          if node.findtext("EntityId") == "12345")
        controller.set(namespace, "MyObjectBuilder_Cockpit")
        tree.write(self.sector, encoding="unicode")
        binding = LAB.require_direct_target_identity(self.instance, None, None, None)
        self.assertEqual(binding["controller_id"], 12345)

    def test_discovery_uses_configured_session_vrage_game_storage(self) -> None:
        decoy = self.instance / "World/Storage/agent-core-discovery-v1.json"
        decoy.parent.mkdir(parents=True)
        decoy.write_text("{}", encoding="utf-8")
        self.assertEqual(LAB._agent_discovery_storage(self.instance), self.discovery_path.parent)
        self.assertEqual(LAB.discover_direct_target(self.instance, self.discovery_time)["grid_id"], 45678)

    def test_discovery_storage_rejects_unsafe_session_name_and_links(self) -> None:
        sandbox = self.instance / "World/Sandbox.sbc"
        sandbox.write_text("<Checkpoint><SessionName>../escape</SessionName></Checkpoint>", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "safe SessionName"):
            LAB._agent_discovery_storage(self.instance)
        sandbox.write_text("<Checkpoint><SessionName>test_suite_world_v4</SessionName></Checkpoint>", encoding="utf-8")
        storage = self.instance / "Saves/test_suite_world_v4/Storage/VRage.Game"
        self.discovery_path.unlink()
        storage.rmdir()
        storage.symlink_to(self.instance / "World")
        with self.assertRaisesRegex(SystemExit, "symbolic link"):
            LAB._agent_discovery_storage(self.instance)

    def test_live_discovery_rejects_missing_duplicate_stale_and_incomplete(self) -> None:
        self.discovery_path.unlink()
        with self.assertRaisesRegex(SystemExit, "missing or ambiguous"):
            LAB.discover_direct_target(self.instance, self.discovery_time)
        self.write_discovery()
        duplicate = json.loads(json.dumps(self.discovery))
        duplicate["agents"].append(json.loads(json.dumps(duplicate["agents"][0])))
        self.write_discovery(duplicate)
        with self.assertRaisesRegex(SystemExit, "multiple complete physical grids are ambiguous"):
            LAB.discover_direct_target(self.instance, self.discovery_time)
        self.write_discovery()
        with self.assertRaisesRegex(SystemExit, "stale or from the future"):
            LAB.discover_direct_target(self.instance, self.discovery_time.replace(year=2020))
        incomplete = json.loads(json.dumps(self.discovery))
        incomplete["agents"][0]["typedBlockInventoryComplete"] = False
        self.write_discovery(incomplete)
        with self.assertRaisesRegex(SystemExit, "inventory is incomplete"):
            LAB.discover_direct_target(self.instance, self.discovery_time)

    def test_live_discovery_rejects_wrong_grid_components(self) -> None:
        wrong_grid = json.loads(json.dumps(self.discovery))
        wrong_grid["agents"][0]["physicalGrids"][0]["blocks"][0]["gridEntityId"] = 999
        self.write_discovery(wrong_grid)
        with self.assertRaisesRegex(SystemExit, "cross-grid block"):
            LAB.discover_direct_target(self.instance, self.discovery_time)
        moved_core = json.loads(json.dumps(self.discovery))
        moved_core["agents"][0]["anchorGridEntityId"] = 999
        self.write_discovery(moved_core)
        with self.assertRaisesRegex(SystemExit, "no complete Agent Core vehicle"):
            LAB.discover_direct_target(self.instance, self.discovery_time)

    def test_stage_two_activation_binds_fresh_current_session_and_is_preflight_only(self) -> None:
        nonce = "e" * 32
        receipt = {
            "schema": LAB.DISCOVERY_RECEIPT_SCHEMA, "bootstrapNonce": nonce,
            "sessionId": "f" * 32, "runId": "1" * 32,
            "capturedSimulationTick": 900,
            "capturedAtUtc": self.discovery_time.isoformat(),
            "exportSha256": LAB.discover_direct_target(self.instance, self.discovery_time)["fingerprint"],
            "coreEntityId": 11111, "gridEntityId": 45678,
            "controllerEntityId": 12345, "cameraEntityId": 67890,
            "uniqueBlueTruck": True, "inventoryComplete": True,
        }
        binding = {
            "run_id": receipt["runId"], "tick": receipt["capturedSimulationTick"],
            "fingerprint": receipt["exportSha256"], "core_id": 11111, "grid_id": 45678,
            "controller_id": 12345, "camera_id": 67890,
        }
        activation = LAB.build_command_activation_contract(
            "a" * 32, "b" * 32, "c" * 32, nonce, "d" * 32,
            receipt, binding, self.discovery_time,
        )
        self.assertEqual(activation["stage"], 2)
        self.assertEqual(activation["context"]["session_id"], receipt["sessionId"])
        self.assertEqual(activation["context"]["discovery_run_id"], receipt["runId"])
        self.assertEqual(activation["context"]["action"], "preflight-only")
        self.assertFalse(activation["motion_dispatch"])
        self.assertEqual(activation["context_sha256"], hashlib.sha256(
            LAB.canonical(activation["context"])
        ).hexdigest())

    def test_same_session_zero_hold_stage_two_command_is_fixed_and_identity_bound(self) -> None:
        binding = LAB.discover_direct_target(self.instance, self.discovery_time)
        command = LAB._build_stage_two_command("direct-maneuver", self.active["fixture_id"],
                                               "c" * 32, binding, binding["fingerprint"])
        contract = json.loads((self.instance / LAB.LAB_FILE).read_text(encoding="utf-8"))
        LAB.validate_lab_command(command, contract)
        self.assertEqual((command["controller_id"], command["grid_id"], command["depth_camera_id"],
                          command["agent_core_id"], command["discovery_run_id"], command["discovery_tick"]),
                         (12345, 45678, 67890, 11111, "d" * 32, 900))
        self.assertEqual(command["timeout_ticks"], 180)
        self.assertEqual(command["segments"], [{"kind": "stationary-hold", "value": 0.0,
                                                "max_speed": 0.0, "timeout_ticks": 60}])
        altered = copy.deepcopy(command)
        altered["max_speed"] = 0.2
        with self.assertRaises(SystemExit):
            LAB.validate_lab_command(altered, contract)
        altered = copy.deepcopy(command)
        altered["discovery_tick"] += 1
        with self.assertRaises(SystemExit):
            LAB.require_direct_target_identity(self.instance, altered["controller_id"],
                                               altered["grid_id"], altered["depth_camera_id"], altered)

    def test_stage_two_activation_rejects_session_replay_nonce_age_and_binding_mismatch(self) -> None:
        nonce = "e" * 32
        receipt = {
            "schema": LAB.DISCOVERY_RECEIPT_SCHEMA, "bootstrapNonce": nonce,
            "sessionId": "f" * 32, "runId": "1" * 32,
            "capturedSimulationTick": 901,
            "capturedAtUtc": self.discovery_time.isoformat(), "exportSha256": "2" * 64,
            "coreEntityId": 11111, "gridEntityId": 45678,
            "controllerEntityId": 12345, "cameraEntityId": 67890,
            "uniqueBlueTruck": True, "inventoryComplete": True,
        }
        binding = {
            "run_id": receipt["runId"], "tick": receipt["capturedSimulationTick"],
            "fingerprint": receipt["exportSha256"], "core_id": 11111, "grid_id": 45678,
            "controller_id": 12345, "camera_id": 67890,
        }
        base_args = ("a" * 32, "b" * 32, "c" * 32, nonce, "d" * 32)
        with self.assertRaisesRegex(SystemExit, "stale-session"):
            LAB.build_command_activation_contract(*base_args[:4], receipt["runId"], receipt, binding,
                                                  self.discovery_time)
        wrong_nonce = dict(receipt, bootstrapNonce="9" * 32)
        with self.assertRaisesRegex(SystemExit, "stale-session"):
            LAB.build_command_activation_contract(*base_args, wrong_nonce, binding, self.discovery_time)
        with self.assertRaisesRegex(SystemExit, "differs from the complete current export"):
            LAB.build_command_activation_contract(*base_args, receipt, dict(binding, grid_id=999),
                                                  self.discovery_time)
        stale = dict(receipt, capturedAtUtc="2020-01-01T00:00:00+00:00")
        with self.assertRaisesRegex(SystemExit, "stale or from the future"):
            LAB.build_command_activation_contract(*base_args, stale, binding, self.discovery_time)

    def test_vwdr2_receipt_rejects_stale_nonce_and_session_replay(self) -> None:
        now = datetime.now(timezone.utc)
        receipt = {"status": "mod-ready", "run_id": "a" * 32, "fixture_id": "b" * 32,
                   "nonce": "c" * 32, "session_id": "d" * 32, "exporter_run_id": "e" * 32,
                   "tick": 40, "fingerprint": "f" * 64, "observed_grid_count": 1,
                   "captured": now}
        args = (receipt, "mod-ready", "a" * 32, "b" * 32, "c" * 32, "d" * 32,
                "e" * 32, 40, "f" * 64, now)
        LAB._validate_discovery_receipt_binding(*args)
        for altered in (dict(receipt, nonce="9" * 32), dict(receipt, session_id="9" * 32),
                        dict(receipt, captured=now.replace(year=2020))):
            with self.assertRaisesRegex(SystemExit, "stale or mismatched"):
                LAB._validate_discovery_receipt_binding(altered, *args[1:])

    def test_vwaa1_ack_rejects_replay_mismatch_and_timeout(self) -> None:
        now = datetime.now(timezone.utc)
        issued = now.replace(microsecond=0)
        fields = ["host-accepted", "a" * 32, "b" * 32, "c" * 32, "d" * 32,
                  "e" * 32, "f" * 32, "40", "1" * 64, "2" * 64,
                  now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"]
        raw = "VWAA1\n" + "|".join(fields) + "\n"
        expected = ("a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32,
                    "f" * 32, 40, "1" * 64, "2" * 64)
        self.assertTrue(LAB._ack_matches(raw, "host-accepted", expected, issued, now))
        self.assertFalse(LAB._ack_matches(raw, "host-accepted", expected[:-1] + ("3" * 64,), issued, now))
        self.assertFalse(LAB._ack_matches(raw, "mod-accepted", expected, issued, now))
        self.assertFalse(LAB._ack_matches(raw, "host-accepted", expected, now + LAB.AGENT_DISCOVERY_MAX_AGE, now))
        self.assertFalse(LAB._ack_matches("", "host-accepted", expected, issued, now))
        with self.assertRaisesRegex(SystemExit, "timed out"):
            LAB._wait_for_stage_two_acknowledgments(self.discovery_path, "a" * 32,
                                                    expected, issued, 0)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def registered_mod_hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix().encode()
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def stage_multiplayer_mod(self, mod_name: str = "Voidwright") -> tuple[Path, Path, int, str]:
        source = self.instance / "Mods" / "Voidwright"
        (source / "Data").mkdir(parents=True, exist_ok=True)
        (source / "Data" / "mod.sbc").write_text("<Definitions />", encoding="utf-8")
        (source / "modinfo.sbmi").write_text("fixture package", encoding="utf-8")
        worker_root = self.instance.parent.parent
        workshop_id = 90_012_345_678_901_234
        cache = worker_root / "magnetar-steam/steamapps/workshop/content/244850" / str(workshop_id)
        cache.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        if cache.exists():
            shutil.rmtree(cache)
        shutil.copytree(source, cache)
        digest = self.registered_mod_hash(cache)
        (self.instance / ".vragecage-mod-stage.json").write_text(json.dumps({
            "schema": "vragecage.mod-stage.v1", "engine": "magnetar",
            "instance": self.instance.name, "name": mod_name,
            "synthetic_workshop_id": workshop_id, "path": str(cache), "tree_sha256": digest,
        }), encoding="utf-8")
        (self.instance / "MagnetarConfig/Sources").mkdir(parents=True, exist_ok=True)
        (self.instance / "MagnetarConfig/Profiles").mkdir(parents=True, exist_ok=True)
        (self.instance / "MagnetarConfig/Sources/sources.xml").write_text(
            f'<SourcesConfig><ModSources><Mod><Name>{mod_name}</Name><ID>{workshop_id}</ID>'
            '<Enabled>true</Enabled></Mod></ModSources></SourcesConfig>', encoding="utf-8")
        (self.instance / "MagnetarConfig/Profiles/Current.xml").write_text(
            f'<Profile><Mods><unsignedLong>{workshop_id}</unsignedLong></Mods></Profile>', encoding="utf-8")
        for name in ("Sandbox.sbc", "Sandbox_config.sbc"):
            (self.instance / "World" / name).write_text("<MyObjectBuilder_World />", encoding="utf-8")
        return source, cache, workshop_id, digest

    def test_preflight_context_captures_activated_sector_digest(self) -> None:
        context = LAB.create_preflight_context(self.instance, self.active["run_id"])
        payload = Path(context["path"]).read_bytes()
        self.assertEqual(context["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertIn(
            "fixture_sector_sha256=" + self.active["fixture_sector_sha256"] + "\n",
            payload.decode("utf-8"),
        )

    def test_direct_maneuver_contract_binds_grid_camera_segments_and_limits(self) -> None:
        contract = json.loads((self.instance / LAB.LAB_FILE).read_text())
        command = {
            "schema": "vragecage.lab-command.v1", "action": "direct-maneuver",
            "fixture_id": self.active["fixture_id"], "operation_id": "d" * 32,
            "controller_id": 12345, "grid_id": 45678, "depth_camera_id": 67890,
            "agent_core_id": 11111, "discovery_run_id": "d" * 32,
            "discovery_tick": 900,
            "discovery_fingerprint": LAB.discover_direct_target(self.instance, self.discovery_time)["fingerprint"],
            "target": [0.0, 0.0, 0.0], "arrival_radius": 0.5, "start_delay_ticks": 0,
            "max_speed": 0.25, "reverse": False, "timeout_ticks": 960,
            "segments": [
                {"kind": "steer-angle", "value": 35, "max_speed": 0, "timeout_ticks": 60},
                {"kind": "forward-distance", "value": 1, "max_speed": 0.25, "timeout_ticks": 240},
                {"kind": "stop", "value": 0, "max_speed": 0, "timeout_ticks": 120},
                {"kind": "reverse-distance", "value": 0.5, "max_speed": 0.25, "timeout_ticks": 180},
                {"kind": "stop", "value": 0, "max_speed": 0, "timeout_ticks": 120},
                {"kind": "stationary-hold", "value": 0, "max_speed": 0, "timeout_ticks": 120},
            ],
        }
        LAB.validate_lab_command(command, contract)
        invalid = copy.deepcopy(command)
        invalid["timeout_ticks"] -= LAB.DIRECT_STOP_CONFIRMATION_TICKS
        with self.assertRaisesRegex(SystemExit, "total distance or runtime limits"):
            LAB.validate_lab_command(invalid, contract)
        invalid = copy.deepcopy(command)
        invalid["grid_id"] = 0
        with self.assertRaisesRegex(SystemExit, "positive exact controller, grid, and same-grid sensor"):
            LAB.validate_lab_command(invalid, contract)
        invalid = copy.deepcopy(command)
        invalid["segments"][-1]["kind"] = "stop"
        with self.assertRaisesRegex(SystemExit, "stationary hold"):
            LAB.validate_lab_command(invalid, contract)
        invalid = copy.deepcopy(command)
        invalid["segments"][3]["value"] = 3.01
        with self.assertRaisesRegex(SystemExit, "reverse segment"):
            LAB.validate_lab_command(invalid, contract)

    def test_preflight_only_command_has_no_motion_fields(self) -> None:
        command = LAB.plan_preflight_only(self.instance, None, None, None)
        contract = json.loads((self.instance / LAB.LAB_FILE).read_text())
        self.assertEqual(command["action"], "preflight-only")
        self.assertEqual(command["grid_id"], 45678)
        self.assertEqual(command["controller_id"], 12345)
        self.assertEqual(command["depth_camera_id"], 67890)
        self.assertEqual(command["agent_core_id"], 11111)
        self.assertEqual(command["discovery_run_id"], "d" * 32)
        self.assertEqual(command["discovery_tick"], 900)
        self.assertEqual(command["depth_camera_id"], 67890)
        self.assertEqual(command["target"], [0.0, 0.0, 0.0])
        self.assertFalse(command["reverse"])
        self.assertNotIn("segments", command)
        self.assertNotIn("distance_meters", command)
        LAB.validate_lab_command(command, contract)

    def test_direct_target_ignores_grid_display_name_and_uses_exact_ids(self) -> None:
        source = self.sector.read_text(encoding="utf-8")
        self.sector.write_text(source.replace("Blue Truck", "Codex Truck"), encoding="utf-8")
        result = LAB.plan_preflight_only(self.instance, 12345, 45678, 67890)
        self.assertEqual(result["grid_id"], 45678)

    def test_direct_target_requires_exact_grid_and_controller_ids(self) -> None:
        for controller_id, grid_id in ((99999, 45678), (12345, 99999)):
            with self.subTest(controller_id=controller_id, grid_id=grid_id):
                with self.assertRaisesRegex(SystemExit, "fresh Agent Core discovery|exact physical-grid binding|manually supplied target IDs"):
                    LAB.plan_preflight_only(self.instance, controller_id, grid_id, 67890)
                self.assertFalse((self.instance / LAB.PENDING_FILE).exists())

    def test_direct_target_rejects_controller_id_duplicated_on_another_grid(self) -> None:
        source = self.sector.read_text(encoding="utf-8")
        extra_grid = '''<MyObjectBuilder_EntityBase xsi:type="MyObjectBuilder_CubeGrid">
    <EntityId>99999</EntityId><DisplayName>Other Grid</DisplayName><CubeBlocks>
      <MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_Cockpit"><EntityId>12345</EntityId></MyObjectBuilder_CubeBlock>
    </CubeBlocks>
  </MyObjectBuilder_EntityBase>'''
        self.sector.write_text(source.replace("</Entities>", extra_grid + "</Entities>"), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "exact physical grid, cockpit, and camera"):
            LAB.plan_preflight_only(self.instance, 12345, 45678, 67890)
        self.assertFalse((self.instance / LAB.PENDING_FILE).exists())

    def test_direct_target_rejects_remote_control_even_when_discovery_calls_it_a_controller(self) -> None:
        source = self.sector.read_text(encoding="utf-8")
        self.sector.write_text(source.replace(
            "MyObjectBuilder_Cockpit", "MyObjectBuilder_RemoteControl", 1), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "cockpit"):
            LAB.plan_preflight_only(self.instance, 12345, 45678, 67890)
        self.assertFalse((self.instance / LAB.PENDING_FILE).exists())

    def test_direct_target_rejects_missing_cockpit_type_proof(self) -> None:
        source = self.sector.read_text(encoding="utf-8")
        source = source.replace(
            '<MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_Cockpit"><EntityId>12345</EntityId></MyObjectBuilder_CubeBlock>',
            '<MyObjectBuilder_CubeBlock xsi:type="MyObjectBuilder_Unknown"><EntityId>12345</EntityId></MyObjectBuilder_CubeBlock>',
            1)
        self.sector.write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "cockpit"):
            LAB.plan_preflight_only(self.instance, 12345, 45678, 67890)
        self.assertFalse((self.instance / LAB.PENDING_FILE).exists())

    def test_plan_direct_maneuver_writes_one_immutable_pending_command(self) -> None:
        segments = [
            {"kind": "forward-distance", "value": 1.0, "max_speed": 0.25, "timeout_ticks": 240},
            {"kind": "stop", "value": 0.0, "max_speed": 0.0, "timeout_ticks": 120},
            {"kind": "stationary-hold", "value": 0.0, "max_speed": 0.0, "timeout_ticks": 120},
        ]
        command = LAB.plan_direct_maneuver(self.instance, None, None, None, segments)
        self.assertEqual(command["action"], "direct-maneuver")
        self.assertEqual((command["agent_core_id"], command["grid_id"], command["controller_id"],
                          command["depth_camera_id"]), (11111, 45678, 12345, 67890))
        self.assertEqual(command["discovery_run_id"], "d" * 32)
        self.assertEqual(command["discovery_tick"], 900)
        self.assertEqual(command["timeout_ticks"], 600)
        self.assertEqual(command["max_speed"], 0.25)
        self.assertEqual(json.loads((self.instance / LAB.PENDING_FILE).read_text()), command)
        with self.assertRaisesRegex(SystemExit, "already pending"):
            LAB.plan_direct_maneuver(self.instance, 12345, 45678, 67890, segments)

    def test_zero_motion_direct_maneuver_uses_fixed_host_envelope_without_actuation(self) -> None:
        segments = [
            {"kind": "steer-angle", "value": 0.0, "max_speed": 0.0, "timeout_ticks": 120},
            {"kind": "stationary-hold", "value": 0.0, "max_speed": 0.0, "timeout_ticks": 120},
        ]
        command = LAB.plan_direct_maneuver(self.instance, 12345, 45678, 67890, segments)
        contract = json.loads((self.instance / LAB.LAB_FILE).read_text())
        self.assertEqual(command["max_speed"], 0.1)
        self.assertTrue(all(segment["value"] == 0 and segment["max_speed"] == 0
                            for segment in command["segments"]))
        LAB.validate_lab_command(command, contract)
        invalid = copy.deepcopy(command)
        invalid["max_speed"] = 0.2
        with self.assertRaisesRegex(SystemExit, "total distance or runtime limits"):
            LAB.validate_lab_command(invalid, contract)

    def test_preflight_rejects_sector_changed_after_activation(self) -> None:
        self.sector.write_bytes(b"changed after the activated fixture snapshot")
        with self.assertRaisesRegex(SystemExit, "fixture sector changed after activation"):
            LAB.create_preflight_context(self.instance, self.active["run_id"])

    def test_depth_activation_missing_local_mod_does_not_consume_command(self) -> None:
        command = {
            "schema": "vragecage.lab-command.v1", "action": "drive-to-coordinate-depth",
            "fixture_id": self.active["fixture_id"], "operation_id": "d" * 32,
            "controller_id": 12345, "depth_camera_id": 67890,
            "zone": "Green Zone", "target": [1, 2, 3], "max_speed": 0.5,
            "arrival_radius": 1.0, "timeout_ticks": 600,
        }
        pending_path = self.instance / LAB.PENDING_FILE
        LAB.atomic_write(pending_path, command)
        active_path = self.instance / LAB.ACTIVE_FILE
        active_before = active_path.read_bytes()
        pending_before = pending_path.read_bytes()
        quarantine_entries_before = set((self.instance / "Quarantine").iterdir())

        with self.assertRaisesRegex(SystemExit, "exact Voidwright source package"):
            LAB.activate(self.instance)

        self.assertEqual(active_path.read_bytes(), active_before)
        self.assertEqual(pending_path.read_bytes(), pending_before)
        self.assertEqual(set((self.instance / "Quarantine").iterdir()), quarantine_entries_before)

    def test_multiplayer_mod_requires_synthetic_registration_and_no_local_world_entry(self) -> None:
        source, cache, _, _ = self.stage_multiplayer_mod()
        mods_path, _ = LAB.verify_voidwright_mod_staged(self.instance)
        self.assertEqual(mods_path, self.instance / "Mods")
        self.assertEqual(self.registered_mod_hash(source), self.registered_mod_hash(cache))

        config = self.instance / "World/Sandbox_config.sbc"
        config.write_text(
            '<MyObjectBuilder_World><Mods><ModItem><Name>Voidwright</Name>'
            '<PublishedFileId>0</PublishedFileId></ModItem></Mods></MyObjectBuilder_World>',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SystemExit, "rejected by multiplayer"):
            LAB.verify_voidwright_mod_staged(self.instance)

    def test_multiplayer_mod_accepts_unique_instance_alias_bound_by_id(self) -> None:
        self.stage_multiplayer_mod("VoidwrightLifecycleDiagBlueTruckU")
        _, tree_hash = LAB.verify_voidwright_mod_staged(self.instance)
        self.assertRegex(tree_hash, r"^[0-9a-f]{64}$")

    def test_multiplayer_mod_rejects_stage_hash_or_identity_mismatch(self) -> None:
        _, _, _, _ = self.stage_multiplayer_mod()
        receipt = self.instance / ".vragecage-mod-stage.json"
        stage = json.loads(receipt.read_text())
        stage["tree_sha256"] = "0" * 64
        receipt.write_text(json.dumps(stage), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "hashes do not match"):
            LAB.verify_voidwright_mod_staged(self.instance)

        _, _, _, _ = self.stage_multiplayer_mod()
        stage = json.loads(receipt.read_text())
        stage["instance"] = "another-fixture"
        receipt.write_text(json.dumps(stage), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "does not identify this instance"):
            LAB.verify_voidwright_mod_staged(self.instance)

        _, _, _, _ = self.stage_multiplayer_mod()
        stage = json.loads(receipt.read_text())
        stage["path"] = str(Path(self.temporary.name) / "outside-cache")
        receipt.write_text(json.dumps(stage), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "points outside this worker"):
            LAB.verify_voidwright_mod_staged(self.instance)


if __name__ == "__main__":
    unittest.main(verbosity=2)
