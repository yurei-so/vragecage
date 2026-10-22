"""Real fixture contracts/journals; fake service and physics. No engine starts."""
import importlib.util
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("lab_trial", Path(__file__).resolve().parents[1] / "lab-trial.py")
trial_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial_module)


class FakeCli:
    def __init__(self, instance, lab):
        self.instance = instance
        self.lab = lab
        self.calls = []
        self.active = False
        self.failure = None
        self.command = None

    def call(self, args, timeout=30):
        self.calls.append(args)
        action = args[0]
        if action == "status":
            return 0, {"schema": "vragecage.status.v1", "instance": self.instance.name,
                       "service": {"ActiveState": "active" if self.active else "inactive"}}
        if action == "lab-drive-zone":
            self.command = self.lab.plan_drive_to(self.instance, int(args[2]), args[3], [10.0, 20.0, 30.0],
                                                  0.5, 3.1, 1800, depth_camera_id=444)
            return 0, self.command
        if action == "run":
            if self.failure == "pending-replaced":
                pending = self.lab.load_object(self.instance / self.lab.PENDING_FILE)
                pending["operation_id"] = "f" * 32
                self.lab.atomic_write(self.instance / self.lab.PENDING_FILE, pending)
            activated = self.lab.activate(self.instance, args[args.index("--expected-operation-id") + 1],
                                          args[args.index("--expected-command-sha256") + 1])
            self.active = True
            snapshot = {"schemaVersion": 1, "runId": "2" * 32, "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
                        "capturedSimulationTick": 100, "agents": [], "playerObservation": {
                            "schemaVersion": 1, "capturedSimulationTick": 100, "connectedHumanPlayerCount": 0,
                            "status": "no_connected_humans", "characterObserved": False}}
            if self.failure in {"human", "human-observer"}:
                snapshot["playerObservation"].update(connectedHumanPlayerCount=1, status="character_not_spawned")
                if self.failure == "human-observer":
                    snapshot["playerObservation"].update(status="observed", characterObserved=True)
            if self.failure == "stale":
                snapshot["capturedAtUtc"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            if self.failure == "preactivation":
                snapshot["capturedAtUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            self.lab.atomic_write(self.instance / "World/Storage/exporter/agent-core-snapshot-v1.json", snapshot)
            if self.failure == "start-error":
                raise TimeoutError("Startup acknowledgement lost after activation")
            return 0, {"schema": "vragecage.operation.v1", "status": "starting"}
        if action == "wait":
            if self.failure == "interrupted":
                raise InterruptedError("Fixture interrupted")
            if self.failure == "foreign-run":
                self.lab.activate(self.instance)
            return (1 if self.failure == "stalled" else 0), {"receipt": self.receipt(False)}
        if action == "receipt":
            return 0, self.receipt(False)
        if action == "stop":
            if self.failure == "replaced-before-stop":
                self.lab.activate(self.instance)
            self.lab.verify_runtime(self.instance, args[args.index("--expected-run-id") + 1])
            self.active = False
            return 0, {"status": "stopped", "receipt": self.receipt(True)}
        raise AssertionError("Unexpected fake CLI action " + action)

    def receipt(self, clean):
        active = self.lab.load_object(self.instance / self.lab.ACTIVE_FILE)
        status = {"stalled": "stalled", "human": "human-connected", "human-lag": "human-connected",
                  "api-lag": "players-api-unavailable"}.get(self.failure, "complete")
        result = {"schema": "vragecage.magnetar-receipt.v1", "lab_run_id": active["run_id"],
                  "lab_operation_id": self.command["operation_id"], "lab_operation_status": status,
                  "lab_operation_terminal": True, "lab_contract_sha256": active["contract_sha256"],
                  "lab_contract_valid": True, "lab_authority": "destructive-lab", "passed": status == "complete",
                  "staged_mod_tree_sha256": trial_module.tree_hash(
                      self.instance.parent.parent / "magnetar-steam/steamapps/workshop/content/244850/90012345678901234"),
                  "staged_mod_unchanged": True,
                  "clean_shutdown": clean, "staged_plugin_tree_sha256": trial_module.tree_hash(self.instance / "Plugin"),
                  "staged_plugin_unchanged": True}
        if self.failure == "wrong-operation":
            result["lab_operation_id"] = "9" * 32
        if self.failure == "wrong-package":
            result["staged_plugin_tree_sha256"] = "9" * 64
        return result


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vragecage-trial-test-")
        self.addCleanup(self.temporary.cleanup)
        self.instance = Path(self.temporary.name) / "worker/instances/fixture"
        (self.instance / "World/Storage/exporter").mkdir(parents=True)
        (self.instance / "Mods/Voidwright/Data/Scripts").mkdir(parents=True)
        (self.instance / "Mods/Voidwright/Data/Scripts/Voidwright.dll").write_text("synthetic mod package\n")
        (self.instance / "Plugin").mkdir()
        (self.instance / "Plugin/Plugin.cs").write_text("// Synthetic fixture only\n")
        self.lab = trial_module.sibling("lab-contract")
        self.lab.atomic_write(self.instance / ".vragecage-world-import.json", {
            "instance": "fixture", "engine": "magnetar", "tree_sha256": "a" * 64})
        (self.instance / ".vragecage-engine").write_text("magnetar\n")
        (self.instance / "SpaceEngineers-Dedicated.cfg").write_text(
            "<Config><LoadWorld>" + str(self.instance / "World") + "</LoadWorld></Config>")
        (self.instance / "World/Sandbox.sbc").write_text(
            "<MyObjectBuilder_Checkpoint><SessionName>fixture-world</SessionName></MyObjectBuilder_Checkpoint>")
        (self.instance / "World/Sandbox.sbc").write_text('''<Checkpoint><SessionName>fixture-world</SessionName><Mods /><Gps><dictionary><item><Value><Entries>
<Entry><name>End Position</name><coords><X>10</X><Y>20</Y><Z>30</Z></coords></Entry>
</Entries></Value></item></dictionary></Gps></Checkpoint>''')
        (self.instance / "World/Sandbox_config.sbc").write_text('''<WorldConfiguration><Mods /></WorldConfiguration>''')
        # Depth runs require the same receipt-bound synthetic Magnetar package as
        # multiplayer fixtures; a local PublishedFileId=0 entry is rejected by SE.
        mod_source = self.instance / "Mods/Voidwright"
        worker_root = self.instance.parent.parent
        synthetic_id = 90_012_345_678_901_234
        cache = worker_root / "magnetar-steam/steamapps/workshop/content/244850" / str(synthetic_id)
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(mod_source, cache)
        staged_hash = hashlib.sha256()
        for path in sorted(item for item in cache.rglob("*") if item.is_file()):
            relative = path.relative_to(cache).as_posix().encode()
            staged_hash.update(len(relative).to_bytes(4, "big"))
            staged_hash.update(relative)
            staged_hash.update(path.read_bytes())
        mod_hash = staged_hash.hexdigest()
        self.lab.atomic_write(self.instance / ".vragecage-mod-stage.json", {
            "schema": "vragecage.mod-stage.v1", "engine": "magnetar",
            "instance": self.instance.name, "name": "Voidwright",
            "synthetic_workshop_id": synthetic_id, "path": str(cache), "tree_sha256": mod_hash,
        })
        (self.instance / "MagnetarConfig/Sources").mkdir(parents=True)
        (self.instance / "MagnetarConfig/Profiles").mkdir(parents=True)
        (self.instance / "MagnetarConfig/Sources/sources.xml").write_text(
            f'<SourcesConfig><ModSources><Mod><Name>Voidwright</Name><ID>{synthetic_id}</ID>'
            '<Enabled>true</Enabled></Mod></ModSources></SourcesConfig>')
        (self.instance / "MagnetarConfig/Profiles/Current.xml").write_text(
            f'<Profile><Mods><unsignedLong>{synthetic_id}</unsignedLong></Mods></Profile>')
        (self.instance / "World/SANDBOX_0_0_0_.sbs").write_text('''<Sector xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<MyObjectBuilder_EntityBase xsi:type="MyObjectBuilder_CubeGrid"><EntityId>106313676705820376</EntityId>
<DisplayName>Ordinary Truck</DisplayName><IsStatic>false</IsStatic><CubeBlocks>
<Block xsi:type="MyObjectBuilder_RemoteControl"><EntityId>333</EntityId></Block>
<Block xsi:type="MyObjectBuilder_CameraBlock"><EntityId>444</EntityId></Block>
</CubeBlocks></MyObjectBuilder_EntityBase></Sector>''')
        self.fixture = self.lab.create(self.instance)
        self.lab.atomic_write(self.instance / ".vragecage-plugin-stage.json", {
            "schema": "vragecage.plugin-stage.v1", "instance": "fixture", "path": str(self.instance / "Plugin"),
            "contract": {"schema": "vragecage.plugin-contract.v1",
                         "expected_log_markers": ["Voidwright server plugin: destructive-lab authority verified; actuator inactive."]},
            "tree_sha256": trial_module.tree_hash(self.instance / "Plugin")})
        self.cli = FakeCli(self.instance, self.lab)
        self.trial = trial_module.Trial(self.instance, self.cli)
        self.request = self.trial.request(self.fixture["fixture_id"], "1" * 32, "333", "444", "End Position",
                                          "Storage/exporter/agent-core-snapshot-v1.json", "Ordinary Truck")

    def preview(self):
        return self.trial.preview(self.request)["plan_sha256"]

    def test_preview_is_stable_exact_and_does_not_queue_or_start(self):
        preview = self.trial.preview(self.request)
        self.assertEqual(preview["plan_sha256"], self.preview())
        self.assertEqual(preview["plan"]["grid"]["entity_id"], "106313676705820376")
        self.assertFalse((self.instance / self.lab.PENDING_FILE).exists())
        self.assertEqual(set(call[0] for call in self.cli.calls), {"status"})

    def test_execute_correlates_one_run_and_retry_returns_same_result(self):
        plan = self.preview()
        result = self.trial.execute(self.request, plan)
        self.assertEqual(result["status"], "arrival-verified")
        self.assertTrue(result["success"])
        count = len(self.cli.calls)
        self.assertEqual(self.trial.execute(self.request, plan), result)
        self.assertEqual(len(self.cli.calls), count)
        self.assertFalse(self.cli.active)

    def test_status_reads_journal_without_starting_or_claiming_live_state(self):
        count = len(self.cli.calls)
        missing = self.trial.status(self.fixture["fixture_id"], self.request["request_id"])
        self.assertEqual(missing["status"], "not-found")
        self.assertFalse((self.instance / ".vragecage-trials").exists())
        self.assertEqual(len(self.cli.calls), count)
        result = self.trial.execute(self.request, self.preview())
        count = len(self.cli.calls)
        status = self.trial.status(self.fixture["fixture_id"], self.request["request_id"])
        self.assertEqual(status["result"], result)
        self.assertEqual(status["phase"], "terminal")
        self.assertIn("not a current service", status["evidence"])
        self.assertEqual(len(self.cli.calls), count)

    def test_reprepared_fixture_cannot_reuse_an_old_journal_scope(self):
        plan = self.preview()
        self.trial.execute(self.request, plan)
        changed = self.lab.load_object(self.instance / self.lab.LAB_FILE)
        changed["fixture_id"] = "9" * 32
        self.lab.atomic_write(self.instance / self.lab.LAB_FILE, changed)
        with self.assertRaisesRegex(ValueError, "fixture"):
            self.trial.execute(self.request, plan)
        with self.assertRaisesRegex(ValueError, "fixture"):
            self.trial.status(self.request["fixture_id"], self.request["request_id"])

    def test_stale_plan_and_changed_plugin_never_queue(self):
        plan = self.preview()
        with (self.instance / "World/Sandbox.sbc").open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.trial.execute(self.request, plan)
        (self.instance / "Plugin/Plugin.cs").write_text("changed")
        with self.assertRaisesRegex(ValueError, "contents changed"):
            self.preview()
        self.assertNotIn("lab-drive-zone", [call[0] for call in self.cli.calls])

    def test_active_pending_wrong_fixture_and_wrong_camera_fail_before_mutation(self):
        self.cli.active = True
        with self.assertRaisesRegex(ValueError, "inactive"):
            self.preview()
        self.cli.active = False
        self.lab.atomic_write(self.instance / self.lab.PENDING_FILE, {})
        with self.assertRaisesRegex(ValueError, "pending"):
            self.preview()
        (self.instance / self.lab.PENDING_FILE).unlink()
        for changed in [{**self.request, "fixture_id": "0" * 32}, {**self.request, "depth_camera_id": "445"}]:
            with self.assertRaises(ValueError):
                self.trial.preview(changed)

    def test_concurrent_trial_and_staging_locks_refuse_before_queueing(self):
        plan = self.preview()
        with (self.instance / ".vragecage-trial.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "Another high-level trial"):
                self.trial.execute(self.request, plan)
        with (self.instance / ".vragecage-stage.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "staging is in progress"):
                self.preview()
        self.assertFalse((self.instance / self.lab.PENDING_FILE).exists())
        self.assertFalse((self.instance / ".vragecage-trials").exists())

    def test_plugin_without_destructive_lab_authority_marker_is_rejected(self):
        stage_path = self.instance / ".vragecage-plugin-stage.json"
        stage = self.lab.load_object(stage_path)
        stage["contract"]["expected_log_markers"] = []
        self.lab.atomic_write(stage_path, stage)
        with self.assertRaisesRegex(ValueError, "destructive-lab-authorized"):
            self.preview()
        self.assertNotIn("lab-drive-zone", [call[0] for call in self.cli.calls])

    def test_terminal_failure_is_cleanly_stopped_without_arrival(self):
        self.cli.failure = "stalled"
        result = self.trial.execute(self.request, self.preview())
        self.assertEqual(result["status"], "stopped-without-arrival")
        self.assertFalse(result["arrival_verified"])
        self.assertTrue(result["clean_shutdown"])

    def test_wrong_receipt_operation_or_package_is_never_arrival(self):
        for failure in ("wrong-operation", "wrong-package"):
            with self.subTest(failure=failure):
                self.cli.failure = failure
                self.request["request_id"] = ("3" if failure == "wrong-operation" else "4") * 32
                result = self.trial.execute(self.request, self.preview())
                self.assertFalse(result["success"])
                self.assertFalse(result["arrival_verified"])
                self.assertFalse(result["receipt_correlated"])
                self.cli.active = False  # End this independent fake-service case.

    def test_legacy_human_terminal_refuses_automatic_cleanup(self):
        self.cli.failure = "human"
        result = self.trial.execute(self.request, self.preview())
        self.assertEqual(result["status"], "review-required")
        self.assertFalse(result["success"])
        self.assertTrue(self.cli.active)
        self.assertNotIn("stop", [call[0] for call in self.cli.calls])

    def test_stale_or_pre_activation_presence_snapshot_skips_cleanup_but_keeps_arrival(self):
        for failure in ("stale", "preactivation"):
            with self.subTest(failure=failure):
                self.cli.failure = failure
                self.request["request_id"] = {"stale": "6", "preactivation": "7"}[failure] * 32
                result = self.trial.execute(self.request, self.preview())
                self.assertEqual(result["status"], "arrival-verified-running")
                self.assertTrue(result["success"])
                self.assertTrue(result["arrival_verified"])
                self.assertFalse(result["clean_shutdown"])
                self.assertTrue(self.cli.active)
                self.assertNotIn("stop", [call[0] for call in self.cli.calls])
                self.cli.active = False  # Only fake state; no server exists.

    def test_connected_operator_can_observe_arrival_without_automatic_shutdown(self):
        self.cli.failure = "human-observer"
        result = self.trial.execute(self.request, self.preview())
        self.assertEqual(result["status"], "arrival-verified-running")
        self.assertTrue(result["success"])
        self.assertTrue(result["arrival_verified"])
        self.assertFalse(result["clean_shutdown"])
        self.assertTrue(result["run_may_be_active"])
        self.assertIn("zero-human evidence", result["shutdown_issue"])
        self.assertTrue(self.cli.active)
        self.assertNotIn("stop", [call[0] for call in self.cli.calls])
        self.cli.active = False  # Only fake state; no server exists.

    def test_known_human_or_api_loss_overrides_a_lagging_zero_player_snapshot(self):
        for failure in ("human-lag", "api-lag"):
            with self.subTest(failure=failure):
                self.cli.failure = failure
                self.request["request_id"] = ("a" if failure == "human-lag" else "b") * 32
                result = self.trial.execute(self.request, self.preview())
                self.assertEqual(result["status"], "review-required")
                self.assertTrue(self.cli.active)
                self.assertIn("leave shutdown to operator", result["shutdown_issue"])
                self.assertNotIn("stop", [call[0] for call in self.cli.calls])
                self.cli.active = False

    def test_receipt_preserves_presence_terminal_across_log_stream_order_and_tail_bound(self):
        for status in ("human-connected", "players-api-unavailable"):
            with self.subTest(status=status):
                command = self.lab.plan_drive_to(self.instance, 333, "End Position", [10, 20, 30], 0.5, 3.1, 1800, depth_camera_id=444)
                active = self.lab.activate(self.instance)
                event = "Voidwright evidence: type=depth-controller operation=" + command["operation_id"]
                (self.instance / "SpaceEngineersDedicated_guard.log").write_text(event + " status=" + status + "\n")
                logs = self.instance / "MagnetarConfig"
                logs.mkdir(exist_ok=True)
                (logs / "info_guard.log").write_text("\n".join(event + " status=running tick=" + str(index) for index in range(40)))
                # The fixture has no engine delay; filesystem mtimes can lag
                # the activation clock. Bind both synthetic logs after it.
                log_time = datetime.fromisoformat(active["started_at"]).timestamp() + 1
                for log in (self.instance / "SpaceEngineersDedicated_guard.log", logs / "info_guard.log"):
                    os.utime(log, (log_time, log_time))
                script = Path(__file__).resolve().parents[1] / "magnetar-receipt.py"
                output = subprocess.run([sys.executable, str(script), str(self.instance)], capture_output=True, text=True, timeout=10)
                self.assertTrue(output.stdout, output.stderr)
                receipt = json.loads(output.stdout)
                self.assertEqual(receipt["lab_operation_status"], status)
                self.assertTrue(receipt["lab_operation_terminal"])
                self.assertLessEqual(len(receipt["lab_operation_events"]), 32)
                self.assertTrue(any("status=" + status in value for value in receipt["lab_operation_events"]))

    def test_interrupted_wait_still_stops_only_owned_run(self):
        self.cli.failure = "interrupted"
        result = self.trial.execute(self.request, self.preview())
        self.assertFalse(result["success"])
        self.assertTrue(result["clean_shutdown"])
        self.assertFalse(self.cli.active)

    def test_startup_error_after_activation_reconciles_and_stops_owned_run(self):
        self.cli.failure = "start-error"
        result = self.trial.execute(self.request, self.preview())
        self.assertFalse(result["success"])
        self.assertTrue(result["clean_shutdown"])

    def test_replaced_run_is_not_stopped(self):
        self.cli.failure = "foreign-run"
        result = self.trial.execute(self.request, self.preview())
        self.assertEqual(result["status"], "review-required")
        self.assertNotIn("stop", [call[0] for call in self.cli.calls])

    def test_pending_replacement_is_rejected_at_activation_before_launch(self):
        self.cli.failure = "pending-replaced"
        result = self.trial.execute(self.request, self.preview())
        self.assertEqual(result["status"], "review-required")
        self.assertFalse(self.cli.active)
        self.assertFalse((self.instance / self.lab.ACTIVE_FILE).exists())
        self.assertEqual(list((self.instance / "Quarantine").iterdir()), [])
        self.assertTrue(result["pending_command_needs_review"])
        self.assertNotIn("wait", [call[0] for call in self.cli.calls])

    def test_run_replacement_between_check_and_stop_is_refused_at_sink(self):
        self.cli.failure = "replaced-before-stop"
        result = self.trial.execute(self.request, self.preview())
        self.assertEqual(result["status"], "review-required")
        self.assertFalse(result["clean_shutdown"])
        self.assertTrue(self.cli.active)
        self.assertIn("does not match", result["shutdown_issue"])

    def test_interrupted_journal_is_not_automatically_replayed(self):
        plan = self.preview()
        directory = self.instance / ".vragecage-trials"
        directory.mkdir()
        self.lab.atomic_write(directory / (self.request["request_id"] + ".json"), {
            "schema": "vragecage.lab-trial-journal.v1", "request": self.request, "plan_sha256": plan, "phase": "reserved"})
        count = len(self.cli.calls)
        result = self.trial.execute(self.request, plan)
        self.assertEqual(result["status"], "review-required")
        self.assertEqual(result["fixture_id"], self.request["fixture_id"])
        self.assertEqual(result["plan_sha256"], plan)
        self.assertTrue(result["run_may_be_active"])
        self.assertEqual(len(self.cli.calls), count)

    def test_unknown_targets_noncanonical_ids_and_path_escape_are_rejected(self):
        for target, controller, path in [("Observation Towers", "333", "Storage/x/agent-core-snapshot-v1.json"),
            ("End Position", "0333", "Storage/x/agent-core-snapshot-v1.json"),
            ("End Position", "333", "../agent-core-snapshot-v1.json")]:
            with self.assertRaises(ValueError):
                self.trial.request(self.fixture["fixture_id"], "1" * 32, controller, "444", target, path, "Ordinary Truck")

    def test_real_cli_dispatch_and_contracts_with_fake_service_and_receipt(self):
        worker = self.instance.parent.parent
        source = Path(__file__).resolve().parents[1]
        for name in ("lab-trial.py", "lab-contract.py", "fixture-contract.py"):
            shutil.copy2(source / name, worker / name)
        shutil.copy2(source.parent / "bin/vragecage", worker / "vragecage")
        stubs = worker / "stubs"
        stubs.mkdir()
        backend = '''#!/usr/bin/env python3
import json,os,sys
from datetime import datetime,timezone
from pathlib import Path
instance=Path(os.environ['VRAGECAGE_WORKER_ROOT'])/'instances/fixture'
flag=instance/'fake-active'
name=Path(sys.argv[0]).name
with (instance/'fake-invocations').open('a') as stream: stream.write(name+' '+repr(sys.argv)+'\\n')
if name=='systemctl':
    if 'is-active' in sys.argv: sys.exit(0 if flag.exists() else 3)
    if 'show' in sys.argv:
        print(json.dumps({'ActiveState':'active' if flag.exists() else 'inactive'}));sys.exit(0)
    if 'stop' in sys.argv: flag.unlink(missing_ok=True);sys.exit(0)
    raise SystemExit('Unexpected fake systemctl invocation')
if name=='systemd-run':
    flag.touch()
    with (instance/'fake-launches').open('a') as stream: stream.write('one\\n')
    snapshot={'schemaVersion':1,'runId':'2'*32,'capturedAtUtc':datetime.now(timezone.utc).isoformat(),
        'capturedSimulationTick':100,'playerObservation':{'schemaVersion':1,'capturedSimulationTick':100,
        'status':'no_connected_humans','connectedHumanPlayerCount':0,'characterObserved':False}}
    snapshot_path=instance/'World/Storage/exporter/agent-core-snapshot-v1.json'
    snapshot_path.parent.mkdir(parents=True,exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot))
    (instance/'fake-snapshot-created').write_text(str(snapshot_path.exists()))
    print('Synthetic user service started; no engine launched');sys.exit(0)
if name=='stop-magnetar.sh': flag.unlink(missing_ok=True);print('Synthetic stop');sys.exit(0)
if name=='magnetar-receipt.py':
    active=json.loads((instance/'.vragecage-active-run.json').read_text());command=active['command']
    stage=json.loads((instance/'.vragecage-plugin-stage.json').read_text())
    mod=json.loads((instance/'.vragecage-mod-stage.json').read_text())
    print(json.dumps({'schema':'vragecage.magnetar-receipt.v1','lab_run_id':active['run_id'],
        'lab_operation_id':command['operation_id'],'lab_operation_status':'complete','lab_operation_terminal':True,
        'lab_contract_sha256':active['contract_sha256'],'lab_contract_valid':True,'lab_authority':'destructive-lab',
        'passed':True,'clean_shutdown':not flag.exists(),'staged_plugin_tree_sha256':stage['tree_sha256'],
        'staged_plugin_unchanged':True,'staged_mod_tree_sha256':mod['tree_sha256'],
        'staged_mod_unchanged':True,'observations':[],'lab_operation_events':['operation='+command['operation_id']+' status=complete']}));sys.exit(0)
raise SystemExit('Unknown fake service entry')
'''
        for path in [stubs / "systemctl", stubs / "systemd-run", worker / "stop-magnetar.sh", worker / "magnetar-receipt.py"]:
            path.write_text(backend)
            path.chmod(0o700)
        (worker / "run-magnetar.sh").write_text("# Never invoked by the fake service\n")
        environment = {**os.environ, "VRAGECAGE_WORKER_ROOT": str(worker), "PYTHONDONTWRITEBYTECODE": "1",
                       "PATH": str(stubs) + os.pathsep + os.environ["PATH"]}
        args = [sys.executable, str(worker / "vragecage"), "--local", "lab-trial", self.instance.name]
        for name, value in self.request.items():
            if name != "instance":
                args += ["--" + name.replace("_", "-"), value]
        def invoke(extra=()):
            result = subprocess.run([*args, *extra], capture_output=True, text=True, env=environment, timeout=15)
            debug = (self.instance / "fake-snapshot-created").read_text() if (self.instance / "fake-snapshot-created").exists() else "not-created"
            calls = (self.instance / "fake-invocations").read_text() if (self.instance / "fake-invocations").exists() else "none"
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr + "\nfake-snapshot-created=" + debug + "\nfake-invocations=" + calls)
            return json.loads(result.stdout.splitlines()[-1])
        preview = invoke()
        self.assertFalse((self.instance / "fake-launches").exists())
        result = invoke(["--execute", "--expect-plan-sha256", preview["plan_sha256"]])
        self.assertEqual(result["status"], "arrival-verified")
        self.assertTrue(result["receipt_correlated"])
        self.assertTrue(result["clean_shutdown"])
        repeated = invoke(["--execute", "--expect-plan-sha256", preview["plan_sha256"]])
        self.assertEqual(result, repeated)
        self.assertEqual((self.instance / "fake-launches").read_text().splitlines(), ["one"])
        self.assertFalse((self.instance / "fake-active").exists())
        terminal = self.instance / "Quarantine" / result["run_id"] / "terminal-receipt.json"
        self.assertTrue(terminal.is_file())
        status_args = [sys.executable, str(worker / "vragecage"), "--local", "lab-trial-status", self.instance.name,
                       "--fixture-id", self.request["fixture_id"], "--request-id", self.request["request_id"]]
        status_output = subprocess.run(status_args, capture_output=True, text=True, env=environment, timeout=10)
        self.assertEqual(status_output.returncode, 0, status_output.stdout + status_output.stderr)
        self.assertEqual(json.loads(status_output.stdout)["result"], result)
        with (self.instance / ".vragecage-lifecycle.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            blocked = subprocess.run([sys.executable, str(worker / "vragecage"), "--local", "run", self.instance.name],
                                     env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(blocked.returncode, 75)
            self.assertEqual(json.loads(blocked.stdout)["status"], "lifecycle_busy")
        self.lab.activate(self.instance)
        (self.instance / "fake-active").touch()
        wrong_stop = subprocess.run([sys.executable, str(worker / "vragecage"), "--local", "stop", self.instance.name,
                                     "--expected-run-id", result["run_id"]], env=environment, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(wrong_stop.returncode, 0)
        self.assertTrue((self.instance / "fake-active").exists(), "A new run must not be signaled by stale cleanup")
        (self.instance / "fake-active").unlink()


if __name__ == "__main__":
    unittest.main()
