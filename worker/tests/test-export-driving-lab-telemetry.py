#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import os
import runpy
import tempfile
from pathlib import Path


CLI = Path(__file__).resolve().parents[2] / "bin" / "vragecage"
run_id = "a" * 32
session_id = "b" * 32
timestamp = "2026-01-01T00:00:00.0000000Z"
record = {
    "schemaVersion": 1,
    "runId": run_id,
    "sessionId": session_id,
    "streamId": "grid-202",
    "source": "server-passive-grid",
    "sequence": 1,
    "capturedAtUtc": timestamp,
    "clockDomain": "server-simulation",
    "simulationTick": 10,
    "simulationTimeSeconds": 1.0,
    "gridEntityId": 202,
    "droppedRecordsTotal": 0,
    "humanInput": {"status": "unavailable", "reason": "human_intent_unavailable", "value": None},
    "actuators": {"status": "unavailable", "reason": "wheel_state_unavailable", "value": None},
}
jsonl = (json.dumps(record, separators=(",", ":")) + "\n").encode()
summary = {
    "schemaVersion": 1,
    "source": "server-passive-grid",
    "permissionScope": "read_only_observation",
    "runId": run_id,
    "sessionId": session_id,
    "gridEntityId": 202,
    "playerIdentityId": 12345,
    "recordCount": 1,
    "droppedRecordsTotal": 0,
    "bufferedBytes": len(jsonl),
    "maximumSampleRateHz": 10,
    "firstCapturedAtUtc": timestamp,
    "lastCapturedAtUtc": timestamp,
    "freshness": "test fixture",
}
summary_bytes = json.dumps(summary, separators=(",", ":")).encode()

with tempfile.TemporaryDirectory(prefix="vragecage-passive-telemetry-") as temporary:
    root = Path(temporary) / "worker"
    instance_name = "disposable-a"
    instance = root / "instances" / instance_name
    storage = instance / "Saves" / "World" / "Storage" / "VRage.Game"
    storage.mkdir(parents=True)
    (instance / ".vragecage-engine").write_text("magnetar\n")
    (instance / ".vragecage-lab.json").write_text(json.dumps({"authority": "destructive-lab", "instance": instance_name}))
    jsonl_path = storage / f"driving-lab-passive-{run_id}.jsonl"
    summary_path = storage / f"driving-lab-passive-{run_id}.summary.json"
    jsonl_path.write_bytes(jsonl)
    summary_path.write_bytes(summary_bytes)
    before = {path: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
              for path in (jsonl_path, summary_path)}

    os.environ["VRAGECAGE_WORKER_ROOT"] = str(root)
    api = runpy.run_path(str(CLI))
    retrieved_jsonl, retrieved_summary, metadata = api["collect_passive_telemetry"](instance_name, run_id)
    assert retrieved_jsonl == jsonl and retrieved_summary == summary_bytes
    assert metadata["record_count"] == 1 and metadata["grid_entity_id"] == 202
    assert "player_identity_id" not in metadata
    assert {path: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in (jsonl_path, summary_path)} == before, "retrieval changed worker-side artifacts"

    envelope = io.BytesIO()
    api["write_passive_telemetry_envelope"](envelope, run_id, jsonl, summary_bytes)
    envelope.seek(0)
    assert api["read_passive_telemetry_envelope"](envelope, run_id) == (jsonl, summary_bytes)

    output_dir = Path(temporary) / "local-output"
    output_paths = api["store_passive_telemetry"](output_dir, run_id, jsonl, summary_bytes)
    assert [Path(path).read_bytes() for path in output_paths] == [jsonl, summary_bytes]
    assert all(Path(path).stat().st_mode & 0o077 == 0 for path in output_paths)

    try:
        api["collect_passive_telemetry"](instance_name, "c" * 32)
        raise AssertionError("retrieval accepted another run ID")
    except SystemExit:
        pass

    outside = Path(temporary) / "outside.jsonl"
    outside.write_bytes(jsonl)
    jsonl_path.unlink()
    jsonl_path.symlink_to(outside)
    try:
        api["collect_passive_telemetry"](instance_name, run_id)
        raise AssertionError("retrieval followed a symlink outside the exact instance")
    except SystemExit:
        pass

    observer_name = "passive-observer-a"
    observer = root / "instances" / observer_name
    observer_storage = observer / "Saves" / "World" / "Storage" / "VRage.Game"
    observer_storage.mkdir(parents=True)
    (observer / ".vragecage-engine").write_text("magnetar\n")
    package = observer / "Plugins" / "voidwright-passive-telemetry"
    package.mkdir(parents=True)
    (package / "plugin.dll").write_bytes(b"fixture")
    package_hash = api["_passive_plugin_tree_hash"](package)
    stage = {"instance": observer_name, "plugin_id": "voidwright-passive-telemetry",
             "tree_sha256": package_hash}
    (observer / ".vragecage-passive-telemetry-stage.json").write_text(json.dumps(stage))
    observer_contract = {
        "schema": "vragecage.passive-telemetry-authority.v1",
        "authority": "read_only_observation",
        "instance": observer_name,
        "engine": "magnetar",
        "plugin_id": "voidwright-passive-telemetry",
        "plugin_tree_sha256": package_hash,
        "capabilities": ["player-consented-passive-grid-telemetry"],
        "denied_capabilities": ["movement", "actuator-control",
                                "programmable-block-script-control", "world-entity-mutation"],
    }
    (observer / ".vragecage-passive-telemetry.json").write_text(json.dumps(observer_contract))
    (observer_storage / f"driving-lab-passive-{run_id}.jsonl").write_bytes(jsonl)
    (observer_storage / f"driving-lab-passive-{run_id}.summary.json").write_bytes(summary_bytes)
    _, _, observer_metadata = api["collect_passive_telemetry"](observer_name, run_id)
    assert observer_metadata["record_count"] == 1

print("PASS retrieve only a completed, run-bound telemetry pair from the exact disposable instance")
print("PASS validate JSONL identity, bounds, read-only labels, and transfer hashes")
print("PASS keep worker artifacts unchanged and reject symlink/path escapes")
