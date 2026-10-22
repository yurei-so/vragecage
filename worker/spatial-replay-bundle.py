#!/usr/bin/env python3
"""Build a portable, provenance-preserving replay bundle from safe-zone A/B evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "vragecage.spatial-replay.v1"
TRUCK_ID = "105034224363529183"
SAFE_ZONE_ID = "73417504878157738"
SOURCE_ROOT = Path("/home/alu52/github/voidwright/artifacts/local-vehicle-steering-test-20261021")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_evidence(source_root: Path) -> tuple[dict, dict, dict, dict]:
    preflight = json.loads((source_root / "safezone-ab-preflight-v2.json").read_text())
    attempt = json.loads((source_root / "safezone-ab-sequential-startup-attempt.json").read_text())
    enabled_audit = json.loads((source_root / "safezone-ab-complete-enabled-v2-source" / "placement-audit.json").read_text())
    disabled_audit = json.loads((source_root / "safezone-ab-complete-disabled-v2-source" / "placement-audit.json").read_text())
    if preflight.get("schema") != "voidwright.safezone-ab-preflight.v2":
        raise ValueError("unsupported safe-zone preflight evidence schema")
    if attempt.get("schema") != "voidwright.safezone-ab-sequential-startup-attempt.v1":
        raise ValueError("unsupported safe-zone attempt evidence schema")
    if enabled_audit.get("blue_truck_grid_id") != TRUCK_ID or disabled_audit.get("blue_truck_grid_id") != TRUCK_ID:
        raise ValueError("source placement evidence does not identify the expected Blue Truck grid")
    return preflight, attempt, enabled_audit, disabled_audit


def _sample(run_id: str, condition: str, kind: str, position: list[float], *, seq: int,
            source_ref: str, sim_tick: int | None = None, sim_time_s: float | None = None,
            wall_time_utc: str | None = None, linear_speed_mps: float | None = None,
            angular_speed_radps: float | None = None) -> dict:
    return {
        "schema": "vragecage.spatial-sample.v1",
        "run_id": run_id,
        "world_id": None,
        "session_name": None,
        "fixture_id": None,
        "condition": condition,
        "sequence": seq,
        "sample_kind": kind,
        "entity_id": TRUCK_ID,
        "controller_entity_id": None,
        "entity_type": "grid",
        "position_m": position,
        "orientation_xyzw": None,
        "linear_velocity_mps": None,
        "linear_speed_mps": linear_speed_mps,
        "angular_velocity_radps": None,
        "angular_speed_radps": angular_speed_radps,
        "simulation_tick": sim_tick,
        "simulation_time_s": sim_time_s,
        "wall_time_utc": wall_time_utc,
        "actuators": None,
        "command_id": None,
        "uncertainty": "source evidence does not provide a per-sample value" if any(
            v is None for v in (sim_tick, sim_time_s, wall_time_utc)
        ) else None,
        "source_ref": source_ref if isinstance(source_ref, dict) else {"path": source_ref, "line": None},
        "source_wall_time": None,
        "actuator_states": None,
        "source_timezone": None,
    }


def _event(run_id: str | None, condition: str, category: str, severity: str,
           event_type: str, description: str, source_ref: str, *, entity_id: str | None = None,
           details: dict | None = None, sim_tick: int | None = None,
           sim_time_s: float | None = None, wall_time_utc: str | None = None) -> dict:
    return {
        "schema": "vragecage.replay-event.v1",
        "event_id": f"{condition}-{event_type.lower().replace('_', '-')}-{len(EVENTS):04d}",
        "run_id": run_id,
        "condition": condition,
        "category": category,
        "severity": severity,
        "event_type": event_type,
        "simulation_tick": sim_tick,
        "simulation_time_s": sim_time_s,
        "wall_time_utc": wall_time_utc,
        "source_wall_time": None,
        "entity_id": entity_id,
        "command_id": None,
        "description": description,
        "details": details or {},
        "source_ref": source_ref if isinstance(source_ref, dict) else {"path": source_ref, "line": None},
    }


EVENTS: list[dict] = []


IDLE_RE = re.compile(
    r"^(?P<wall>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) - Thread:\s+\d+ ->\s+"
    r"Voidwright local test idle: grid=(?P<grid>\d+) controller=(?P<controller>\d+) "
    r"position=(?P<position>-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?) "
    r"linearSpeed=(?P<linear>-?\d+(?:\.\d+)?) angularSpeed=(?P<angular>-?\d+(?:\.\d+)?)"
)


def parse_idle_log(path: Path, run_id: str, condition: str, evidence_ref: str) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = IDLE_RE.match(line)
        if not match:
            continue
        if match["grid"] != TRUCK_ID:
            continue
        position = [float(v) for v in match["position"].split(",")]
        wheel_text = None
        if "wheelReadbacks=" in line:
            wheel_text = line.split("wheelReadbacks=", 1)[1].split(" writerAudit=", 1)[0]
        wheels = []
        if wheel_text:
            for channel in wheel_text.split(";"):
                if ":" not in channel:
                    continue
                block_id, values = channel.split(":", 1)
                props = {}
                for token in values.split(","):
                    if "=" in token:
                        key, value = token.split("=", 1)
                        props[key] = value
                wheels.append({"block_id": block_id, "observed_properties": props})
        records.append({
            "schema": "vragecage.spatial-sample.v1", "run_id": run_id,
            "world_id": None, "session_name": None, "fixture_id": None, "condition": condition,
            "sequence": len(records), "sample_kind": "runtime_idle_log_sample",
            "entity_id": TRUCK_ID, "controller_entity_id": match["controller"], "entity_type": "grid", "position_m": position,
            "orientation_xyzw": None, "linear_velocity_mps": None,
            "linear_speed_mps": float(match["linear"]),
            "angular_velocity_radps": None, "angular_speed_radps": float(match["angular"]),
            "simulation_tick": None, "simulation_time_s": None, "wall_time_utc": None,
            "source_wall_time": match["wall"], "source_timezone": "unknown; dedicated log timestamp is unqualified",
            "actuators": None, "actuator_states": wheels, "command_id": None,
            "uncertainty": "simulation tick, orientation, velocity vectors, and timezone are not present in source line",
            "source_ref": {"path": evidence_ref, "line": line_number},
        })
    return records


def parse_log_events(path: Path, run_id: str, condition: str, evidence_ref: str) -> list[dict]:
    parsed = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number, line in enumerate(lines, 1):
        stamp_match = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)", line)
        stamp = stamp_match.group(1) if stamp_match else None
        event_type = category = severity = None
        description = ""
        if "Voidwright local test authorization accepted:" in line:
            category, severity, event_type = "CONTROL", "info", "LOCAL_TEST_AUTHORIZATION_ACCEPTED"
            description = "Run-bound local test authorization was accepted; this does not mean movement was commanded."
        elif "Voidwright local test baseline:" in line:
            category, severity, event_type = "LIFECYCLE", "info", "PREFLIGHT_BASELINE_RECORDED"
            description = "The plugin recorded its first baseline pose and actuator readbacks."
        elif "Voidwright local test preflight passed:" in line:
            category, severity, event_type = "LIFECYCLE", "info", "PREFLIGHT_PASSED"
            description = "The local test preflight passed for the bound grid and controller."
        elif "Voidwright local test terminal:" in line:
            category, severity, event_type = "DIAGNOSTICS", "warning", "LOCAL_TEST_TERMINAL"
            description = "The local test operation reached a terminal state. The raw receipt line is retained for its exact reason and stop fields."
        elif "OnEntityInitialized " + SAFE_ZONE_ID + " (MySafeZone" in line:
            category, severity, event_type = "LIFECYCLE", "info", "SAFE_ZONE_ENTITY_INITIALIZED"
            description = "The safe-zone entity was initialized by the game during startup."
        if event_type is None:
            continue
        parsed.append({"category": category, "severity": severity, "event_type": event_type,
                       "description": description, "source_wall_time": stamp,
                       "source_ref": {"path": evidence_ref, "line": line_number},
                       "raw_source_line": line.strip()})
    # Make parser coverage visible in the replay. This distinguishes "no matching
    # markers were found" from "the entire log was understood" without dumping
    # unmatched server noise into the event timeline.
    parsed.append({"category": "DIAGNOSTICS", "severity": "info",
                   "event_type": "LOG_PARSE_COVERAGE",
                   "description": "Replay extraction coverage for recognized event markers in this source log.",
                   "source_wall_time": None, "source_ref": {"path": evidence_ref, "line": None},
                   "raw_source_line": None,
                   "details": {"total_lines": len(lines), "recognized_marker_lines": len(parsed),
                               "unclassified_lines": len(lines) - len(parsed),
                               "recognized_event_types": sorted({item["event_type"] for item in parsed})}})
    return parsed


def elapsed_source_seconds(a: str, b: str) -> float | None:
    from datetime import datetime
    try:
        left = datetime.strptime(a, "%Y-%m-%d %H:%M:%S.%f")
        right = datetime.strptime(b, "%Y-%m-%d %H:%M:%S.%f")
        return (right - left).total_seconds()
    except (TypeError, ValueError):
        return None


def path_length(samples: list[dict], max_gap_s: float = 2.0) -> float | None:
    """Sum only adjacent logged samples with a parseable, bounded interval."""
    if len(samples) < 2 or not all(s.get("source_wall_time") for s in samples):
        return None
    total = 0.0
    included = 0
    for left, right in zip(samples, samples[1:]):
        dt = elapsed_source_seconds(left["source_wall_time"], right["source_wall_time"])
        if dt is not None and 0 <= dt <= max_gap_s:
            total += math.dist(left["position_m"], right["position_m"])
            included += 1
    return round(total, 6) if included else None


def runtime_net(samples: list[dict]) -> float | None:
    if len(samples) < 2:
        return None
    return round(math.dist(samples[0]["position_m"], samples[-1]["position_m"]), 6)


def build_bundle(source_root: Path, output: Path, enabled_log: Path | None = None,
                 disabled_log: Path | None = None) -> dict:
    preflight, attempt, enabled_audit, disabled_audit = source_evidence(source_root)
    enabled_run = attempt["enabled_startup_attempts"][-1]
    disabled_run = attempt["disabled_startup_attempt"]["sequential_run"]
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty bundle directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "evidence").mkdir(exist_ok=True)

    source_files = {
        "safezone_ab_preflight": source_root / "safezone-ab-preflight-v2.json",
        "safezone_ab_attempt": source_root / "safezone-ab-sequential-startup-attempt.json",
        "enabled_placement_audit": source_root / "safezone-ab-complete-enabled-v2-source" / "placement-audit.json",
        "disabled_placement_audit": source_root / "safezone-ab-complete-disabled-v2-source" / "placement-audit.json",
        "enabled_world_snapshot": source_root / "safezone-ab-complete-enabled-v2-source" / "SANDBOX_0_0_0_.sbs",
        "disabled_world_snapshot": source_root / "safezone-ab-complete-disabled-v2-source" / "SANDBOX_0_0_0_.sbs",
        "enabled_sandbox_config": source_root / "safezone-ab-complete-enabled-v2-source" / "Sandbox.sbc",
        "disabled_sandbox_config": source_root / "safezone-ab-complete-disabled-v2-source" / "Sandbox.sbc",
    }
    provenance = {}
    copied = {}
    evidence_names = {
        "safezone_ab_preflight": "safezone-ab-preflight-v2.json",
        "safezone_ab_attempt": "safezone-ab-sequential-startup-attempt.json",
        "enabled_placement_audit": "enabled-placement-audit.json",
        "disabled_placement_audit": "disabled-placement-audit.json",
        "enabled_world_snapshot": "enabled-SANDBOX_0_0_0_.sbs",
        "disabled_world_snapshot": "disabled-SANDBOX_0_0_0_.sbs",
        "enabled_sandbox_config": "enabled-Sandbox.sbc",
        "disabled_sandbox_config": "disabled-Sandbox.sbc",
    }
    for key, src in source_files.items():
        if not src.is_file():
            raise FileNotFoundError(f"missing source evidence: {src}")
        rel = Path("evidence") / evidence_names[key]
        dest = output / rel
        shutil.copyfile(src, dest)
        digest = sha256(src)
        if sha256(dest) != digest:
            raise IOError(f"evidence copy hash mismatch: {key}")
        copied[key] = rel.as_posix()
        provenance[key] = {"source_path": str(src), "bundle_path": rel.as_posix(), "sha256": digest, "bytes": src.stat().st_size}

    log_paths = {"enabled_run_log": enabled_log, "disabled_run_log": disabled_log}
    log_refs: dict[str, str] = {}
    for key, src in log_paths.items():
        if src is None:
            continue
        if not src.is_file():
            raise FileNotFoundError(f"missing source run log: {src}")
        rel = Path("evidence") / ("safe-zone-enabled.log" if key.startswith("enabled") else "safe-zone-disabled.log")
        dest = output / rel
        shutil.copyfile(src, dest)
        digest = sha256(src)
        expected_log_hash = (enabled_run if key == "enabled_run_log" else disabled_run).get("game_log_sha256")
        if expected_log_hash and digest != expected_log_hash:
            raise ValueError(f"preserved run log hash differs from run record for {key}")
        if sha256(dest) != digest:
            raise IOError(f"run log evidence copy hash mismatch: {key}")
        log_refs[key] = rel.as_posix()
        remote_origins = {
            "enabled_run_log": "/home/alu52/.local/share/vragecage-worker/instances/local-vehicle-steering-v4-20261021-m/SpaceEngineersDedicated_20261021_181253507.log",
            "disabled_run_log": "/home/alu52/.local/share/vragecage-worker/instances/local-vehicle-steering-v4-20261021-n/SpaceEngineersDedicated_20261021_181428992.log",
        }
        provenance[key] = {"source_path": remote_origins[key], "retrieved_copy_path": str(src), "bundle_path": rel.as_posix(), "sha256": digest, "bytes": src.stat().st_size}

    imported = preflight["vehicle_and_wheel_volume_bounds"]
    source_positions = {item["grid_id"]: item["position_m"] for item in imported}
    imported_truck = source_positions[TRUCK_ID]
    imported_pose = {k: v for k, v in source_positions.items()}
    safe = preflight["safe_zone_test_settings"]
    enabled = enabled_run
    disabled = disabled_run
    samples: list[dict] = []
    EVENTS.clear()

    # Imported pose is a reference, not a runtime physics observation.
    session_root = ET.parse(source_files["enabled_sandbox_config"]).getroot()
    session_name = next((e.text for e in session_root.iter() if e.tag.endswith("SessionName")), None)
    samples.append(_sample("source-world", "shared", "imported_reference", imported_truck,
                           seq=0, source_ref=copied["enabled_placement_audit"]))
    samples[-1]["session_name"] = session_name
    for condition, run in (("safe-zone-enabled", enabled), ("safe-zone-disabled", disabled)):
        rid = run["run_id"]
        observed = run.get("first_live_preflight_position_m", run.get("first_live_position_m"))
        delta = math.dist(imported_truck, observed)
        speeds = run.get("linear_speed_mps", {})
        angular = run.get("angular_speed_radps", {})
        first_linear = speeds.get("max") if isinstance(speeds, dict) else run.get("first_live_linear_speed_mps")
        first_angular = angular.get("max") if isinstance(angular, dict) else run.get("first_live_angular_speed_radps")
        run_log_key = "enabled_run_log" if condition == "safe-zone-enabled" else "disabled_run_log"
        run_log_path = enabled_log if condition == "safe-zone-enabled" else disabled_log
        log_samples = parse_idle_log(run_log_path, rid, condition, log_refs[run_log_key]) if run_log_path else []
        log_events = parse_log_events(run_log_path, rid, condition, log_refs[run_log_key]) if run_log_path else []
        for parsed in log_events:
            event = _event(rid, condition, parsed["category"], parsed["severity"], parsed["event_type"],
                           parsed["description"], parsed["source_ref"]["path"], entity_id=TRUCK_ID,
                           details=parsed.get("details", {"source_line": parsed["source_ref"]["line"],
                                                            "raw_source_line": parsed["raw_source_line"]}))
            event["source_wall_time"] = parsed["source_wall_time"]
            event["source_ref"] = parsed["source_ref"]
            EVENTS.append(event)
        if log_samples:
            for sample in log_samples:
                sample["fixture_id"] = preflight["instances"]["enabled" if condition == "safe-zone-enabled" else "disabled"]["fixture_id"]
                sample["session_name"] = session_name
            samples.extend(log_samples)
            observed = log_samples[0]["position_m"]
            first_linear = log_samples[0]["linear_speed_mps"]
            first_angular = log_samples[0]["angular_speed_radps"]
        else:
            samples.append(_sample(rid, condition, "first_observed_runtime_pose", observed,
                                   seq=1, source_ref=copied["safezone_ab_attempt"],
                                   linear_speed_mps=first_linear, angular_speed_radps=first_angular))
            samples[-1]["fixture_id"] = preflight["instances"]["enabled" if condition == "safe-zone-enabled" else "disabled"]["fixture_id"]
            samples[-1]["session_name"] = session_name
        EVENTS.append(_event(rid, condition, "LIFECYCLE", "info", "FIRST_POSE_OBSERVED",
                             "First available runtime pose; no earlier runtime frames are preserved.",
                             copied["safezone_ab_attempt"], entity_id=TRUCK_ID,
                             details={"position_m": observed, "distance_from_imported_pose_m": round(delta, 6),
                                      "pose_is_continuous_history": False}))
        EVENTS.append(_event(rid, condition, "DIAGNOSTICS", "warning", "POSITION_DISCONTINUITY",
                             "Imported reference and first runtime pose are separated by a large unobserved interval; this is not verified travel.",
                             copied["safezone_ab_attempt"], entity_id=TRUCK_ID,
                             details={"from_position_m": imported_truck, "to_position_m": observed,
                                      "straight_line_offset_m": round(delta, 6), "elapsed_time": None,
                                      "classification": "unobserved_transition; cause unknown"}))
        safe_center = safe["center_m"]
        imported_distance = math.dist(imported_truck, safe_center)
        runtime_distance = math.dist(observed, safe_center)
        for label, distance in (("imported_reference", imported_distance), ("first_runtime_pose", runtime_distance)):
            relation = "inside" if distance <= safe["radius_m"] else "outside"
            EVENTS.append(_event(rid, condition, "WORLD", "info", "SAFE_ZONE_RELATION_DERIVED",
                                 f"{label.replace('_', ' ').title()} is geometrically {relation} the saved spherical safe-zone boundary.",
                                 copied["safezone_ab_preflight"], entity_id=TRUCK_ID,
                                 details={"pose_label": label, "pose_m": imported_truck if label == "imported_reference" else observed,
                                          "center_m": safe_center, "radius_m": safe["radius_m"],
                                          "center_distance_m": round(distance, 6),
                                          "signed_clearance_m": round(safe["radius_m"] - distance, 6),
                                          "relation": relation, "derived_from_saved_geometry": True,
                                          "crossing_observed": False, "continuous_path_available": False}))
        if condition == "safe-zone-enabled":
            EVENTS.append(_event(rid, condition, "MOVEMENT", "info", "STATIONARY_WINDOW_REPORTED",
                                 "Twelve-sample window was stationary after the first observed pose; individual poses and sample times are unavailable.",
                                 copied["safezone_ab_attempt"], entity_id=TRUCK_ID,
                                 details={"reported_sample_count": run.get("live_idle_sample_count"),
                                          "linear_speed_mps": run.get("linear_speed_mps"),
                                          "angular_speed_radps": run.get("angular_speed_radps"),
                                          "window_utc": run.get("live_idle_sample_window_utc")}))
        elif not log_samples:
            last = run.get("last_live_position_m")
            if last:
                samples.append(_sample(rid, condition, "last_observed_runtime_pose", last,
                                       seq=2, source_ref=copied["safezone_ab_attempt"]))
                samples[-1]["fixture_id"] = preflight["instances"]["disabled"]["fixture_id"]
                samples[-1]["session_name"] = session_name
            EVENTS.append(_event(rid, condition, "MOVEMENT", "warning", "STARTUP_MOTION_OBSERVED",
                                 "First sample reported nonzero linear and angular speed before any control command.",
                                 copied["safezone_ab_attempt"], entity_id=TRUCK_ID,
                                 details={"initial_linear_speed_mps": run.get("first_live_linear_speed_mps"),
                                          "initial_angular_speed_radps": run.get("first_live_angular_speed_radps"),
                                          "maximum_displacement_from_imported_pose_m": run.get("max_displacement_from_imported_position_m"),
                                          "per_sample_history_available": False}))
        EVENTS.append(_event(rid, condition, "DIAGNOSTICS", "info", "NO_COMMANDS_ISSUED",
                             "The preserved run records zero propulsion or steering commands.",
                             copied["safezone_ab_attempt"], entity_id=TRUCK_ID,
                             details={"control_commands_issued": run.get("control_commands_issued", 0),
                                      "motion_command_sent": False}))
        if log_samples:
            movement_marked = False
            for sample in log_samples:
                stamp = sample["source_wall_time"]
                if not movement_marked and (sample["linear_speed_mps"] > 0.05 or sample["angular_speed_radps"] > 0.05):
                    EVENTS.append(_event(rid, condition, "MOVEMENT", "warning", "MOVEMENT_DETECTED",
                                         "Logged pose sample reports nonzero vehicle speed before any propulsion command.",
                                         sample["source_ref"]["path"], entity_id=TRUCK_ID,
                                         details={"position_m": sample["position_m"],
                                                  "linear_speed_mps": sample["linear_speed_mps"],
                                                  "angular_speed_radps": sample["angular_speed_radps"],
                                                  "derived_thresholds": {"linear_mps": 0.05, "angular_radps": 0.05},
                                                  "source_line": sample["source_ref"]["line"]},
                                         wall_time_utc=None))
                    EVENTS[-1]["source_wall_time"] = stamp
                    movement_marked = True
            for i in range(1, len(log_samples)):
                before, after = log_samples[i - 1], log_samples[i]
                elapsed = elapsed_source_seconds(before["source_wall_time"], after["source_wall_time"])
                # Preserve the source timestamp string; its timezone is not specified.
                EVENTS.append(_event(rid, condition, "DIAGNOSTICS", "info", "SAMPLE_SEQUENCE",
                                     "Consecutive retained log samples; exact simulation ticks are unavailable.",
                                     after["source_ref"]["path"], entity_id=TRUCK_ID,
                                     details={"from_sequence": before["sequence"], "to_sequence": after["sequence"],
                                              "from_source_wall_time": before["source_wall_time"],
                                              "to_source_wall_time": after["source_wall_time"],
                                              "position_delta_m": round(math.dist(before["position_m"], after["position_m"]), 6),
                                              "elapsed_wall_seconds": elapsed}))
                EVENTS[-1]["source_wall_time"] = after["source_wall_time"]
                if elapsed is None or elapsed > 2.0:
                    EVENTS.append(_event(rid, condition, "DIAGNOSTICS", "warning", "SAMPLE_GAP",
                                         "Adjacent retained samples are separated by an unobserved interval; path integration excludes this interval.",
                                         after["source_ref"]["path"], entity_id=TRUCK_ID,
                                         details={"from_sequence": before["sequence"], "to_sequence": after["sequence"],
                                                  "elapsed_wall_seconds": elapsed, "integrated_path_includes_interval": False}))
                    EVENTS[-1]["source_wall_time"] = after["source_wall_time"]
        EVENTS.append(_event(rid, condition, "LIFECYCLE", "info", "CLEAN_SHUTDOWN",
                             "Disposable server was stopped cleanly and the run was quarantined.",
                             copied["safezone_ab_attempt"], details={"clean_shutdown": run.get("clean_shutdown", True),
                                      "quarantine_receipt": run.get("quarantine_receipt")}))

    # The disabled run preserves distinct first and last runtime poses. Their chord is net
    # displacement only; it is not a reconstructed path length.
    disabled_samples = [s for s in samples if s.get("condition") == "safe-zone-disabled"]
    disabled_first = disabled_samples[0]["position_m"] if disabled_samples else None
    disabled_last = disabled_samples[-1]["position_m"] if disabled_samples else None
    if disabled_first and disabled_last:
        net = math.dist(disabled_first, disabled_last)
        EVENTS.append(_event(disabled["run_id"], "safe-zone-disabled", "MOVEMENT", "info",
                             "NET_DISPLACEMENT_AVAILABLE", "Net displacement between the preserved first and last runtime positions; path length remains unknown.",
                             copied["safezone_ab_attempt"], entity_id=TRUCK_ID,
                             details={"first_position_m": disabled_first, "last_position_m": disabled_last,
                                      "net_displacement_m": round(net, 6), "integrated_path_length_m": None,
                                      "intermediate_sample_poses_available": False}))

    events_path = output / "events.ndjson"
    events_path.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in EVENTS), encoding="utf-8")
    samples_path = output / "samples.ndjson"
    samples_path.write_text("".join(json.dumps(s, separators=(",", ":")) + "\n" for s in samples), encoding="utf-8")

    # World/config details that cannot be derived from the sparse runtime capture remain explicit.
    entity_map = [{"entity_id": gid, "kind": "saved_grid", "position_m": pos,
                   "orientation_xyzw": None, "geometry_status": "center and conservative extent only" if gid == TRUCK_ID else "center only"}
                  for gid, pos in imported_pose.items()]
    entities = {
        "schema": "vragecage.replay-entities.v1",
        "world_id": None,
        "safe_zone": {"entity_id": SAFE_ZONE_ID, "center_m": safe["center_m"],
                      "radius_m": safe["radius_m"], "shape": safe["shape"],
                      "enabled_by_condition": {"safe-zone-enabled": True, "safe-zone-disabled": False},
                      "allowed_actions": safe["allowed_actions"], "grid_access": safe["grid_access"],
                      "source_ref": copied["safezone_ab_preflight"]},
        "grids": entity_map,
        "mechanical_subgrids": [{"parent_grid_id": TRUCK_ID, "child_grid_id": child,
                                 "relationship": "saved fixture wheel subgrid; runtime topology not captured"}
                                for child in enabled_audit["wheel_subgrid_ids"]],
        "grid_extent_bounds_m": {item["grid_id"]: item["extent_bound_m"] for item in enabled_audit["vehicle_zone_intersection_bounds"]},
        "limitations": ["Saved placement evidence has grid centers and conservative extents, not oriented block wireframes.",
                        "Runtime subgrid transforms, ownership, safe-zone changes, and physics-init events were not captured."]
    }
    write_json(output / "entities.json", entities)

    manifest = {
        "schema": SCHEMA,
        "title": "Blue Truck safe-zone startup anomaly",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_run_ids": {"safe-zone-enabled": enabled["run_id"], "safe-zone-disabled": disabled["run_id"]},
        "source_instance_ids": {"safe-zone-enabled": attempt["comparison"]["enabled_instance"],
                                 "safe-zone-disabled": attempt["comparison"]["disabled_instance"]},
        "fixture_id_by_condition": {"safe-zone-enabled": preflight["instances"]["enabled"]["fixture_id"],
                                     "safe-zone-disabled": preflight["instances"]["disabled"]["fixture_id"]},
        "world_identity": {"session_name": session_name, "world_id": None,
                            "source_world": preflight["common_ab_source_world"],
                            "source_world_tree_hashes": preflight["a_b_source_hashes"]},
        "entity_id": TRUCK_ID,
        "safe_zone_id": SAFE_ZONE_ID,
        "coordinate_frame": "Space Engineers world coordinates",
        "units": {"position": "m", "speed": "m/s", "angular_speed": "rad/s"},
        "condition_difference": attempt["comparison"]["source_condition_difference"],
        "outcome": attempt["outcome"],
        "commands_issued": False,
        "server_state": "both disposable services stopped; runs quarantined",
        "source_artifacts": provenance,
        "source_hashes": {"original_source_world": preflight.get("original_source_hashes"),
                          "enabled_ab_source": preflight.get("a_b_source_hashes", {}).get("enabled"),
                          "disabled_ab_source": preflight.get("a_b_source_hashes", {}).get("disabled")},
        "available_data": {"position_samples": len(samples), "runtime_tick_values": 0,
                           "runtime_orientation_values": 0, "runtime_velocity_vectors": 0,
                           "runtime_per_wheel_actuator_samples": sum(1 for s in samples if s.get("actuator_states")), "runtime_command_events": 0,
                           "runtime_source_wall_times": sum(1 for s in samples if s.get("source_wall_time")),
                           "source_wall_timezone_known": False},
        "measurement_summary": {
            "safe-zone-enabled": {"imported_to_first_observed_offset_m": enabled.get("displacement_from_imported_position_m"),
                                   "post_observation_sample_count": enabled.get("live_idle_sample_count"),
                                   "reported_linear_speed_range_mps": enabled.get("linear_speed_mps"),
                                   "reported_angular_speed_range_radps": enabled.get("angular_speed_radps"),
                                   "continuous_path_length_m": path_length([s for s in samples if s.get("condition") == "safe-zone-enabled"]),
                                   "net_runtime_displacement_m": runtime_net([s for s in samples if s.get("condition") == "safe-zone-enabled"])},
            "safe-zone-disabled": {"imported_to_first_observed_offset_m": disabled.get("first_live_displacement_from_imported_position_m"),
                                    "max_offset_from_imported_position_m": disabled.get("max_displacement_from_imported_position_m"),
                                    "first_linear_speed_mps": disabled.get("first_live_linear_speed_mps"),
                                    "first_angular_speed_radps": disabled.get("first_live_angular_speed_radps"),
                                    "continuous_path_length_m": path_length(disabled_samples),
                                    "net_runtime_displacement_m": runtime_net(disabled_samples)}
        },
        "interpretation": "The approximately 463 m imported-to-runtime offset is an unobserved coordinate/physics transition, not a verified traveled path. The two conditions cannot establish a safe-zone propulsion effect.",
        "files": {"samples": "samples.ndjson", "events": "events.ndjson", "entities": "entities.json", "viewer": "index.html", "report": "report.md"}
    }
    write_json(output / "manifest.json", manifest)
    write_viewer(output / "index.html", manifest, samples, EVENTS, entities)
    write_report(output / "report.md", manifest, events_path, samples_path)
    return manifest


def write_viewer(path: Path, manifest: dict, samples: list[dict], events: list[dict], entities: dict) -> None:
    data = json.dumps({"manifest": manifest, "samples": samples, "events": events, "entities": entities}, separators=(",", ":")).replace("</", "<\\/")
    html = VIEWER.replace("__REPLAY_DATA__", data)
    path.write_text(html, encoding="utf-8")


def write_report(path: Path, manifest: dict, events_path: Path, samples_path: Path) -> None:
    enabled = manifest["measurement_summary"]["safe-zone-enabled"]
    disabled = manifest["measurement_summary"]["safe-zone-disabled"]
    fmt = lambda value: "unavailable" if value is None else f"{value:.3f}"
    lines = [
        "# Blue Truck safe-zone startup replay", "",
        f"- Outcome: `{manifest['outcome']}`",
        f"- Target grid: `{manifest['entity_id']}`; safe zone: `{manifest['safe_zone_id']}`",
        f"- Enabled run: `{manifest['source_run_ids']['safe-zone-enabled']}` ({manifest['source_instance_ids']['safe-zone-enabled']})",
        f"- Disabled run: `{manifest['source_run_ids']['safe-zone-disabled']}` ({manifest['source_instance_ids']['safe-zone-disabled']})",
        "- No propulsion or steering commands were issued.",
        "- The two source configurations differ only in the safe-zone enabled flag.", "",
        "## Measurements supported by preserved samples", "",
        f"- Enabled: imported-to-first-runtime offset **{fmt(enabled['imported_to_first_observed_offset_m'])} m**; {enabled['post_observation_sample_count']} samples afterward report zero linear and angular speed; observed-window path length **{fmt(enabled['continuous_path_length_m'])} m**.",
        f"- Disabled: imported-to-first-runtime offset **{fmt(disabled['imported_to_first_observed_offset_m'])} m**; first observed speed **{fmt(disabled['first_linear_speed_mps'])} m/s** and angular speed **{fmt(disabled['first_angular_speed_radps'])} rad/s**; within-window net displacement **{fmt(disabled['net_runtime_displacement_m'])} m** and integrated path length **{fmt(disabled['continuous_path_length_m'])} m** from contiguous approximately one-second samples.",
        "- The roughly 463 m imported-to-first-observation offset is an unobserved transition, not measured travel. No trajectory is drawn through it.",
        "- The disabled run's first-to-last runtime displacement/path describes only the short retained sample window. It does not reconstruct how the grid reached the first observed pose.", "",
        "## Unknown or unavailable", "",
        "- Simulation tick/time, orientation, velocity vectors, exact timezone, runtime subgrid transforms, ownership transitions, safe-zone state transitions, collision events, and missing startup frames are unavailable.",
        "- No safe-zone boundary crossing is observed. Inside/outside markers are derived independently from saved sphere geometry and pose samples.",
        "- This A/B evidence cannot establish whether safe-zone state affected propulsion.", "",
        "## Replay and provenance", "",
        "Open `index.html` for the interactive replay. Dashed offsets mark missing history; they are not paths.",
        f"- Normalized samples: [`{samples_path.name}`]({samples_path.name}) ({manifest['available_data']['position_samples']} rows).",
        f"- Event timeline: [`{events_path.name}`]({events_path.name}).",
        "- Each copied source evidence file is SHA-256 recorded in `manifest.json`."
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


VIEWER = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VRageCage Spatial Replay — Blue Truck</title><style>
:root{color-scheme:dark;--bg:#111820;--panel:#1b2631;--line:#344657;--text:#e8f0f5;--muted:#9aabba;--cyan:#62d5e8;--amber:#ffbe55;--red:#ff6e6e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:14px;align-items:center}h1{font-size:18px;margin:0}small,.muted{color:var(--muted)}main{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:12px;padding:12px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}.views{display:grid;grid-template-columns:1fr 1fr;gap:10px}.view{height:320px;background:#10171e;border:1px solid var(--line);border-radius:5px;position:relative}.view canvas{width:100%;height:100%}.label{position:absolute;top:8px;left:10px;color:var(--muted);font-size:12px}.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px}button,select,input{background:#253544;color:var(--text);border:1px solid var(--line);border-radius:4px;padding:6px}input[type=range]{flex:1;min-width:180px}.events{max-height:370px;overflow:auto}.event{padding:8px;border-bottom:1px solid var(--line);cursor:pointer}.event:hover,.event.selected{background:#263847}.badge{display:inline-block;padding:1px 6px;margin-right:6px;border-radius:12px;background:#384c5e;font-size:11px}.warn{color:var(--amber)}.danger{color:var(--red)}.stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}.stat{background:#121a21;padding:8px;border-radius:4px}.stat b{display:block;font-size:12px;color:var(--muted);font-weight:500}.stat span{font-size:16px}.details{white-space:pre-wrap;overflow-wrap:anywhere;font:12px ui-monospace,monospace;max-height:260px;overflow:auto}.banner{background:#3b2e1c;color:#ffe0a4;padding:9px 12px;margin:0 12px;border-radius:5px}footer{padding:8px 16px;color:var(--muted);font-size:12px}@media(max-width:900px){main{grid-template-columns:1fr}.views{grid-template-columns:1fr}.view{height:260px}}
</style></head><body><header><div><h1>Blue Truck · Safe-zone startup anomaly</h1><small id="runline"></small></div><div><span class="badge">OFFLINE REPLAY</span><span class="badge">NO COMMANDS ISSUED</span></div></header>
<div class="banner">Dashed links mark unobserved offsets, not verified travel paths. Playback steps through indexed samples; the enabled and disabled captures are separate runs, not one shared clock.</div>
<main><section class="panel"><div class="views"><div class="view"><span class="label">TOP DOWN · X/Z · meters</span><canvas id="top"></canvas></div><div class="view"><span class="label">OBLIQUE SCHEMATIC · Y vertical</span><canvas id="oblique"></canvas></div></div>
<div class="controls"><button id="play">Play</button><button id="prev">◀ Frame</button><button id="next">Frame ▶</button><input id="scrub" type="range" min="0" max="0" value="0"><span id="frameLabel"></span><label>Speed <select id="rate"><option>0.5</option><option selected>1</option><option>2</option><option>4</option></select>×</label><label>Zoom <input id="zoom" type="range" min="0.5" max="8" step="0.1" value="1"></label><label>Orbit <input id="orbit" type="range" min="-180" max="180" value="35"></label><button id="focus">Focus selection</button><button id="reset">Reset view</button><label><input type="checkbox" id="showZone" checked> Safe zone</label><label><input type="checkbox" id="showSubgrids" checked> Saved wheel subgrids</label></div>
<div class="stats" id="stats"></div></section><aside class="panel"><h2 style="font-size:15px;margin:0 0 8px">Event timeline</h2><div class="controls"><select id="condition"><option value="all">Both conditions</option><option value="safe-zone-enabled">Safe zone enabled</option><option value="safe-zone-disabled">Safe zone disabled</option></select><select id="category"><option value="all">All categories</option></select><select id="severity"><option value="all">All severities</option><option>info</option><option>warning</option><option>error</option></select><select id="entity"><option value="all">All entities</option></select><button id="prevEvent">Previous event</button><button id="nextEvent">Next event</button><input id="search" placeholder="Search event / command / entity ID"></div><div class="events" id="events"></div><h3 style="font-size:13px">Selected evidence</h3><div class="details" id="details">Select an event marker.</div></aside></main><footer id="foot"></footer>
<script>const D=__REPLAY_DATA__;const S=D.samples,E=D.events,M=D.manifest,G=D.entities;let idx=0,timer=null,selected=null,focusAt=null,panX=0,panZ=0;
document.getElementById('runline').textContent=`enabled run ${M.source_run_ids['safe-zone-enabled']} · disabled run ${M.source_run_ids['safe-zone-disabled']} · fixture ${M.source_instance_ids['safe-zone-enabled']} / ${M.source_instance_ids['safe-zone-disabled']}`;
document.getElementById('foot').textContent=`Replay ${M.schema} · Grid ${M.entity_id} · Safe zone ${M.safe_zone_id} · Units: meters, m/s, rad/s. Missing values remain unknown.`;
const cat=document.getElementById('category');[...new Set(E.map(e=>e.category))].forEach(c=>{let o=document.createElement('option');o.value=c;o.textContent=c;cat.append(o)});const ent=document.getElementById('entity');[...new Set([M.entity_id,G.safe_zone.entity_id,...G.mechanical_subgrids.map(q=>q.child_grid_id)])].forEach(id=>{let o=document.createElement('option');o.value=id;o.textContent=id;ent.append(o)});
function activeSamples(){let c=document.getElementById('condition').value;return S.filter(s=>c==='all'||s.condition===c||s.condition==='shared')}
function current(){let a=activeSamples();return a[Math.min(idx,a.length-1)]}
function setupCanvas(c){let r=c.getBoundingClientRect(),d=window.devicePixelRatio||1;c.width=r.width*d;c.height=r.height*d;let x=c.getContext('2d');x.scale(d,d);return {x,w:r.width,h:r.height}}
function bounds(){let pts=[...S.map(s=>s.position_m),G.safe_zone.center_m];let cx=G.safe_zone.center_m[0],cz=G.safe_zone.center_m[2],rad=G.safe_zone.radius_m;return {minX:Math.min(...pts.map(p=>p[0]),cx-rad),maxX:Math.max(...pts.map(p=>p[0]),cx+rad),minZ:Math.min(...pts.map(p=>p[2]),cz-rad),maxZ:Math.max(...pts.map(p=>p[2]),cz+rad)}}
function draw(){let a=activeSamples();document.getElementById('scrub').max=Math.max(0,a.length-1);idx=Math.min(idx,a.length-1);let s=current();document.getElementById('frameLabel').textContent=`${idx+1}/${a.length} · ${s?.source_wall_time||'source time unknown'} · ${s?.simulation_tick==null?'tick unknown':'tick '+s.simulation_tick} · ${s?.simulation_time_s==null?'sim time unknown':s.simulation_time_s+' s'}`;document.getElementById('scrub').value=idx;drawTop(s);drawOblique(s);renderStats(s);renderEvents();}
function drawTop(cur){let {x,w,h}=setupCanvas(document.getElementById('top')),b=bounds(),pad=28,base=Math.min((w-2*pad)/(b.maxX-b.minX),(h-2*pad)/(b.maxZ-b.minZ)),sc=base*+document.getElementById('zoom').value,fc=focusAt||G.safe_zone.center_m,ox=w/2+panX,oz=h/2+panZ;let X=v=>ox+(v-fc[0])*sc,Z=v=>oz-(v-fc[2])*sc;x.clearRect(0,0,w,h);x.strokeStyle='#263746';x.fillStyle='#9aabba';x.lineWidth=1;for(let i=0;i<6;i++){let xx=pad+i*(w-2*pad)/5,zz=pad+i*(h-2*pad)/5;x.beginPath();x.moveTo(xx,pad);x.lineTo(xx,h-pad);x.stroke();x.beginPath();x.moveTo(pad,zz);x.lineTo(w-pad,zz);x.stroke()}
if(document.getElementById('showZone').checked){let z=G.safe_zone,cx=X(z.center_m[0]),cz=Z(z.center_m[2]),rr=z.radius_m*sc;x.beginPath();x.arc(cx,cz,rr,0,Math.PI*2);x.strokeStyle='#e5b04e';x.setLineDash([7,5]);x.lineWidth=2;x.stroke();x.setLineDash([])}
let imp=S.find(q=>q.sample_kind==='imported_reference');let runtime=S.filter(q=>q.sample_kind!=='imported_reference');if(imp){dot(x,X(imp.position_m[0]),Z(imp.position_m[2]),'#62d5e8',6,'');}
if(document.getElementById('showSubgrids').checked){for(const g of G.grids.filter(q=>q.entity_id!==M.entity_id)){dot(x,X(g.position_m[0]),Z(g.position_m[2]),'#587c92',3,'');}}let extent=G.grid_extent_bounds_m[M.entity_id];if(extent&&imp){x.beginPath();x.arc(X(imp.position_m[0]),Z(imp.position_m[2]),extent*sc,0,Math.PI*2);x.strokeStyle='#62d5e8';x.setLineDash([2,3]);x.stroke();x.setLineDash([])}
for(const condition of ['safe-zone-enabled','safe-zone-disabled']){let track=runtime.filter(q=>q.condition===condition);if(!track.length)continue;let color=condition==='safe-zone-enabled'?'#68d391':'#ff8b72',first=track[0];if(imp){x.beginPath();x.moveTo(X(imp.position_m[0]),Z(imp.position_m[2]));x.lineTo(X(first.position_m[0]),Z(first.position_m[2]));x.strokeStyle='#ffbe55';x.setLineDash([4,5]);x.stroke();x.setLineDash([])}for(let i=1;i<track.length;i++){let a=track[i-1],b=track[i],dt=(a.source_wall_time&&b.source_wall_time)?(Date.parse(b.source_wall_time.replace(' ','T'))-Date.parse(a.source_wall_time.replace(' ','T')))/1000:null;if(dt!=null&&dt<=2){x.beginPath();x.moveTo(X(a.position_m[0]),Z(a.position_m[2]));x.lineTo(X(b.position_m[0]),Z(b.position_m[2]));x.strokeStyle=color;x.lineWidth=2;x.stroke();x.lineWidth=1}}for(const q of track)dot(x,X(q.position_m[0]),Z(q.position_m[2]),color,4,'')}
if(cur)dot(x,X(cur.position_m[0]),Z(cur.position_m[2]),'#fff',8,'');x.fillStyle='#dce6ee';x.fillText('● Imported   ● Enabled   ● Disabled   ○ selected',pad,pad+10);x.fillStyle='#e5b04e';x.fillText(`Safe zone r=${G.safe_zone.radius_m} m`,pad,pad+26);x.fillStyle='#9aabba';x.fillText('Saved wheel-subgrid positions are not runtime transforms.',pad,h-8)}
function dot(x,a,b,color,r,label){x.beginPath();x.arc(a,b,r,0,Math.PI*2);x.fillStyle=color;x.fill();if(label){x.fillStyle='#dce6ee';x.fillText(label,a+8,b-8)}}
function drawOblique(cur){let {x,w,h}=setupCanvas(document.getElementById('oblique')),pts=[...S.map(s=>s.position_m),G.safe_zone.center_m],cx=pts.reduce((a,p)=>a+p[0],0)/pts.length,cy=pts.reduce((a,p)=>a+p[1],0)/pts.length,cz=pts.reduce((a,p)=>a+p[2],0)/pts.length;let scale=Math.min(w,h)/900*+document.getElementById('zoom').value,theta=+document.getElementById('orbit').value*Math.PI/180;function P(p){let dx=p[0]-cx,dz=p[2]-cz,rx=dx*Math.cos(theta)-dz*Math.sin(theta),rz=dx*Math.sin(theta)+dz*Math.cos(theta),dy=p[1]-cy;return [w/2+panX+(rx-rz)*.66*scale,h*.54+panZ+(rx+rz)*.3*scale-dy*.9*scale]};x.clearRect(0,0,w,h);let z=G.safe_zone,center=P(z.center_m),rx=z.radius_m*.66*scale,ry=z.radius_m*.3*scale;x.beginPath();x.ellipse(center[0],center[1],rx,ry,0,0,2*Math.PI);x.strokeStyle='#e5b04e';x.setLineDash([7,5]);x.stroke();x.setLineDash([]);let imp=S.find(q=>q.sample_kind==='imported_reference');let a=P(imp.position_m);dot(x,a[0],a[1],'#62d5e8',6,'Imported reference');for(const condition of ['safe-zone-enabled','safe-zone-disabled']){let track=S.filter(q=>q.condition===condition);if(!track.length)continue;let first=P(track[0].position_m),color=condition==='safe-zone-enabled'?'#68d391':'#ff8b72';x.beginPath();x.moveTo(a[0],a[1]);x.lineTo(first[0],first[1]);x.strokeStyle='#ffbe55';x.setLineDash([4,5]);x.stroke();x.setLineDash([]);for(let i=1;i<track.length;i++){let p0=P(track[i-1].position_m),p1=P(track[i].position_m),dt=(track[i-1].source_wall_time&&track[i].source_wall_time)?(Date.parse(track[i].source_wall_time.replace(' ','T'))-Date.parse(track[i-1].source_wall_time.replace(' ','T')))/1000:null;if(dt!=null&&dt<=2){x.beginPath();x.moveTo(p0[0],p0[1]);x.lineTo(p1[0],p1[1]);x.strokeStyle=color;x.lineWidth=2;x.stroke();x.lineWidth=1}}track.forEach((q,i)=>{let p=P(q.position_m);dot(x,p[0],p[1],color,4,'')})}
x.strokeStyle='#73899b';x.beginPath();x.moveTo(26,h-24);x.lineTo(96,h-24);x.moveTo(26,h-24);x.lineTo(26,h-80);x.stroke();x.fillStyle='#dce6ee';x.fillText('● Imported   ● Enabled   ● Disabled',22,24);x.fillStyle='#9aabba';x.fillText('X/Z projection; Y vertical. No terrain or oriented block mesh in source.',105,h-22);}
function renderStats(s){let el=document.getElementById('stats');let m=M.measurement_summary[s?.condition]||{};let entries=[['Current sample',s?.sample_kind||'—'],['Position',s?`${s.position_m.map(v=>v.toFixed(2)).join(', ')} m`:'—'],['Linear speed',s?.linear_speed_mps==null?'unknown':s.linear_speed_mps+' m/s'],['Angular speed',s?.angular_speed_radps==null?'unknown':s.angular_speed_radps+' rad/s'],['Integrated path','unavailable · sparse samples'],['Net runtime displacement',m.net_runtime_displacement_m??'unavailable'],['Imported → first pose offset',m.imported_to_first_observed_offset_m==null?'—':m.imported_to_first_observed_offset_m.toFixed(3)+' m'],['Orientation / actuator state','not captured']];el.innerHTML=entries.map(([a,b])=>`<div class="stat"><b>${a}</b><span>${b}</span></div>`).join('')}
function renderEvents(){let c=document.getElementById('condition').value,k=cat.value,q=document.getElementById('search').value.toLowerCase(),sev=document.getElementById('severity').value,eid=ent.value;let list=E.filter(e=>(c==='all'||e.condition===c)&&(k==='all'||e.category===k)&&(sev==='all'||e.severity===sev)&&(eid==='all'||e.entity_id===eid)&&(!q||JSON.stringify(e).toLowerCase().includes(q)));document.getElementById('events').innerHTML=list.map(e=>`<div class="event ${selected===e.event_id?'selected':''}" data-id="${e.event_id}"><span class="badge">${e.category}</span><span class="badge ${e.severity==='warning'?'warn':''}">${e.severity}</span><b>${e.event_type}</b><div>${e.description}</div><small>${e.condition} · tick/time ${e.simulation_tick??'unknown'}/${e.simulation_time_s??'unknown'} · source ${e.source_wall_time??'time unknown'}</small></div>`).join('')||'<p class="muted">No matching events.</p>';document.querySelectorAll('.event').forEach(n=>n.onclick=()=>selectEvent(n.dataset.id));}
function selectEvent(id){let e=E.find(v=>v.event_id===id);if(!e)return;selected=id;let a=activeSamples(),eligible=a.map((s,n)=>({s,n})).filter(q=>q.s.run_id===e.run_id&&(e.entity_id==null||q.s.entity_id===e.entity_id));let hit=eligible.find(q=>e.source_wall_time&&q.s.source_wall_time===e.source_wall_time);if(!hit&&e.source_wall_time&&eligible.length){let t=Date.parse(e.source_wall_time.replace(' ','T'));eligible.sort((u,v)=>Math.abs(Date.parse(u.s.source_wall_time.replace(' ','T'))-t)-Math.abs(Date.parse(v.s.source_wall_time.replace(' ','T'))-t));hit=eligible[0]}let i=hit?.n??a.findIndex(s=>s.condition===e.condition&&s.entity_id===e.entity_id);if(i>=0){idx=i;focusAt=a[i].position_m}let detail=document.getElementById('details');detail.textContent=JSON.stringify(e,null,2)+'\n\nSupporting evidence: ';let link=document.createElement('a');link.href=e.source_ref.path;link.textContent=e.source_ref.path+(e.source_ref.line?` · line ${e.source_ref.line}`:'');link.target='_blank';link.rel='noreferrer';detail.append(link);draw()}
document.getElementById('scrub').oninput=e=>{idx=+e.target.value;draw()};document.getElementById('prev').onclick=()=>{idx=Math.max(0,idx-1);draw()};document.getElementById('next').onclick=()=>{idx=Math.min(activeSamples().length-1,idx+1);draw()};document.getElementById('condition').onchange=()=>{idx=0;draw()};cat.onchange=renderEvents;document.getElementById('severity').onchange=renderEvents;ent.onchange=renderEvents;document.getElementById('search').oninput=renderEvents;document.getElementById('showZone').onchange=draw;document.getElementById('showSubgrids').onchange=draw;document.getElementById('zoom').oninput=draw;document.getElementById('orbit').oninput=draw;document.getElementById('focus').onclick=()=>{let s=current();if(s)focusAt=s.position_m;panX=0;panZ=0;draw()};document.getElementById('reset').onclick=()=>{focusAt=null;panX=0;panZ=0;document.getElementById('zoom').value=1;document.getElementById('orbit').value=35;draw()};document.getElementById('prevEvent').onclick=()=>{let i=E.findIndex(e=>e.event_id===selected);selectEvent(E[(i<=0?E.length:i)-1].event_id)};document.getElementById('nextEvent').onclick=()=>{let i=E.findIndex(e=>e.event_id===selected);selectEvent(E[(i+1)%E.length].event_id)};document.getElementById('rate').onchange=()=>{if(timer){clearInterval(timer);timer=setInterval(()=>{idx=(idx+1)%activeSamples().length;draw()},1000/+document.getElementById('rate').value)}};document.getElementById('play').onclick=()=>{if(timer){clearInterval(timer);timer=null;play.textContent='Play'}else{play.textContent='Pause';timer=setInterval(()=>{idx=(idx+1)%activeSamples().length;draw()},1000/+document.getElementById('rate').value)}};for(const id of ['top','oblique']){const canvas=document.getElementById(id);let drag=null;canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(drag){panX+=e.clientX-drag[0];panZ+=e.clientY-drag[1];drag=[e.clientX,e.clientY];draw()}};canvas.onpointerup=()=>drag=null}window.onresize=draw;draw();
</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--enabled-log", type=Path, help="preserved stopped-run log for safe-zone-enabled condition")
    parser.add_argument("--disabled-log", type=Path, help="preserved stopped-run log for safe-zone-disabled condition")
    args = parser.parse_args()
    manifest = build_bundle(args.source_root, args.output, args.enabled_log, args.disabled_log)
    print(json.dumps({"schema": manifest["schema"], "output": str(args.output),
                      "samples": manifest["available_data"]["position_samples"],
                      "events": sum(1 for _ in (args.output / "events.ndjson").open()),
                      "source_run_ids": manifest["source_run_ids"], "commands_issued": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
