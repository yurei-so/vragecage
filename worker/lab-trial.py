#!/usr/bin/env python3
"""Preview and execute one receipt-correlated, operator-observed lab GPS trial.

No provisioning or authority creation. Existing low-level CLI/contract checks
remain in force. A durable request journal makes interrupted retries reviewable.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

TARGETS = {"Red Zone", "Yellow Zone", "Green Zone", "Blue Zone", "Safe Spawn Zone", "Starting Area", "End Position"}
LIMITS = {"max_speed": 0.5, "arrival_radius": 3.1, "timeout_ticks": 1800, "wall_wait_seconds": 300}
HEX_ID = re.compile(r"[a-f0-9]{32}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_hash(path, maximum=4 * 1024**3):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("Expected a bounded regular fixture file")
        result = hashlib.sha256(); total = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if total > maximum:
                raise ValueError("Fixture file grew beyond its bound")
            result.update(chunk)
        return result.hexdigest()


def read_bytes(path, maximum=16 * 1024 * 1024):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("Expected a bounded regular fixture file")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Fixture file exceeds its read bound")
        return data


def read_object(path, maximum=16 * 1024 * 1024):
    value = json.loads(read_bytes(path, maximum))
    if not isinstance(value, dict):
        raise ValueError("Expected a fixture JSON object")
    return value


def atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sibling(name):
    spec = importlib.util.spec_from_file_location("trial_" + name.replace("-", "_"), Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entity_id(text):
    if not isinstance(text, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", text) or int(text) > 9223372036854775807:
        raise ValueError("Entity IDs must be canonical positive signed-64-bit decimal strings")
    return text


def snapshot_path(instance, relative):
    path = PurePosixPath(relative)
    if (len(relative) > 512 or "\\" in relative or path.is_absolute() or ".." in path.parts
            or path.as_posix() != relative or path.parts[0:1] != ("Storage",)
            or path.name != "agent-core-snapshot-v1.json"):
        raise ValueError("Snapshot must be one exact Storage/.../agent-core-snapshot-v1.json path in this copied World")
    result = instance / "World"
    for part in path.parts:
        result /= part
        if result.is_symlink():
            raise ValueError("Snapshot path must not traverse symbolic links")
    return result


def tree_hash(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Staged package must be a non-symlink directory")
    result = hashlib.sha256()
    count = total = 0
    files = []
    entries = 0
    for path in root.rglob("*"):
        entries += 1
        if entries > 200_000:
            raise ValueError("Staged package has too many filesystem entries")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Staged package contains a link or special entry")
        count += 1
        total += path.stat().st_size
        if count > 100_000 or total > 4 * 1024**3:
            raise ValueError("Staged package exceeds the existing upload bounds")
        files.append(path)
    read_total = 0
    for path in sorted(files):
        name = path.relative_to(root).as_posix().encode()
        result.update(len(name).to_bytes(4, "big")); result.update(name)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Staged package file changed type")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                read_total += len(chunk)
                if read_total > 4 * 1024**3:
                    raise ValueError("Staged package grew beyond its byte bound")
                result.update(chunk)
    return result.hexdigest()


class Cli:
    def __init__(self, path):
        self.path = str(path)

    def call(self, args, timeout=30):
        # File-backed output avoids unbounded in-memory communicate() buffers.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([sys.executable, self.path, "--local", *args], stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=errors, start_new_session=True)
            try:
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Bounded VRageCage command timed out")
                    if os.fstat(output.fileno()).st_size > 2 * 1024 * 1024 or os.fstat(errors.fileno()).st_size > 256 * 1024:
                        raise ValueError("VRageCage command output exceeded its bound")
                    time.sleep(0.05)
                output.seek(0); errors.seek(0)
                raw = output.read(2 * 1024 * 1024 + 1)
                error = errors.read(256 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024 or len(error) > 256 * 1024:
                    raise ValueError("VRageCage command output exceeded its bound")
                value = None
                for line in reversed(raw.decode("utf-8").splitlines()):
                    try:
                        candidate = json.loads(line)
                        if isinstance(candidate, dict):
                            value = candidate; break
                    except json.JSONDecodeError:
                        pass
                if value is None:
                    detail = error.decode("utf-8", errors="replace").strip()
                    raise ValueError("VRageCage returned no terminal JSON object"
                                     + (": " + detail[-4000:] if detail else ""))
                return process.returncode, value
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()


class Trial:
    def __init__(self, instance, cli):
        if instance.is_symlink():
            raise ValueError("Lab instance must not be a symbolic link")
        self.instance = instance.resolve()
        self.cli = cli
        self.lab = sibling("lab-contract")
        self.fixture = sibling("fixture-contract")

    def request(self, fixture_id, request_id, controller_id, camera_id, target, snapshot_relative, grid_name):
        if not HEX_ID.fullmatch(fixture_id) or not HEX_ID.fullmatch(request_id):
            raise ValueError("Fixture and request IDs must be exact lowercase 32-hex identities")
        if target not in TARGETS:
            raise ValueError("Target must be an exact supported lab GPS label; Observation Towers is observation-only")
        if not isinstance(grid_name, str) or not 1 <= len(grid_name) <= 128 or any(ord(value) < 32 for value in grid_name):
            raise ValueError("Grid name must be one exact bounded fixture label")
        snapshot_path(self.instance, snapshot_relative)
        return {"instance": self.instance.name, "fixture_id": fixture_id, "request_id": request_id,
                "controller_id": entity_id(controller_id), "depth_camera_id": entity_id(camera_id),
                "target": target, "snapshot_relative_path": snapshot_relative, "grid_name": grid_name}

    @contextmanager
    def stage_guard(self):
        descriptor = os.open(self.instance / ".vragecage-stage.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+b") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Stage lock must be a regular file")
            try:
                fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Fixture staging is in progress; preview again after it finishes") from None
            yield

    def state(self):
        code, status = self.cli.call(["status", self.instance.name])
        if code != 0 or status.get("schema") != "vragecage.status.v1" or status.get("instance") != self.instance.name:
            raise ValueError("Cannot confirm the exact instance's service state")
        service = status.get("service")
        if not isinstance(service, dict):
            raise ValueError("Service state is unavailable")
        return service.get("ActiveState")

    def inputs(self):
        paths = [".vragecage-lab.json", ".vragecage-world-import.json", "SpaceEngineers-Dedicated.cfg",
                 ".vragecage-engine", "World/Sandbox.sbc", "World/SANDBOX_0_0_0_.sbs"]
        for optional in [".vragecage-fixture-edits.json", ".vragecage-mod-stage.json", ".vragecage-plugin-stage.json"]:
            if os.path.lexists(self.instance / optional):
                paths.append(optional)
        for optional in ["World/Sandbox_config.sbc", "World/SANDBOX_0_0_0_.sbsB5",
                         "MagnetarConfig/Sources/sources.xml", "MagnetarConfig/Profiles/Current.xml"]:
            if os.path.lexists(self.instance / optional):
                paths.append(optional)
        hashes = {name: file_hash(self.instance / name) for name in paths}
        packages = {}
        for kind in ["plugin", "mod"]:
            receipt_path = self.instance / f".vragecage-{kind}-stage.json"
            if not receipt_path.exists():
                if kind == "plugin":
                    raise ValueError("A staged destructive-lab plugin is required")
                continue
            stage = read_object(receipt_path, 128 * 1024)
            package = Path(str(stage.get("path", "")))
            if not package.is_absolute() or not package.resolve().is_relative_to(self.instance.parent.parent):
                raise ValueError("Staged package path is outside this worker")
            current = tree_hash(package)
            if stage.get("instance") != self.instance.name or current != stage.get("tree_sha256"):
                raise ValueError("Staged package identity or contents changed")
            if kind == "plugin":
                contract = stage.get("contract", {})
                markers = contract.get("expected_log_markers") if isinstance(contract, dict) else None
                if (stage.get("schema") != "vragecage.plugin-stage.v1" or not isinstance(contract, dict) or contract.get("schema") != "vragecage.plugin-contract.v1"
                        or not isinstance(markers, list) or "Voidwright server plugin: destructive-lab authority verified; actuator inactive." not in markers):
                    raise ValueError("High-level trials require the reviewed destructive-lab-authorized plugin build")
            packages[kind] = current
        return {"file_sha256": hashes, "package_tree_sha256": packages}

    def preview(self, request):
        with self.stage_guard():
            return self._preview(request)

    def _preview(self, request):
        contract, imported = self.lab.validate(self.instance)
        if contract["fixture_id"] != request["fixture_id"]:
            raise ValueError("Fixture identity differs from the requested disposable copy")
        if self.state() != "inactive":
            raise ValueError("Lab instance is not confirmed inactive; no queue or interruption is allowed")
        if os.path.lexists(self.instance / self.lab.PENDING_FILE):
            raise ValueError("A pending command needs review before another trial can be planned")
        inputs = self.inputs()
        inspected = self.fixture.inspection(self.instance, contract, imported)
        matches = [grid for grid in inspected["grids"] if grid.get("static") is False and grid.get("name") == request["grid_name"]
                   and any(str(block["entity_id"]) == request["controller_id"] for block in grid["remote_controls"])
                   and any(str(block["entity_id"]) == request["depth_camera_id"] for block in grid["cameras"])]
        if len(matches) != 1:
            raise ValueError("Controller and front camera must share exactly one non-static fixture grid")
        targets = [value for value in inspected["gps_targets"] if value["name"] == request["target"]]
        if len(targets) != 1 or not all(math.isfinite(targets[0][axis]) for axis in ("x", "y", "z")):
            raise ValueError("The exact GPS label must resolve once to finite fixture coordinates")
        if inputs != self.inputs():
            raise ValueError("Fixture changed during planning; preview again")
        plan = {"schema": "vragecage.lab-trial-plan.v1", "request": request, "limits": LIMITS,
                "grid": {"entity_id": str(matches[0]["entity_id"]), "name": matches[0]["name"]},
                "target_position": [targets[0][axis] for axis in ("x", "y", "z")], "inputs": inputs,
                "cleanup_policy": "stop only the owned run with fresh zero-human evidence; otherwise require operator review"}
        return {"schema": "vragecage.lab-trial-preview.v1", "plan_sha256": digest(canonical(plan)), "plan": plan,
                "execution_requested": False}

    def owned_run(self, operation_id, previous_id, expected_command):
        active = read_object(self.instance / self.lab.ACTIVE_FILE)
        run_id = active.get("run_id", "")
        if not HEX_ID.fullmatch(run_id) or run_id == previous_id:
            raise ValueError("No new run identity was confirmed")
        self.lab.verify_runtime(self.instance, run_id)
        command = active.get("command", {})
        command_path = self.instance / "Quarantine" / run_id / "command.json"
        if (command != expected_command or command.get("operation_id") != operation_id or digest(read_bytes(command_path)) != active.get("command_sha256")
                or read_object(command_path) != command):
            raise ValueError("Active run does not own the queued operation")
        return active

    def zero_humans(self, request, active):
        snapshot = read_object(snapshot_path(self.instance, request["snapshot_relative_path"]))
        captured = datetime.fromisoformat(str(snapshot.get("capturedAtUtc", "")).replace("Z", "+00:00"))
        started = datetime.fromisoformat(active["started_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - captured).total_seconds()
        player = snapshot.get("playerObservation", {})
        if (not 0 <= age <= 10 or captured < started or snapshot.get("schemaVersion") != 1
                or not HEX_ID.fullmatch(snapshot.get("runId", "")) or player.get("schemaVersion") != 1
                or player.get("capturedSimulationTick") != snapshot.get("capturedSimulationTick")
                or player.get("connectedHumanPlayerCount") != 0 or player.get("status") != "no_connected_humans"
                or player.get("characterObserved") is not False):
            raise ValueError("Fresh zero-human evidence for this activation is unavailable; do not interrupt the server")

    def execute(self, request, expected_hash):
        if not HASH.fullmatch(expected_hash):
            raise ValueError("Execution requires the exact reviewed plan SHA256")
        contract, _ = self.lab.validate(self.instance)
        if contract["fixture_id"] != request["fixture_id"]:
            raise ValueError("Current fixture identity differs from this trial request")
        lock = os.open(self.instance / ".vragecage-trial.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, "a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another high-level trial owns this instance; inspect its journal before further control") from None
            journal_dir = self.instance / ".vragecage-trials"
            if journal_dir.is_symlink():
                raise ValueError("Trial journal must not be a symbolic link")
            journal_dir.mkdir(mode=0o700, exist_ok=True)
            journal_path = journal_dir / (request["request_id"] + ".json")
            if os.path.lexists(journal_path):
                previous = read_object(journal_path)
                if previous.get("schema") != "vragecage.lab-trial-journal.v1" or previous.get("phase") not in {"reserved", "queued", "started", "terminal"}:
                    raise ValueError("Unsupported or corrupt trial journal; inspect it before further control")
                if previous.get("request") != request or previous.get("plan_sha256") != expected_hash:
                    raise ValueError("Request identity was already used for a different plan")
                if previous.get("phase") == "terminal":
                    result = previous.get("result")
                    if (not isinstance(result, dict) or result.get("schema") != "vragecage.lab-trial-result.v1"
                            or result.get("request_id") != request["request_id"] or result.get("fixture_id") != request["fixture_id"]
                            or result.get("plan_sha256") != expected_hash):
                        raise ValueError("Terminal journal has no matching result; inspect it before further control")
                    return result
                return {"schema": "vragecage.lab-trial-result.v1", "status": "review-required",
                    "request_id": request["request_id"], "fixture_id": request["fixture_id"], "plan_sha256": expected_hash,
                    "operation_id": previous.get("operation_id"), "run_id": previous.get("run_id"),
                    "issue": "An interrupted request journal exists; no automatic replay", "success": False,
                    "arrival_verified": False, "clean_shutdown": False, "receipt_correlated": False,
                    "run_may_be_active": True, "pending_command_needs_review": os.path.lexists(self.instance / self.lab.PENDING_FILE)}
            preview = self.preview(request)
            if preview["plan_sha256"] != expected_hash:
                raise ValueError("Reviewed plan is stale or differs from the current fixture; no command was queued")
            journal = {"schema": "vragecage.lab-trial-journal.v1", "request": request, "plan_sha256": expected_hash, "phase": "reserved"}
            with self.stage_guard():
                # Recheck under the shared staging lock before the reservation.
                if self._preview(request)["plan_sha256"] != expected_hash:
                    raise ValueError("Reviewed plan changed before reservation")
                atomic_json(journal_path, journal)
                return self.perform(request, preview, journal, journal_path)

    def status(self, fixture_id, request_id):
        if not HEX_ID.fullmatch(fixture_id) or not HEX_ID.fullmatch(request_id):
            raise ValueError("Fixture/request IDs must be exact lowercase 32-hex identities")
        contract, _ = self.lab.validate(self.instance)
        if contract["fixture_id"] != fixture_id:
            raise ValueError("Current fixture differs from the requested journal scope")
        directory = self.instance / ".vragecage-trials"
        if directory.is_symlink():
            raise ValueError("Trial journal must not be a symbolic link")
        path = directory / (request_id + ".json")
        if not os.path.lexists(path):
            return {"schema": "vragecage.lab-trial-status.v1", "status": "not-found", "request_id": request_id,
                    "fixture_id": fixture_id, "request": None, "phase": None, "result": None}
        journal = read_object(path, 2 * 1024 * 1024)
        request = journal.get("request", {})
        if (journal.get("schema") != "vragecage.lab-trial-journal.v1" or not isinstance(request, dict)
                or request.get("fixture_id") != fixture_id or request.get("request_id") != request_id):
            raise ValueError("Trial journal identity differs from its requested scope")
        return {"schema": "vragecage.lab-trial-status.v1", "status": "result-recorded" if journal.get("result") else "in-progress-or-interrupted",
                "request_id": request_id, "fixture_id": fixture_id, "request": request, "phase": journal.get("phase"),
                "plan_sha256": journal.get("plan_sha256"), "operation_id": journal.get("operation_id"),
                "run_id": journal.get("run_id"), "result": journal.get("result"),
                "evidence": "Last durable journal state; not a current service or player-presence observation"}

    def perform(self, request, preview, journal, journal_path):
        operation_id = None
        command = None
        owned = None
        final_receipt = None
        operation_receipt = None
        issue = shutdown_issue = None
        previous_path = self.instance / self.lab.ACTIVE_FILE
        previous_id = read_object(previous_path).get("run_id") if previous_path.exists() else None
        try:
            code, command = self.cli.call(["lab-drive-zone", self.instance.name, request["controller_id"], request["target"],
                "--depth-camera-id", request["depth_camera_id"], "--max-speed", "0.5", "--arrival-radius", "3.1", "--timeout-ticks", "1800"])
            operation_id = command.get("operation_id")
            if (code != 0 or not isinstance(operation_id, str) or not HEX_ID.fullmatch(operation_id)
                    or command.get("fixture_id") != request["fixture_id"] or command.get("action") != "drive-to-coordinate-depth"
                    or str(command.get("controller_id")) != request["controller_id"] or str(command.get("depth_camera_id")) != request["depth_camera_id"]
                    or command.get("zone") != request["target"] or command.get("target") != preview["plan"]["target_position"]
                    or any(command.get(key) != value for key, value in LIMITS.items() if key != "wall_wait_seconds")):
                raise ValueError("Queued command does not match the reviewed trial")
            journal.update(phase="queued", operation_id=operation_id); atomic_json(journal_path, journal)
            if self.inputs() != preview["plan"]["inputs"] or self.state() != "inactive":
                raise ValueError("Fixture or service changed after queueing; command needs review before any launch")
            code, started = self.cli.call(["run", self.instance.name, "--expected-operation-id", operation_id,
                                          "--expected-command-sha256", digest(canonical(command))], 45)
            owned = self.owned_run(operation_id, previous_id, command)
            journal.update(phase="started", run_id=owned["run_id"]); atomic_json(journal_path, journal)
            if code != 0 or started.get("status") != "starting":
                raise ValueError("A new owned run exists but startup was not acknowledged")
            code, waited = self.cli.call(["wait", self.instance.name, "--timeout", "300", "--require-observation", "operation=" + operation_id], 330)
            if code != 0:
                issue = "Trial did not reach a passing terminal operation"
        except (Exception, SystemExit) as error:
            issue = str(error)
        finally:
            # Startup can fail after activation. Reconcile the exact queued op,
            # never infer ownership from a successful service-start message.
            if operation_id:
                try:
                    owned = self.owned_run(operation_id, previous_id, command)
                    _, current = self.cli.call(["receipt", self.instance.name], 15)
                    current = current.get("receipt", current)
                    if (not isinstance(current, dict) or current.get("lab_run_id") != owned["run_id"]
                            or current.get("lab_operation_id") != operation_id):
                        raise ValueError("Cannot confirm current operation evidence before shutdown")
                    operation_receipt = current
                    if current.get("lab_operation_status") in {"human-connected", "players-api-unavailable"}:
                        raise ValueError("This operation observed a human or lost the player API; leave shutdown to operator review")
                    self.zero_humans(request, owned)
                    code, stopped = self.cli.call(["stop", self.instance.name, "--expected-run-id", owned["run_id"]], 90)
                    final_receipt = stopped.get("receipt")
                    if code != 0 or stopped.get("status") != "stopped" or self.state() != "inactive":
                        raise ValueError("Owned run did not confirm an inactive clean terminal state")
                    self.owned_run(operation_id, previous_id, command)
                except (Exception, SystemExit) as error:
                    shutdown_issue = str(error)
                    # If ownership changed during cleanup, the previously read
                    # operation receipt can no longer certify the current run.
                    try:
                        self.owned_run(operation_id, previous_id, command)
                    except (Exception, SystemExit):
                        operation_receipt = None
        receipt = final_receipt if isinstance(final_receipt, dict) else operation_receipt
        correlated = bool(isinstance(receipt, dict) and owned
            and receipt.get("lab_run_id") == owned["run_id"] and receipt.get("lab_operation_id") == operation_id
            and receipt.get("lab_contract_sha256") == owned["contract_sha256"]
            and receipt.get("lab_contract_valid") is True and receipt.get("lab_authority") == "destructive-lab")
        if correlated:
            correlated = all(receipt.get(f"staged_{kind}_tree_sha256") == value
                and receipt.get(f"staged_{kind}_unchanged") is True
                for kind, value in preview["plan"]["inputs"]["package_tree_sha256"].items())
        if not correlated:
            issue = issue or "No terminal receipt matched the reviewed operation, run, contract and package hashes"
        arrival = bool(correlated and receipt.get("lab_operation_status") == "complete" and receipt.get("lab_operation_terminal") is True)
        clean = bool(correlated and final_receipt is not None and final_receipt.get("clean_shutdown") is True and shutdown_issue is None)
        # Arrival and cleanup are separate outcomes. An occupied observation
        # session can verify the route while remaining online for the operator.
        success = bool(arrival and receipt.get("passed") is True and issue is None)
        result = {"schema": "vragecage.lab-trial-result.v1", "request_id": request["request_id"], "fixture_id": request["fixture_id"],
            "plan_sha256": preview["plan_sha256"], "operation_id": operation_id, "run_id": owned["run_id"] if owned else None,
            "status": "arrival-verified" if success and clean else "arrival-verified-running" if success else "completed-with-issues" if clean and arrival else "stopped-without-arrival" if clean else "review-required",
            "success": success, "arrival_verified": arrival, "clean_shutdown": clean, "issue": issue,
            "shutdown_issue": shutdown_issue, "receipt_correlated": correlated, "receipt": receipt,
            "run_may_be_active": not clean and owned is not None,
            "pending_command_needs_review": os.path.lexists(self.instance / self.lab.PENDING_FILE),
            "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
        journal.update(phase="terminal", result=result); atomic_json(journal_path, journal)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("--cli", required=True, type=Path)
    for name in ["fixture-id", "request-id"]:
        parser.add_argument("--" + name, required=True)
    for name in ["controller-id", "depth-camera-id", "target", "snapshot-relative-path", "grid-name"]:
        parser.add_argument("--" + name)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expect-plan-sha256")
    args = parser.parse_args()
    if bool(args.expect_plan_sha256) != args.execute:
        parser.error("--execute and --expect-plan-sha256 must be supplied together")
    if args.status and args.execute:
        parser.error("Status never executes a trial")
    if not args.status and any(getattr(args, name) is None for name in ["controller_id", "depth_camera_id", "target", "snapshot_relative_path", "grid_name"]):
        parser.error("Preview/execute require all exact fixture binding fields")
    def interrupted(signum, frame):
        raise InterruptedError("Trial interrupted; inspect its journal and exact run before retrying")
    signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGINT, interrupted)
    try:
        trial = Trial(args.instance, Cli(args.cli))
        if args.status:
            result = trial.status(args.fixture_id, args.request_id)
        else:
            request = trial.request(args.fixture_id, args.request_id, args.controller_id, args.depth_camera_id, args.target, args.snapshot_relative_path, args.grid_name)
            result = trial.execute(request, args.expect_plan_sha256) if args.execute else trial.preview(request)
        print(canonical(result).decode(), end="")
        return 0 if not args.execute or result.get("success") is True else 1
    except (Exception, SystemExit) as error:
        print(canonical({"schema": "vragecage.lab-trial-error.v1", "error": str(error), "success": False}).decode(), end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
