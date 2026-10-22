#!/usr/bin/env python3
"""Create and verify disposable destructive-lab authority contracts."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


LAB_FILE = ".vragecage-lab.json"
ACTIVE_FILE = ".vragecage-active-run.json"
PENDING_FILE = ".vragecage-pending-command.json"
LOCK_FILE = ".vragecage-lab.lock"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
DIRECT_STOP_CONFIRMATION_TICKS = 120
DIRECT_MAX_COMMAND_TICKS = 2700
AGENT_DISCOVERY_FILE = "agent-core-discovery-v1.json"
AGENT_DISCOVERY_MAX_AGE = timedelta(seconds=15)
DISCOVERY_RECEIPT_SCHEMA = "vragecage.agent-core-discovery-receipt.v1"


def build_command_activation_contract(run_id: str, fixture_id: str, operation_id: str,
                                      bootstrap_nonce: str, baseline_discovery_run_id: str,
                                      receipt: dict[str, object], binding: dict[str, object],
                                      now: datetime | None = None) -> dict[str, object]:
    """Build the offline stage-two, preflight-only command activation contract.

    A runtime host/mod consumer is not wired yet. This pure contract is safe to
    validate and test independently; it cannot start a service or dispatch motion.
    """
    hex32 = re.compile(r"^[0-9a-f]{32}$")
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    if not all(isinstance(value, str) and hex32.fullmatch(value) for value in
               (run_id, fixture_id, operation_id, bootstrap_nonce, baseline_discovery_run_id)):
        raise SystemExit("stage-two activation identity is malformed")
    if not isinstance(receipt, dict) or receipt.get("schema") != DISCOVERY_RECEIPT_SCHEMA:
        raise SystemExit("stage-two discovery receipt schema is unsupported")
    receipt_nonce = receipt.get("bootstrapNonce")
    session_id = receipt.get("sessionId")
    discovery_run_id = receipt.get("runId")
    tick = receipt.get("capturedSimulationTick")
    fingerprint = receipt.get("exportSha256")
    captured_text = receipt.get("capturedAtUtc")
    if (receipt_nonce != bootstrap_nonce or not isinstance(session_id, str) or not hex32.fullmatch(session_id) or
            not isinstance(discovery_run_id, str) or not hex32.fullmatch(discovery_run_id) or
            discovery_run_id == baseline_discovery_run_id or
            not isinstance(tick, int) or isinstance(tick, bool) or tick < 0 or
            not isinstance(fingerprint, str) or not hex64.fullmatch(fingerprint) or
            not isinstance(captured_text, str) or receipt.get("uniqueBlueTruck") is not True or
            receipt.get("inventoryComplete") is not True):
        raise SystemExit("stage-two discovery receipt is stale-session, incomplete, or mismatched")
    try:
        captured = datetime.fromisoformat(captured_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit(f"stage-two discovery receipt time is invalid: {error}") from error
    current = now or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise SystemExit("stage-two discovery receipt time must include a UTC offset")
    age = current.astimezone(timezone.utc) - captured.astimezone(timezone.utc)
    if age < timedelta(0) or age > AGENT_DISCOVERY_MAX_AGE:
        raise SystemExit("stage-two discovery receipt is stale or from the future")
    expected_binding = {
        "run_id": discovery_run_id, "tick": tick, "fingerprint": fingerprint,
        "core_id": receipt.get("coreEntityId"), "grid_id": receipt.get("gridEntityId"),
        "controller_id": receipt.get("controllerEntityId"), "camera_id": receipt.get("cameraEntityId"),
    }
    if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in expected_binding.items()):
        raise SystemExit("stage-two discovery receipt differs from the complete current export")
    for key in ("core_id", "grid_id", "controller_id", "camera_id"):
        value = expected_binding[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SystemExit("stage-two discovery receipt has an invalid exact vehicle identity")
    context = {
        "schema": "vragecage.preflight-context.v2", "run_id": run_id,
        "fixture_id": fixture_id, "operation_id": operation_id, "action": "preflight-only",
        "agent_core_id": expected_binding["core_id"], "grid_id": expected_binding["grid_id"],
        "controller_id": expected_binding["controller_id"], "depth_camera_id": expected_binding["camera_id"],
        "discovery_run_id": discovery_run_id, "discovery_tick": tick,
        "discovery_fingerprint": fingerprint, "session_id": session_id,
        "bootstrap_nonce": bootstrap_nonce, "motion_dispatch": False,
    }
    context_sha256 = hashlib.sha256(canonical(context)).hexdigest()
    return {"schema": "vragecage.command-activation.v1", "stage": 2,
            "run_id": run_id, "fixture_id": fixture_id, "operation_id": operation_id,
            "action": "preflight-only", "context": context, "context_sha256": context_sha256,
            "motion_dispatch": False}


def _read_vwdr2(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2048:
        raise SystemExit("discovery receipt is missing or unsafe")
    raw = path.read_text(encoding="utf-8")
    lines = raw.split("\n")
    if len(lines) != 3 or lines[0] != "VWDR2" or lines[2] or not raw.endswith("\n"):
        raise SystemExit("discovery receipt wire format is invalid")
    fields = lines[1].split("|")
    if len(fields) != 10:
        raise SystemExit("discovery receipt field count is invalid")
    status, run_id, fixture_id, nonce, session_id, exporter_run, tick_text, captured_text, fingerprint, count_text = fields
    if (status not in {"mod-ready", "host-ready"} or
            not all(re.fullmatch(r"[0-9a-f]{32}", value) for value in
                    (run_id, fixture_id, nonce, session_id, exporter_run)) or
            not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or
            not tick_text.isdigit() or not count_text.isdigit()):
        raise SystemExit("discovery receipt contains malformed identities")
    grid_count = int(count_text)
    if not 1 <= grid_count <= 100:
        raise SystemExit("discovery receipt contains an invalid observed-grid count")
    try:
        captured = datetime.fromisoformat(captured_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit("discovery receipt timestamp is invalid") from error
    if captured.tzinfo is None:
        raise SystemExit("discovery receipt timestamp must include UTC offset")
    return {"status": status, "run_id": run_id, "fixture_id": fixture_id, "nonce": nonce,
            "session_id": session_id, "exporter_run_id": exporter_run, "tick": int(tick_text),
            "captured": captured.astimezone(timezone.utc), "fingerprint": fingerprint,
            "observed_grid_count": grid_count}


def _validate_discovery_receipt_binding(receipt: dict[str, object], status: str,
                                        run_id: str, fixture_id: str, nonce: str,
                                        session_id: str, exporter_run_id: str, tick: int,
                                        fingerprint: str, now: datetime) -> None:
    age = now - receipt["captured"]
    if (receipt["status"] != status or receipt["run_id"] != run_id or
            receipt["fixture_id"] != fixture_id or receipt["nonce"] != nonce or
            receipt["session_id"] != session_id or receipt["exporter_run_id"] != exporter_run_id or
            receipt["tick"] != tick or receipt["fingerprint"] != fingerprint or
            receipt["observed_grid_count"] <= 0 or age < timedelta(0) or age > AGENT_DISCOVERY_MAX_AGE):
        raise SystemExit("stage-two receipt is stale or mismatched")


def _ack_matches(raw: str, status: str, expected: tuple[object, ...], issued: datetime,
                 now: datetime) -> bool:
    lines = raw.split("\n")
    if len(lines) != 3 or lines[0] != "VWAA1" or lines[2] or not raw.endswith("\n"):
        return False
    fields = lines[1].split("|")
    if len(fields) != 11 or fields[0] != status:
        return False
    try:
        actual = (fields[1], fields[2], fields[3], fields[4], fields[5], fields[6],
                  int(fields[7]), fields[8], fields[9])
        ack_time = datetime.fromisoformat(fields[10].replace("Z", "+00:00"))
    except ValueError:
        return False
    return actual == expected and ack_time.tzinfo is not None and issued <= ack_time.astimezone(timezone.utc) <= now + timedelta(seconds=5)


def _wait_for_stage_two_acknowledgments(discovery_path: Path, run_id: str,
                                       expected: tuple[object, ...], issued: datetime,
                                       maximum_wait_seconds: int) -> set[str]:
    deadline = time.monotonic() + max(0, min(maximum_wait_seconds, 90))
    seen: set[str] = set()
    while time.monotonic() < deadline and len(seen) < 2:
        for status in ("host-accepted", "mod-accepted"):
            ack_path = discovery_path.with_name("voidwright-ack-" + run_id + "-" + status + ".txt")
            if not ack_path.is_file() or ack_path.is_symlink() or ack_path.stat().st_size > 2048:
                continue
            if _ack_matches(ack_path.read_text(encoding="utf-8"), status, expected, issued,
                            datetime.now(timezone.utc)):
                seen.add(status)
        if len(seen) < 2:
            time.sleep(0.25)
    if len(seen) != 2:
        raise SystemExit("stage-two activation timed out waiting for both host and mod acceptance acknowledgments")
    return seen


def _agent_discovery_storage(instance: Path) -> Path:
    """Resolve API WorldStorage for the configured session without following links."""
    config = instance / "SpaceEngineers-Dedicated.cfg"
    if config.is_symlink() or not config.is_file():
        raise SystemExit("dedicated-server configuration is missing or unsafe")
    try:
        load_world = ET.parse(config).getroot().findtext("LoadWorld")
    except (OSError, ET.ParseError) as error:
        raise SystemExit(f"invalid dedicated-server configuration: {error}") from error
    if not load_world:
        raise SystemExit("dedicated-server configuration has no LoadWorld")
    world = Path(load_world)
    if not world.is_absolute():
        world = instance / world
    if world.is_symlink():
        raise SystemExit("configured lab world must not be a symbolic link")
    try:
        world = world.resolve(strict=True)
        world.relative_to(instance)
    except (OSError, ValueError) as error:
        raise SystemExit("configured lab world escaped the disposable instance") from error
    sandbox = world / "Sandbox.sbc"
    if sandbox.is_symlink() or not sandbox.is_file():
        raise SystemExit("configured lab world's Sandbox.sbc is missing or unsafe")
    try:
        session_name = ET.parse(sandbox).getroot().findtext("SessionName")
    except (OSError, ET.ParseError) as error:
        raise SystemExit(f"invalid configured lab checkpoint: {error}") from error
    if (not session_name or session_name in {".", ".."} or
            Path(session_name).name != session_name or "/" in session_name or "\\" in session_name):
        raise SystemExit("configured lab world has no safe SessionName")
    storage = instance / "Saves" / session_name / "Storage"
    for path in (instance / "Saves", instance / "Saves" / session_name, storage,
                 storage / "VRage.Game"):
        if path.is_symlink():
            raise SystemExit("configured session WorldStorage path must not traverse a symbolic link")
    try:
        storage_root = (storage / "VRage.Game").resolve(strict=True)
        storage_root.relative_to(instance)
    except (OSError, ValueError) as error:
        raise SystemExit("configured session VRage.Game WorldStorage is missing or escaped the instance") from error
    if not storage_root.is_dir():
        raise SystemExit("configured session VRage.Game WorldStorage is not a directory")
    return storage_root


def activate_preflight_from_discovery(instance: Path, maximum_wait_seconds: int = 45) -> dict[str, object]:
    return _activate_stage_two_from_discovery(instance, "preflight-only", maximum_wait_seconds)


def activate_zero_hold_from_discovery(instance: Path, maximum_wait_seconds: int = 45) -> dict[str, object]:
    return _activate_stage_two_from_discovery(instance, "direct-maneuver", maximum_wait_seconds)


def _build_stage_two_command(action: str, fixture_id: str, operation_id: str,
                             binding: dict[str, object], fingerprint: str) -> dict[str, object]:
    if action == "preflight-only":
        return {"schema": "vragecage.lab-command.v1", "action": action,
                "fixture_id": fixture_id, "operation_id": operation_id,
                "controller_id": binding["controller_id"], "grid_id": binding["grid_id"],
                "depth_camera_id": binding["camera_id"], "agent_core_id": binding["core_id"],
                "discovery_run_id": binding["run_id"], "discovery_tick": binding["tick"],
                "discovery_fingerprint": fingerprint, "target": [0.0, 0.0, 0.0],
                "arrival_radius": 0.5, "start_delay_ticks": 0, "reverse": False,
                "max_speed": 0.8, "timeout_ticks": 300}
    if action == "direct-maneuver":
        return {"schema": "vragecage.lab-command.v1", "action": action,
                "fixture_id": fixture_id, "operation_id": operation_id,
                "controller_id": binding["controller_id"], "grid_id": binding["grid_id"],
                "depth_camera_id": binding["camera_id"], "agent_core_id": binding["core_id"],
                "discovery_run_id": binding["run_id"], "discovery_tick": binding["tick"],
                "discovery_fingerprint": fingerprint, "target": [0.0, 0.0, 0.0],
                "arrival_radius": 0.5, "start_delay_ticks": 0, "reverse": False,
                "max_speed": 0.1, "timeout_ticks": 180,
                "segments": [{"kind": "stationary-hold", "value": 0.0,
                              "max_speed": 0.0, "timeout_ticks": 60}]}
    raise SystemExit("unsupported stage-two action")


def _activate_stage_two_from_discovery(instance: Path, action: str,
                                       maximum_wait_seconds: int = 45) -> dict[str, object]:
    """Pin one current bootstrap export and publish a fixed stage-two action."""
    if action not in {"preflight-only", "direct-maneuver"}:
        raise SystemExit("unsupported stage-two action")
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    active = load_object(instance / ACTIVE_FILE)
    run_id = str(active.get("run_id", ""))
    if (active.get("bootstrap_mode") != "discovery-only" or
            not re.fullmatch(r"[0-9a-f]{32}", run_id) or
            not re.fullmatch(r"[0-9a-f]{32}", str(active.get("bootstrap_nonce", "")))):
        raise SystemExit("stage-two activation requires the active discovery-only run")
    active = verify_runtime(instance, run_id)
    storage = _agent_discovery_storage(instance)
    discoveries = list(storage.rglob(AGENT_DISCOVERY_FILE)) if storage.is_dir() else []
    if len(discoveries) != 1:
        raise SystemExit("stage-two activation requires exactly one configured-session WorldStorage discovery export")
    discovery_path = discoveries[0]
    binding = discover_direct_target(instance)
    raw = discovery_path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if binding["fingerprint"] != fingerprint or binding["run_id"] == active.get("baseline_discovery_run_id"):
        raise SystemExit("stage-two discovery changed or belongs to the bootstrap baseline")
    discovery = json.loads(raw)
    nonce, fixture_id = str(active["bootstrap_nonce"]), str(active["fixture_id"])
    session_id = discovery.get("sessionId")
    if (discovery.get("labRunId") != run_id or discovery.get("fixtureId") != fixture_id or
            discovery.get("bootstrapNonce") != nonce or not isinstance(session_id, str) or
            not re.fullmatch(r"[0-9a-f]{32}", session_id)):
        raise SystemExit("discovery export does not match the active run, fixture, nonce, and session")
    suspension_ids = binding.get("suspension_ids")
    if (not isinstance(suspension_ids, list) or not suspension_ids or
            any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in suspension_ids) or
            len(set(suspension_ids)) != len(suspension_ids)):
        raise SystemExit("stage-two activation requires a complete, non-empty exact suspension identity set")
    mod_receipt = _read_vwdr2(discovery_path.with_name("voidwright-discovery-" + run_id + ".receipt"))
    host_receipt = _read_vwdr2(discovery_path.with_name("voidwright-host-ready-" + run_id + ".receipt"))
    now = datetime.now(timezone.utc)
    for receipt, status in ((mod_receipt, "mod-ready"), (host_receipt, "host-ready")):
        _validate_discovery_receipt_binding(receipt, status, run_id, fixture_id, nonce,
                                            session_id, binding["run_id"], binding["tick"],
                                            fingerprint, now)
    if (instance / PENDING_FILE).exists() or active.get("command"):
        raise SystemExit("stage-two activation refuses an existing or replayed command")
    operation_id = secrets.token_hex(16)
    command = _build_stage_two_command(action, fixture_id, operation_id, binding, fingerprint)
    contract, _ = validate(instance)
    validate_lab_command(command, contract)
    verify_voidwright_mod_staged(instance)
    # Resolve the same exact fixture binding before making the one-shot command durable.
    # create_preflight_context repeats this check after persistence as defense in depth.
    require_direct_target_identity(instance, command["controller_id"], command["grid_id"],
                                   command["depth_camera_id"], command)
    run_dir = Path(str(active["quarantine"]))
    command_path = run_dir / "command.json"
    payload = canonical(command)
    try:
        with command_path.open("xb") as stream:
            os.chmod(command_path, 0o600); stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError as error:
        raise SystemExit("run already contains a stage-two command") from error
    active.update({"command": command, "command_sha256": hashlib.sha256(payload).hexdigest(),
                   "stage_two_operation_id": operation_id})
    atomic_write(instance / ACTIVE_FILE, active)
    atomic_write(run_dir / "run.json", active)
    context = create_preflight_context(instance, run_id)
    challenge = secrets.token_hex(16)
    issued, expires = now, now + timedelta(seconds=90)
    activation_fields = [run_id, fixture_id, operation_id, nonce, session_id, binding["run_id"],
                         str(binding["tick"]), fingerprint, str(binding["core_id"]), str(binding["grid_id"]),
                         str(binding["controller_id"]), str(binding["camera_id"]),
                         ",".join(str(value) for value in binding["suspension_ids"]),
                         __import__("base64").b64encode(str(context["path"]).encode()).decode(),
                         str(context["sha256"]), challenge,
                         issued.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z",
                         expires.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z", action]
    artifact = "VWCA1\n" + "|".join(activation_fields) + "\n"
    artifact_path = discovery_path.with_name("voidwright-activation-" + run_id + ".txt")
    try:
        with artifact_path.open("x", encoding="utf-8") as stream:
            os.chmod(artifact_path, 0o600); stream.write(artifact); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError as error:
        raise SystemExit("stage-two activation artifact already exists; replay refused") from error
    expected = (run_id, fixture_id, operation_id, nonce, session_id, binding["run_id"],
                binding["tick"], fingerprint, str(context["sha256"]))
    seen = _wait_for_stage_two_acknowledgments(discovery_path, run_id, expected, issued, maximum_wait_seconds)
    return {"schema": "vragecage.command-activation.v1", "run_id": run_id,
            "operation_id": operation_id, "artifact": str(artifact_path), "acknowledgments": sorted(seen),
            "context": context, "expires_at_utc": expires.isoformat()}
def discover_direct_target(instance: Path, now: datetime | None = None,
                           expected_grid_id: int | None = None) -> dict[str, object]:
    """Resolve one fresh, complete physical grid by exact ID from the mod's read-only export."""
    storage = _agent_discovery_storage(instance)
    matches = []
    for path in storage.rglob(AGENT_DISCOVERY_FILE):
        if path.is_symlink() or not path.is_file():
            raise SystemExit("Agent Core discovery path contains a link or non-file")
        parent = path.parent
        while parent != storage:
            if parent.is_symlink():
                raise SystemExit("Agent Core discovery path traverses a symbolic link")
            parent = parent.parent
        if path.stat().st_size > 16 * 1024 * 1024:
            raise SystemExit("Agent Core discovery exceeds its 16 MiB bound")
        matches.append(path)
        if len(matches) > 1:
            raise SystemExit("multiple Agent Core discovery exports are ambiguous")
    if len(matches) != 1:
        raise SystemExit("fresh Agent Core discovery export is missing or ambiguous")
    path = matches[0]
    raw = path.read_bytes()
    try:
        discovery = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"Agent Core discovery is malformed: {error}")
    if not isinstance(discovery, dict) or discovery.get("schemaVersion") != 1 or \
            discovery.get("scope") != "physical_grid_group":
        raise SystemExit("Agent Core discovery schema or grid scope is unsupported")
    run_id = discovery.get("runId")
    tick = discovery.get("capturedSimulationTick")
    captured_text = discovery.get("capturedAtUtc")
    if (not isinstance(run_id, str) or len(run_id) != 32 or
            any(c not in "0123456789abcdef" for c in run_id) or
            not isinstance(tick, int) or isinstance(tick, bool) or tick < 0 or
            not isinstance(captured_text, str)):
        raise SystemExit("Agent Core discovery is missing its run, tick, or capture time")
    try:
        captured = datetime.fromisoformat(captured_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit(f"Agent Core discovery capture time is invalid: {error}")
    if captured.tzinfo is None:
        raise SystemExit("Agent Core discovery capture time must include a UTC offset")
    current = now or datetime.now(timezone.utc)
    age = current.astimezone(timezone.utc) - captured.astimezone(timezone.utc)
    if age < timedelta(0) or age > AGENT_DISCOVERY_MAX_AGE:
        raise SystemExit("Agent Core discovery is stale or from the future")
    agents = discovery.get("agents")
    if (discovery.get("agentsTruncated") is not False or not isinstance(agents, list) or
            not agents):
        raise SystemExit("Agent Core discovery agent inventory is incomplete")
    candidates = []
    all_blocks = []
    all_core_ids = []
    for agent in agents:
        if not isinstance(agent, dict):
            raise SystemExit("Agent Core discovery contains an invalid agent record")
        grids = agent.get("physicalGrids")
        if (agent.get("physicalGridInventoryComplete") is not True or
                agent.get("typedBlockInventoryComplete") is not True or
                agent.get("mechanicalConnectionInventoryComplete") is not True or
                agent.get("physicalGridCountObserved") != agent.get("physicalGridCountIncluded") or
                not isinstance(grids, list) or
                agent.get("physicalGridCountIncluded") != len(grids)):
            raise SystemExit("Agent Core discovery physical-grid or typed-block inventory is incomplete")
        if agent.get("coreEntityId", 0) <= 0 or agent.get("anchorGridEntityId", 0) <= 0:
            raise SystemExit("Agent Core discovery contains an invalid Core or anchor ID")
        all_core_ids.append(agent["coreEntityId"])
        for grid in grids:
            if not isinstance(grid, dict):
                raise SystemExit("Agent Core discovery contains an invalid physical-grid record")
            grid_blocks = grid.get("blocks")
            if (not isinstance(grid.get("entityId"), int) or not isinstance(grid_blocks, list) or
                    not isinstance(grid.get("blockCountObserved"), int) or
                    grid.get("blockCountObserved") != len(grid_blocks)):
                raise SystemExit("Agent Core discovery contains an incomplete physical-grid block inventory")
            if any(not isinstance(block, dict) or block.get("gridEntityId") != grid.get("entityId")
                   for block in grid_blocks):
                raise SystemExit("Agent Core discovery contains a cross-grid block")
            all_blocks.extend(grid_blocks)
            grid_id = grid["entityId"]
            if (expected_grid_id is not None and grid_id != expected_grid_id) or \
                    agent.get("anchorGridEntityId") != grid_id:
                continue
            grid_blocks = grid["blocks"]
            controllers = [block for block in grid_blocks if block.get("typedKind") == "controller"]
            cameras = [block for block in grid_blocks if block.get("typedKind") == "camera"]
            suspensions = [block for block in grid_blocks if block.get("typedKind") == "suspension" and
                           block.get("hasSuspensionState") is True]
            if len(controllers) == 1 and len(cameras) == 1 and suspensions:
                candidates.append((agent, grid, controllers, cameras, suspensions))
    if not candidates:
        raise SystemExit("no complete Agent Core vehicle matches the exact physical-grid binding")
    if len(candidates) != 1:
        raise SystemExit("multiple complete physical grids are ambiguous; supply the exact grid ID")
    if any(agent.get("blocksObserved") != sum(
            grid.get("blockCountObserved", -1) for grid in agent["physicalGrids"])
            for agent in agents):
        raise SystemExit("Agent Core discovery block counts do not match its complete typed inventory")
    agent, grid, controllers, cameras, suspensions = candidates[0]
    grid_id = grid.get("entityId")
    core_id = agent.get("coreEntityId")
    if (not isinstance(grid_id, int) or isinstance(grid_id, bool) or grid_id <= 0 or
            not isinstance(core_id, int) or isinstance(core_id, bool) or core_id <= 0 or
            agent.get("anchorGridEntityId") != grid_id or
            not isinstance(grid.get("blockCountObserved"), int) or
            not isinstance(grid.get("blocks"), list) or
            grid.get("blockCountObserved") != len(grid["blocks"])):
        raise SystemExit("selected grid does not have one complete on-grid Agent Core inventory")
    blocks = grid["blocks"]
    for block in blocks:
        if not isinstance(block, dict) or block.get("gridEntityId") != grid_id:
            raise SystemExit("selected grid discovery contains a cross-grid block")
    if len(controllers) != 1 or len(cameras) != 1 or not suspensions:
        raise SystemExit("selected grid needs one discovered controller and camera plus complete suspension state")
    controller_id, camera_id = controllers[0].get("entityId"), cameras[0].get("entityId")
    suspension_ids = [b.get("entityId") for b in suspensions]
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in [controller_id, camera_id, *suspension_ids]):
        raise SystemExit("selected grid discovery contains invalid typed block IDs")
    if (len(set(all_core_ids)) != len(all_core_ids) or
            sum(1 for block in all_blocks if block.get("entityId") == controller_id) != 1 or
            sum(1 for block in all_blocks if block.get("entityId") == camera_id) != 1):
        raise SystemExit("Agent Core discovery contains duplicate Core, controller, or camera identities")
    return {
        "run_id": run_id, "tick": tick, "fingerprint": hashlib.sha256(raw).hexdigest(),
        "core_id": core_id, "grid_id": grid_id, "controller_id": controller_id,
        "camera_id": camera_id, "suspension_ids": sorted(suspension_ids),
        "discovery_path": str(path.resolve()),
    }


def require_direct_target_identity(instance: Path, controller_id: int | None, grid_id: int | None,
                                   camera_id: int | None, command: dict[str, object] | None = None) -> dict[str, object]:
    """Fail closed unless the supplied exact grid, controller, and camera match this snapshot."""
    binding = discover_direct_target(instance, expected_grid_id=grid_id)
    if command is not None:
        expected = {
            "controller_id": binding["controller_id"], "grid_id": binding["grid_id"],
            "depth_camera_id": binding["camera_id"], "agent_core_id": binding["core_id"],
            "discovery_run_id": binding["run_id"], "discovery_tick": binding["tick"],
            "discovery_fingerprint": binding["fingerprint"],
        }
        if any(command.get(name) != value for name, value in expected.items()):
            raise SystemExit("command target identity differs from the fresh Agent Core discovery binding")
    if any(value is not None and value != binding[name]
           for value, name in ((controller_id, "controller_id"), (grid_id, "grid_id"), (camera_id, "camera_id"))):
        raise SystemExit("manually supplied target IDs differ from fresh Agent Core discovery")
    controller_id = binding["controller_id"]
    grid_id = binding["grid_id"]
    camera_id = binding["camera_id"]
    sector = instance / "World/SANDBOX_0_0_0_.sbs"
    try:
        root = ET.parse(sector).getroot()
    except (OSError, ET.ParseError) as error:
        raise SystemExit(f"cannot verify selected grid identity from fixture snapshot: {error}")
    grids = []
    for grid in root.iter("MyObjectBuilder_EntityBase"):
        if grid.get(XSI_TYPE) != "MyObjectBuilder_CubeGrid":
            continue
        try:
            actual_grid_id = int(grid.findtext("EntityId") or "0")
        except ValueError:
            actual_grid_id = 0
        cube_blocks = grid.find("CubeBlocks")
        blocks = list(cube_blocks) if cube_blocks is not None else []
        # This direct control mode currently requires a cockpit entity, even though
        # the runtime binding itself uses only exact entity IDs.
        controllers = [b for b in blocks if b.get(XSI_TYPE) == "MyObjectBuilder_Cockpit"
                       and (b.findtext("EntityId") or "").isdigit()
                       and int(b.findtext("EntityId") or "0") == controller_id]
        cameras = [b for b in blocks if (b.get(XSI_TYPE) or "").endswith("CameraBlock")
                   and (b.findtext("EntityId") or "").isdigit()
                   and int(b.findtext("EntityId") or "0") == camera_id]
        grids.append((actual_grid_id, controllers, cameras))
    id_grids = [item for item in grids if item[0] == grid_id]
    controller_grids = [item for item in grids if item[1]]
    camera_grids = [item for item in grids if item[2]]
    if (len(id_grids) != 1
            or len(controller_grids) != 1 or controller_grids[0] is not id_grids[0]
            or len(camera_grids) != 1 or camera_grids[0] is not id_grids[0]):
        raise SystemExit("target identity is unverified: exact physical grid, cockpit, and camera must match the fixture snapshot")
    return binding


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic_write(path: Path, value: object, mode: int = 0o600) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical(value))
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def load_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid lab contract {path}: {error}")
    if not isinstance(value, dict):
        raise SystemExit(f"invalid lab contract object: {path}")
    return value


def tree_sha256(root: Path) -> str:
    """Hash a staged source tree by sorted relative path and file bytes."""
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("staged mod tree must be a regular directory")
    digest = hashlib.sha256()
    total_bytes = 0
    files = 0
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink():
            raise SystemExit("staged mod tree contains a symbolic link")
        if not path.is_file():
            continue
        files += 1
        total_bytes += path.stat().st_size
        if files > 100_000 or total_bytes > 4 * 1024**3:
            raise SystemExit("staged mod tree exceeds the preflight hash limit")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def verify_voidwright_mod_staged(instance: Path) -> tuple[Path, str]:
    mods_path = instance / "Mods"
    package = mods_path / "Voidwright"
    if mods_path.is_symlink() or not package.is_dir() or package.is_symlink() or not (package / "Data").is_dir():
        raise SystemExit("depth navigation requires the exact Voidwright source package")
    stage_path = instance / ".vragecage-mod-stage.json"
    if stage_path.is_symlink() or not stage_path.is_file():
        raise SystemExit("depth navigation requires a registered Magnetar stage receipt")
    try:
        stage = load_object(stage_path)
    except SystemExit as error:
        raise SystemExit(f"invalid Magnetar mod stage receipt: {error}")
    workshop_id = stage.get("synthetic_workshop_id")
    stage_name = stage.get("name")
    if (stage.get("schema") != "vragecage.mod-stage.v1" or stage.get("engine") != "magnetar"
            or stage.get("instance") != instance.name
            or not isinstance(stage_name, str)
            or not re.fullmatch(r"Voidwright(?:LifecycleDiag[A-Za-z0-9_-]{1,48})?", stage_name)
            or not isinstance(workshop_id, int) or not 90_000_000_000_000_000 <= workshop_id < 90_100_000_000_000_000):
        raise SystemExit("Magnetar stage receipt does not identify this instance's Voidwright mod")
    worker_root = instance.parent.parent.resolve()
    cache_root = worker_root / "magnetar-steam/steamapps/workshop/content/244850"
    staged_path = Path(str(stage.get("path", "")))
    expected_path = cache_root / str(workshop_id)
    cache_ancestors = (worker_root / "magnetar-steam", worker_root / "magnetar-steam/steamapps",
                       worker_root / "magnetar-steam/steamapps/workshop",
                       worker_root / "magnetar-steam/steamapps/workshop/content", cache_root)
    if (any(path.is_symlink() for path in cache_ancestors)
            or staged_path != expected_path or staged_path.is_symlink()
            or not staged_path.is_dir() or not (staged_path / "Data").is_dir()):
        raise SystemExit("Magnetar stage receipt points outside this worker's workshop cache")

    # Match register-magnetar-mod.py's length-prefixed tree hash so the receipt,
    # actual Magnetar cache, and instance-local source mirror name identical bytes.
    def registered_tree_hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise SystemExit("Voidwright staged tree contains a symbolic link")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    receipt_hash = stage.get("tree_sha256")
    if (not isinstance(receipt_hash, str) or len(receipt_hash) != 64
            or registered_tree_hash(staged_path) != receipt_hash
            or registered_tree_hash(package) != receipt_hash):
        raise SystemExit("Voidwright source, Magnetar cache, and stage receipt hashes do not match")

    sources_path = instance / "MagnetarConfig/Sources/sources.xml"
    profile_path = instance / "MagnetarConfig/Profiles/Current.xml"
    if any(path.is_symlink() or not path.is_file() for path in (sources_path, profile_path)):
        raise SystemExit("Magnetar mod registration configuration is missing or unsafe")
    try:
        sources = ET.parse(sources_path).getroot()
        profile = ET.parse(profile_path).getroot()
    except ET.ParseError as error:
        raise SystemExit(f"invalid Magnetar mod registration configuration: {error}")
    registrations = [node for node in sources.findall("./ModSources/Mod")
                     if node.findtext("ID") == str(workshop_id)]
    if (len(registrations) != 1 or registrations[0].findtext("Name") != stage_name
            or registrations[0].findtext("ID") != str(workshop_id)
            or registrations[0].findtext("Enabled", "true").lower() != "true"):
        raise SystemExit("Magnetar Sources does not enable the receipt-bound Voidwright package")
    profile_ids = [node.text for node in profile.findall("./Mods/unsignedLong")]
    if profile_ids.count(str(workshop_id)) != 1:
        raise SystemExit("Magnetar Current profile does not select the receipt-bound Voidwright package")

    for filename in ("Sandbox.sbc", "Sandbox_config.sbc"):
        path = instance / "World" / filename
        if path.is_symlink() or not path.is_file():
            raise SystemExit("Voidwright multiplayer registration requires regular world configuration files")
        try:
            mods = ET.parse(path).getroot().find("Mods")
        except ET.ParseError as error:
            raise SystemExit(f"invalid world mod configuration: {error}")
        entries = [item for item in (mods.findall("ModItem") if mods is not None else [])
                   if item.findtext("Name") == "Voidwright"]
        if entries:
            raise SystemExit("unpublished local Voidwright entry would be rejected by multiplayer")
    return mods_path, tree_sha256(mods_path)


def ensure_instance(instance: Path) -> tuple[Path, dict[str, object]]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    world_path = instance / "World"
    if world_path.is_symlink():
        raise SystemExit("lab world must not be a symbolic link")
    world = world_path.resolve()
    if world.parent != instance or not world.is_dir():
        raise SystemExit("lab world must be the instance's direct World directory")
    imported = load_object(instance / ".vragecage-world-import.json")
    if imported.get("instance") != instance.name or imported.get("tree_sha256") is None:
        raise SystemExit("lab authority requires a matching world-import receipt")
    if imported.get("engine") != "magnetar":
        raise SystemExit("destructive-lab authority currently requires the Magnetar engine")
    try:
        configured_world = ET.parse(instance / "SpaceEngineers-Dedicated.cfg").getroot().findtext("LoadWorld")
    except (OSError, ET.ParseError) as error:
        raise SystemExit(f"invalid dedicated-server configuration: {error}")
    if not configured_world or Path(configured_world).resolve() != world:
        raise SystemExit("dedicated server is not configured for the contracted lab world")
    return world, imported


def validate(instance: Path) -> tuple[dict[str, object], dict[str, object]]:
    world, imported = ensure_instance(instance)
    root_path = instance / LAB_FILE
    root_raw = root_path.read_bytes() if root_path.is_file() else b""
    if not root_raw:
        raise SystemExit("lab authority contract is missing")
    contract = load_object(root_path)
    expected = {
        "schema": "vragecage.lab-authority.v1",
        "authority": "destructive-lab",
        "disposable": True,
        "instance": instance.name,
        "engine": imported.get("engine"),
        "source_tree_sha256": imported.get("tree_sha256"),
    }
    for key, value in expected.items():
        if contract.get(key) != value:
            raise SystemExit(f"lab authority mismatch for {key}")
    fixture_id = contract.get("fixture_id")
    if not isinstance(fixture_id, str) or len(fixture_id) != 32 or any(c not in "0123456789abcdef" for c in fixture_id):
        raise SystemExit("invalid lab fixture identity")
    return contract, imported


def fixture_snapshot(instance: Path, contract: dict[str, object]) -> dict[str, object]:
    sector = instance / "World/SANDBOX_0_0_0_.sbs"
    if sector.is_symlink() or not sector.is_file():
        raise SystemExit("fixture sector must be a regular non-symlink file")
    ledger_path = instance / ".vragecage-fixture-edits.json"
    if not ledger_path.exists():
        return {
            "fixture_sector_sha256": hashlib.sha256(sector.read_bytes()).hexdigest(),
            "fixture_edit_count": 0,
        }
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise SystemExit("fixture edit ledger must be a regular non-symlink file")
    ledger_raw = ledger_path.read_bytes()
    ledger = load_object(ledger_path)
    edits = ledger.get("edits")
    if (ledger.get("schema") != "vragecage.fixture-edits.v1"
            or ledger.get("instance") != instance.name
            or ledger.get("fixture_id") != contract.get("fixture_id")
            or ledger.get("source_tree_sha256") != contract.get("source_tree_sha256")
            or not isinstance(edits, list) or not edits):
        raise SystemExit("fixture edit ledger does not match the active lab contract")
    previous = None
    expected_names: dict[int, tuple[str, str]] = {}
    operation_ids: set[str] = set()
    for edit in edits:
        if (not isinstance(edit, dict) or edit.get("action") != "voidwright-opt-in"
                or edit.get("previous_operation_id") != previous
                or not isinstance(edit.get("operation_id"), str)
                or len(edit["operation_id"]) != 32
                or any(character not in "0123456789abcdef" for character in edit["operation_id"])
                or edit["operation_id"] in operation_ids
                or not isinstance(edit.get("controller_id"), int)
                or not isinstance(edit.get("new_name"), str)):
            raise SystemExit("fixture edit ledger contains an invalid edit chain")
        previous = edit["operation_id"]
        operation_ids.add(previous)
        grid_carrier = edit.get("new_grid_name")
        entity_carrier = edit.get("new_entity_name")
        expected_names[edit["controller_id"]] = (
            "grid" if isinstance(grid_carrier, str) else
            "entity" if isinstance(entity_carrier, str) else "custom",
            grid_carrier if isinstance(grid_carrier, str) else
            entity_carrier if isinstance(entity_carrier, str) else edit["new_name"],
        )
    try:
        tree = ET.parse(sector)
    except ET.ParseError as error:
        raise SystemExit(f"invalid fixture sector XML: {error}")
    actual_names: dict[int, tuple[str, str, str]] = {}
    for entity in tree.getroot().iter("MyObjectBuilder_EntityBase"):
        if entity.get(XSI_TYPE) != "MyObjectBuilder_CubeGrid":
            continue
        grid_name = entity.findtext("DisplayName") or ""
        blocks = entity.find("CubeBlocks")
        for block in list(blocks) if blocks is not None else []:
            if block.get(XSI_TYPE) != "MyObjectBuilder_RemoteControl":
                continue
            entity_id = block.findtext("EntityId")
            if entity_id and entity_id.isdigit():
                actual_names[int(entity_id)] = (
                    grid_name,
                    block.findtext("Name") or "",
                    block.findtext("CustomName") or "",
                )
    for controller_id, expected in expected_names.items():
        actual = actual_names.get(controller_id, ("", "", ""))
        actual_value = actual[0] if expected[0] == "grid" else actual[1] if expected[0] == "entity" else actual[2]
        if actual_value != expected[1]:
            raise SystemExit(
                f"fixture edit outcome is not present for controller {controller_id}"
            )
    return {
        "fixture_edit_ledger_sha256": hashlib.sha256(ledger_raw).hexdigest(),
        "fixture_sector_sha256": hashlib.sha256(sector.read_bytes()).hexdigest(),
        "fixture_edit_count": len(edits),
    }


def create(instance: Path) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    world, imported = ensure_instance(instance)
    if (instance / LAB_FILE).exists():
        raise SystemExit("refusing to replace an existing lab authority contract")
    contract = {
        "schema": "vragecage.lab-authority.v1",
        "authority": "destructive-lab",
        "disposable": True,
        "instance": instance.name,
        "engine": imported["engine"],
        "source_tree_sha256": imported["tree_sha256"],
        "fixture_id": secrets.token_hex(16),
        "quarantine": "Quarantine",
    }
    atomic_write(instance / LAB_FILE, contract)
    quarantine = instance / "Quarantine"
    quarantine.mkdir(mode=0o700, exist_ok=True)
    os.chmod(quarantine, 0o700)
    validate(instance)
    return contract


def activate(instance: Path, expected_operation_id: str | None = None,
             expected_command_sha256: str | None = None,
             discovery_only: bool = False) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    # Main holds the shared lab-command lock. Resolve and validate the exact
    # pending bytes and all activation-time preflight prerequisites before
    # creating a run or consuming the queued command.
    pinned_raw = None
    pinned_command = None
    pending_raw = None
    pending_command = None
    pending_path = instance / PENDING_FILE
    pending_exists = pending_path.exists() or pending_path.is_symlink()
    if discovery_only and pending_exists:
        raise SystemExit("discovery-only bootstrap refuses to consume or coexist with a pending command")
    if expected_operation_id is not None or expected_command_sha256 is not None:
        if (not isinstance(expected_operation_id, str) or len(expected_operation_id) != 32
                or any(value not in "0123456789abcdef" for value in expected_operation_id)
                or not isinstance(expected_command_sha256, str) or len(expected_command_sha256) != 64
                or any(value not in "0123456789abcdef" for value in expected_command_sha256)):
            raise SystemExit("reviewed activation requires exact operation and command hash pins")
        if pending_path.is_symlink() or not pending_path.is_file() or pending_path.stat().st_size > 128 * 1024:
            raise SystemExit("reviewed pending command is absent or invalid; no run activated")
        pinned_raw = pending_path.read_bytes()
        if len(pinned_raw) > 128 * 1024 or hashlib.sha256(pinned_raw).hexdigest() != expected_command_sha256:
            raise SystemExit("pending command changed after review; no run activated")
        pinned_command = json.loads(pinned_raw)
        validate_lab_command(pinned_command, contract)
        if pinned_command.get("operation_id") != expected_operation_id:
            raise SystemExit("pending operation differs from the reviewed request; no run activated")
        pending_raw = pinned_raw
        pending_command = pinned_command
    elif pending_exists:
        if pending_path.is_symlink() or not pending_path.is_file() or pending_path.stat().st_size > 128 * 1024:
            raise SystemExit("pending command is absent or invalid; no run activated")
        pending_raw = pending_path.read_bytes()
        try:
            pending_command = json.loads(pending_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SystemExit(f"invalid pending command; no run activated: {error}")
        if not isinstance(pending_command, dict):
            raise SystemExit("pending command must be an object; no run activated")
        validate_lab_command(pending_command, contract)

    activation_snapshot = fixture_snapshot(instance, contract)
    if pending_command is not None and pending_command.get("action") in {"direct-maneuver", "preflight-only"}:
        require_direct_target_identity(instance, pending_command["controller_id"],
                                       pending_command["grid_id"],
                                       pending_command["depth_camera_id"], pending_command)
    if pending_command is not None and pending_command.get("action") == "drive-to-coordinate-depth":
        # This was previously checked only after the active-run record and
        # pending-command move, which could leave a partial activation.
        verify_voidwright_mod_staged(instance)

    quarantine_path = instance / str(contract["quarantine"])
    if quarantine_path.is_symlink():
        raise SystemExit("lab quarantine must not be a symbolic link")
    quarantine = quarantine_path.resolve()
    if quarantine.parent != instance:
        raise SystemExit("lab quarantine must be a direct, non-symlink instance directory")
    quarantine.mkdir(mode=0o700, exist_ok=True)
    run_id = secrets.token_hex(16)
    run_directory = quarantine / run_id
    run_directory.mkdir(mode=0o700)
    active = {
        "schema": "vragecage.lab-run.v1",
        "authority": "destructive-lab",
        "fixture_id": contract["fixture_id"],
        "instance": instance.name,
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "quarantine": str(run_directory),
        "contract_sha256": hashlib.sha256(canonical(contract)).hexdigest(),
    }
    if discovery_only:
        prior_discovery = instance / "World/Storage" / "agent-core-discovery-v1.json"
        baseline = "0" * 32
        if prior_discovery.is_file() and not prior_discovery.is_symlink():
            try:
                prior = json.loads(prior_discovery.read_text(encoding="utf-8"))
                value = prior.get("runId") if isinstance(prior, dict) else None
                if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value): baseline = value
            except (OSError, json.JSONDecodeError):
                pass
        active.update({"bootstrap_mode": "discovery-only", "bootstrap_nonce": secrets.token_hex(16),
                       "baseline_discovery_run_id": baseline})
    active.update(activation_snapshot)
    if pending_command is not None:
        command = pending_command
        command_path = run_directory / "command.json"
        if pinned_raw is not None:
            with command_path.open("xb") as stream:
                os.chmod(command_path, 0o600)
                stream.write(pinned_raw); stream.flush(); os.fsync(stream.fileno())
            pending_path.unlink()
        else:
            os.replace(pending_path, command_path)
            if command_path.read_bytes() != pending_raw:
                os.replace(command_path, pending_path)
                raise SystemExit("pending command changed during activation; no run activated")
        active["command"] = command
        active["command_sha256"] = hashlib.sha256(command_path.read_bytes()).hexdigest()
    atomic_write(instance / ACTIVE_FILE, active)
    atomic_write(run_directory / "run.json", active)
    result = dict(active)
    if not discovery_only:
        result["preflight_context"] = create_preflight_context(instance, run_id)
    return result


def validate_lab_command(command: dict[str, object], contract: dict[str, object]) -> None:
    if command.get("schema") != "vragecage.lab-command.v1":
        raise SystemExit("unsupported lab command")
    if command.get("fixture_id") != contract.get("fixture_id"):
        raise SystemExit("lab command belongs to another fixture")
    operation_id = command.get("operation_id")
    if (not isinstance(operation_id, str) or len(operation_id) != 32 or
            any(c not in "0123456789abcdef" for c in operation_id)):
        raise SystemExit("invalid lab command operation identity")
    if command.get("action") == "sensor-frame":
        camera_id = command.get("camera_id")
        width = command.get("width")
        height = command.get("height")
        range_meters = command.get("range_meters")
        fov_degrees = command.get("fov_degrees")
        if not isinstance(camera_id, int) or camera_id <= 0:
            raise SystemExit("invalid lab camera identity")
        if not isinstance(width, int) or not 4 <= width <= 64:
            raise SystemExit("lab sensor width must be 4-64 pixels")
        if not isinstance(height, int) or not 4 <= height <= 36 or width * height > 2304:
            raise SystemExit("lab sensor height must be 4-36 pixels and frame at most 2304 pixels")
        if not isinstance(range_meters, (int, float)) or not 1 <= float(range_meters) <= 250:
            raise SystemExit("lab sensor range must be 1-250 meters")
        if not isinstance(fov_degrees, (int, float)) or not 10 <= float(fov_degrees) <= 120:
            raise SystemExit("lab sensor FOV must be 10-120 degrees")
        return
    if command.get("action") == "preflight-only":
        expected = {"schema", "action", "fixture_id", "operation_id", "controller_id", "grid_id",
                    "depth_camera_id", "agent_core_id", "discovery_run_id", "discovery_tick",
                    "discovery_fingerprint", "target", "arrival_radius", "start_delay_ticks", "reverse",
                    "max_speed", "timeout_ticks"}
        if set(command) != expected:
            raise SystemExit("preflight-only command has missing or unexpected fields")
        for name in ("controller_id", "grid_id", "depth_camera_id"):
            value = command.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise SystemExit("preflight-only requires positive exact controller, grid, and camera IDs")
        if (not isinstance(command.get("agent_core_id"), int) or command["agent_core_id"] <= 0 or
                not isinstance(command.get("discovery_run_id"), str) or
                not re.fullmatch(r"[0-9a-f]{32}", command["discovery_run_id"]) or
                not isinstance(command.get("discovery_tick"), int) or command["discovery_tick"] < 0 or
                not isinstance(command.get("discovery_fingerprint"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", command["discovery_fingerprint"])):
            raise SystemExit("preflight-only requires a bound Agent Core discovery receipt")
        speed, ticks = command.get("max_speed"), command.get("timeout_ticks")
        if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not 0.1 <= float(speed) <= 0.8:
            raise SystemExit("preflight-only sensor speed bound must be 0.1-0.8 m/s")
        if not isinstance(ticks, int) or isinstance(ticks, bool) or not 60 <= ticks <= 7200:
            raise SystemExit("preflight-only timeout must be 60-7200 simulation ticks")
        if (command.get("target") != [0.0, 0.0, 0.0] or command.get("arrival_radius") != 0.5
                or command.get("start_delay_ticks") != 0 or command.get("reverse") is not False):
            raise SystemExit("preflight-only command has unexpected movement fields")
        return
    if command.get("action") == "rotate-stator":
        stator_id = command.get("stator_id")
        camera_id = command.get("camera_id")
        expected_name = command.get("expected_name")
        angle = command.get("desired_angle_degrees")
        speed = command.get("max_rpm")
        ticks = command.get("timeout_ticks")
        if not isinstance(stator_id, int) or stator_id <= 0:
            raise SystemExit("invalid lab stator identity")
        if not isinstance(camera_id, int) or camera_id <= 0:
            raise SystemExit("invalid lab stator camera identity")
        if (not isinstance(expected_name, str) or not expected_name or len(expected_name) > 128 or
                "\n" in expected_name or "\r" in expected_name):
            raise SystemExit("invalid expected stator name")
        if not isinstance(angle, (int, float)) or not math.isfinite(float(angle)) or not -360 <= float(angle) <= 360:
            raise SystemExit("lab stator angle must be -360 to 360 degrees")
        if not isinstance(speed, (int, float)) or not 0.1 <= float(speed) <= 0.5:
            raise SystemExit("lab stator speed must be 0.1-0.5 RPM")
        if not isinstance(ticks, int) or not 60 <= ticks <= 3600:
            raise SystemExit("lab stator timeout must be 60-3600 simulation ticks")
        return
    if command.get("action") == "play-recorded-route":
        controller_id = command.get("controller_id")
        recorder_id = command.get("recorder_id")
        duration_ticks = command.get("duration_ticks")
        if not isinstance(controller_id, int) or controller_id <= 0:
            raise SystemExit("invalid recorded-route controller identity")
        if not isinstance(recorder_id, int) or recorder_id <= 0:
            raise SystemExit("invalid recorded-route recorder identity")
        if not isinstance(duration_ticks, int) or not 60 <= duration_ticks <= 7200:
            raise SystemExit("recorded-route duration must be 60-7200 simulation ticks")
        return
    if command.get("action") == "drive-recorded-route":
        controller_id = command.get("controller_id")
        recorder_id = command.get("recorder_id")
        speed = command.get("max_speed")
        radius = command.get("arrival_radius")
        ticks = command.get("timeout_ticks")
        if not isinstance(controller_id, int) or controller_id <= 0:
            raise SystemExit("invalid recorded-route controller identity")
        if not isinstance(recorder_id, int) or recorder_id <= 0:
            raise SystemExit("invalid recorded-route recorder identity")
        if not isinstance(speed, (int, float)) or not 0.1 <= float(speed) <= 5:
            raise SystemExit("recorded-route speed must be 0.1-5 m/s")
        if not isinstance(radius, (int, float)) or not 0.5 <= float(radius) <= 10:
            raise SystemExit("recorded-route arrival radius must be 0.5-10 meters")
        if not isinstance(ticks, int) or not 60 <= ticks <= 7200:
            raise SystemExit("recorded-route timeout must be 60-7200 simulation ticks")
        return
    if command.get("action") == "direct-maneuver":
        expected_fields = {"schema", "action", "fixture_id", "operation_id", "controller_id", "grid_id",
                           "depth_camera_id", "agent_core_id", "discovery_run_id", "discovery_tick",
                           "discovery_fingerprint", "target", "arrival_radius", "start_delay_ticks", "max_speed",
                           "reverse", "timeout_ticks", "segments"}
        if set(command) != expected_fields:
            raise SystemExit("direct maneuver command has missing or unexpected fields")
        controller_id = command.get("controller_id")
        grid_id = command.get("grid_id")
        camera_id = command.get("depth_camera_id")
        segments = command.get("segments")
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in (controller_id, grid_id, camera_id)):
            raise SystemExit("direct maneuver requires positive exact controller, grid, and same-grid sensor IDs")
        if (not isinstance(command.get("agent_core_id"), int) or command["agent_core_id"] <= 0 or
                not isinstance(command.get("discovery_run_id"), str) or
                not re.fullmatch(r"[0-9a-f]{32}", command["discovery_run_id"]) or
                not isinstance(command.get("discovery_tick"), int) or command["discovery_tick"] < 0 or
                not isinstance(command.get("discovery_fingerprint"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", command["discovery_fingerprint"])):
            raise SystemExit("direct maneuver requires a bound Agent Core discovery receipt")
        if not isinstance(segments, list) or not 1 <= len(segments) <= 12:
            raise SystemExit("direct maneuver requires 1-12 bounded segments")
        total_ticks = 0
        forward_meters = 0.0
        reverse_meters = 0.0
        max_speed = 0.0
        for segment in segments:
            if not isinstance(segment, dict) or set(segment) != {"kind", "value", "max_speed", "timeout_ticks"}:
                raise SystemExit("direct maneuver segment fields are invalid")
            kind = segment.get("kind")
            value = segment.get("value")
            speed = segment.get("max_speed")
            ticks = segment.get("timeout_ticks")
            if (kind not in {"forward-distance", "reverse-distance", "steer-to-heading", "steer-angle", "stop", "stationary-hold"}
                    or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))
                    or not isinstance(speed, (int, float)) or isinstance(speed, bool) or not math.isfinite(float(speed))
                    or not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 1):
                raise SystemExit("direct maneuver segment contains an invalid value")
            value, speed = float(value), float(speed)
            total_ticks += ticks
            max_speed = max(max_speed, speed)
            if kind == "forward-distance":
                if not 0.25 <= value <= 25 or not 0.1 <= speed <= 0.8 or ticks > 3600:
                    raise SystemExit("direct forward segment exceeds the existing distance, sensor-speed, or runtime limits")
                forward_meters += value
            elif kind == "reverse-distance":
                if not 0.25 <= value <= 3 or not 0.1 <= speed <= 0.5 or ticks > 1800:
                    raise SystemExit("direct reverse segment exceeds the existing distance, speed, or runtime limits")
                reverse_meters += value
            elif kind == "steer-to-heading":
                if not 0.1 <= speed <= 0.25 or abs(value) > 90 or ticks > 1800:
                    raise SystemExit("direct heading segment exceeds its bounded creep-turn envelope")
            elif kind == "steer-angle":
                if speed != 0 or abs(value) > 40 or ticks > 1800:
                    raise SystemExit("direct steering angle exceeds the bounded wheel-angle envelope")
            else:
                if value != 0 or speed != 0 or ticks > 1800:
                    raise SystemExit("direct stop and hold segments require zero magnitude and speed")
        command_timeout = command.get("timeout_ticks")
        command_speed = command.get("max_speed")
        zero_motion_only = all(
            segment["kind"] in {"stop", "stationary-hold"}
            or (segment["kind"] == "steer-angle" and float(segment["value"]) == 0)
            for segment in segments
        )
        expected_command_speed = 0.1 if zero_motion_only else max_speed
        if (total_ticks + DIRECT_STOP_CONFIRMATION_TICKS > DIRECT_MAX_COMMAND_TICKS
                or forward_meters > 25 or reverse_meters > 3
                or not isinstance(command_timeout, int) or isinstance(command_timeout, bool)
                or command_timeout != total_ticks + DIRECT_STOP_CONFIRMATION_TICKS
                or command_timeout > DIRECT_MAX_COMMAND_TICKS
                or not isinstance(command_speed, (int, float)) or isinstance(command_speed, bool)
                or not math.isfinite(float(command_speed)) or float(command_speed) != expected_command_speed
                or command.get("target") != [0.0, 0.0, 0.0]
                or command.get("arrival_radius") != 0.5 or command.get("start_delay_ticks") != 0
                or command.get("reverse") is not False):
            raise SystemExit("direct maneuver exceeds the existing total distance or runtime limits")
        if segments[-1]["kind"] != "stationary-hold":
            raise SystemExit("direct maneuver must end in a telemetry-confirmed stationary hold")
        return
    if command.get("action") in ("drive-to-coordinate", "drive-to-coordinate-depth"):
        depth_mode = command.get("action") == "drive-to-coordinate-depth"
        controller_id = command.get("controller_id")
        target = command.get("target")
        zone = command.get("zone")
        speed = command.get("max_speed")
        radius = command.get("arrival_radius")
        offset_left = command.get("target_offset_left_m", 0)
        ticks = command.get("timeout_ticks")
        depth_camera_id = command.get("depth_camera_id")
        start_delay_ticks = command.get("start_delay_ticks", 0)
        if not isinstance(controller_id, int) or controller_id <= 0:
            raise SystemExit("invalid lab command controller identity")
        if (not isinstance(target, list) or len(target) != 3 or
                any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) or
                    abs(float(value)) > 1_000_000_000 for value in target)):
            raise SystemExit("invalid bounded lab navigation target")
        if not isinstance(zone, str) or not zone or len(zone) > 128 or "\n" in zone or "\r" in zone:
            raise SystemExit("invalid lab navigation zone label")
        if not isinstance(speed, (int, float)) or not 0.1 <= float(speed) <= 5:
            raise SystemExit("lab navigation speed must be 0.1-5 m/s")
        if not isinstance(radius, (int, float)) or not 0.5 <= float(radius) <= 10:
            raise SystemExit("lab navigation arrival radius must be 0.5-10 meters")
        if (not isinstance(offset_left, (int, float)) or
                not math.isfinite(float(offset_left)) or abs(float(offset_left)) > 10):
            raise SystemExit("lab navigation left offset must be -10 to 10 meters")
        if not isinstance(ticks, int) or not 60 <= ticks <= 7200:
            raise SystemExit("lab navigation timeout must be 60-7200 simulation ticks")
        if depth_mode:
            if (not isinstance(depth_camera_id, int) or isinstance(depth_camera_id, bool) or
                    depth_camera_id <= 0):
                raise SystemExit("depth-guided navigation requires a positive front-camera identity")
            if float(speed) > 0.8 or ticks > 1800:
                raise SystemExit("depth-guided navigation is limited to 0.8 m/s and 1800 ticks")
            if (not isinstance(start_delay_ticks, int) or isinstance(start_delay_ticks, bool)
                    or not 0 <= start_delay_ticks <= 3600):
                raise SystemExit("depth-guided start delay must be 0-3600 ticks")
        elif depth_camera_id is not None:
            raise SystemExit("depth camera identity is only valid for depth-guided navigation")
        elif start_delay_ticks != 0:
            raise SystemExit("start delay is only valid for depth-guided navigation")
        return
    if command.get("action") != "drive-distance":
        raise SystemExit("unsupported lab command")
    controller_id = command.get("controller_id")
    distance = command.get("distance_meters")
    speed = command.get("max_speed")
    ticks = command.get("timeout_ticks")
    reverse = command.get("reverse", False)
    if not isinstance(controller_id, int) or controller_id <= 0:
        raise SystemExit("invalid lab command controller identity")
    if not isinstance(reverse, bool):
        raise SystemExit("invalid lab drive direction")
    max_distance = 3 if reverse else 25
    if not isinstance(distance, (int, float)) or not 0.25 <= float(distance) <= max_distance:
        raise SystemExit(f"lab drive distance must be 0.25-{max_distance} meters")
    max_speed = 0.5 if reverse else 5
    if not isinstance(speed, (int, float)) or not 0.1 <= float(speed) <= max_speed:
        raise SystemExit(f"lab drive speed must be 0.1-{max_speed} m/s")
    max_ticks = 1800 if reverse else 3600
    if not isinstance(ticks, int) or not 60 <= ticks <= max_ticks:
        raise SystemExit(f"lab drive timeout must be 60-{max_ticks} simulation ticks")


def plan_drive(instance: Path, controller_id: int, distance: float, speed: float,
               ticks: int, reverse: bool = False) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    command = {
        "schema": "vragecage.lab-command.v1",
        "action": "drive-distance",
        "fixture_id": contract["fixture_id"],
        "operation_id": secrets.token_hex(16),
        "controller_id": controller_id,
        "distance_meters": distance,
        "reverse": reverse,
        "max_speed": speed,
        "timeout_ticks": ticks,
    }
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command))
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def plan_direct_maneuver(instance: Path, controller_id: int | None, grid_id: int | None,
                         camera_id: int | None, segments: list[dict[str, object]]) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    binding = require_direct_target_identity(instance, controller_id, grid_id, camera_id)
    controller_id, grid_id, camera_id = binding["controller_id"], binding["grid_id"], binding["camera_id"]
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    timeout_ticks = (sum(int(segment.get("timeout_ticks", 0)) for segment in segments if isinstance(segment, dict))
                     + DIRECT_STOP_CONFIRMATION_TICKS)
    maximum_speed = max((float(segment.get("max_speed", 0)) for segment in segments if isinstance(segment, dict)), default=0.0)
    zero_motion_only = all(
        segment.get("kind") in {"stop", "stationary-hold"}
        or (segment.get("kind") == "steer-angle" and float(segment.get("value", 0)) == 0)
        for segment in segments if isinstance(segment, dict)
    )
    if zero_motion_only:
        # The host runtime requires a nonzero command-level safety envelope. This
        # fixed floor is not an actuator request; every segment remains zero-speed.
        maximum_speed = 0.1
    command = {
        "schema": "vragecage.lab-command.v1", "action": "direct-maneuver",
        "fixture_id": contract["fixture_id"], "operation_id": secrets.token_hex(16),
        "controller_id": controller_id, "grid_id": grid_id, "depth_camera_id": camera_id,
        "agent_core_id": binding["core_id"], "discovery_run_id": binding["run_id"],
        "discovery_tick": binding["tick"], "discovery_fingerprint": binding["fingerprint"],
        "target": [0.0, 0.0, 0.0], "arrival_radius": 0.5, "start_delay_ticks": 0,
        "max_speed": maximum_speed, "reverse": False,
        "timeout_ticks": timeout_ticks, "segments": segments,
    }
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command)); stream.flush(); os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def plan_preflight_only(instance: Path, controller_id: int | None, grid_id: int | None,
                        camera_id: int | None, max_speed: float = 0.8,
                        timeout_ticks: int = 300) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    binding = require_direct_target_identity(instance, controller_id, grid_id, camera_id)
    controller_id, grid_id, camera_id = binding["controller_id"], binding["grid_id"], binding["camera_id"]
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    command = {
        "schema": "vragecage.lab-command.v1", "action": "preflight-only",
        "fixture_id": contract["fixture_id"], "operation_id": secrets.token_hex(16),
        "controller_id": controller_id, "grid_id": grid_id, "depth_camera_id": camera_id,
        "agent_core_id": binding["core_id"], "discovery_run_id": binding["run_id"],
        "discovery_tick": binding["tick"], "discovery_fingerprint": binding["fingerprint"],
        "target": [0.0, 0.0, 0.0], "arrival_radius": 0.5,
        "start_delay_ticks": 0, "reverse": False,
        "max_speed": max_speed, "timeout_ticks": timeout_ticks,
    }
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command))
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def plan_sensor_frame(instance: Path, camera_id: int, width: int, height: int,
                      range_meters: float, fov_degrees: float) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    command = {
        "schema": "vragecage.lab-command.v1",
        "action": "sensor-frame",
        "fixture_id": contract["fixture_id"],
        "operation_id": secrets.token_hex(16),
        "camera_id": camera_id,
        "width": width,
        "height": height,
        "range_meters": range_meters,
        "fov_degrees": fov_degrees,
    }
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command))
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def plan_rotate_stator(instance: Path, stator_id: int, camera_id: int, expected_name: str,
                       desired_angle_degrees: float, max_rpm: float,
                       timeout_ticks: int) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    command = {
        "schema": "vragecage.lab-command.v1",
        "action": "rotate-stator",
        "fixture_id": contract["fixture_id"],
        "operation_id": secrets.token_hex(16),
        "stator_id": stator_id,
        "camera_id": camera_id,
        "expected_name": expected_name,
        "desired_angle_degrees": desired_angle_degrees,
        "max_rpm": max_rpm,
        "timeout_ticks": timeout_ticks,
    }
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command))
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def plan_recorded_route(instance: Path, controller_id: int, recorder_id: int,
                        duration_ticks: int) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    command = {
        "schema": "vragecage.lab-command.v1", "action": "play-recorded-route",
        "fixture_id": contract["fixture_id"], "operation_id": secrets.token_hex(16),
        "controller_id": controller_id, "recorder_id": recorder_id,
        "duration_ticks": duration_ticks,
    }
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command)); stream.flush(); os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def plan_follow_recorder(instance: Path, controller_id: int, recorder_id: int,
                         speed: float, radius: float, ticks: int) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    command = {
        "schema": "vragecage.lab-command.v1", "action": "drive-recorded-route",
        "fixture_id": contract["fixture_id"], "operation_id": secrets.token_hex(16),
        "controller_id": controller_id, "recorder_id": recorder_id,
        "max_speed": speed, "arrival_radius": radius, "timeout_ticks": ticks,
    }
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command)); stream.flush(); os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def plan_drive_to(instance: Path, controller_id: int, zone: str, target: list[float],
                  speed: float, radius: float, ticks: int,
                  offset_left: float = 0,
                  depth_camera_id: int | None = None,
                  start_delay_ticks: int = 0) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    pending = instance / PENDING_FILE
    if pending.exists():
        raise SystemExit("a lab command is already pending")
    command = {
        "schema": "vragecage.lab-command.v1",
        "action": "drive-to-coordinate-depth" if depth_camera_id is not None else "drive-to-coordinate",
        "fixture_id": contract["fixture_id"], "operation_id": secrets.token_hex(16),
        "controller_id": controller_id, "zone": zone, "target": target,
        "target_offset_left_m": offset_left,
        "max_speed": speed, "arrival_radius": radius, "timeout_ticks": ticks,
        "start_delay_ticks": start_delay_ticks,
    }
    if depth_camera_id is not None:
        command["depth_camera_id"] = depth_camera_id
    validate_lab_command(command, contract)
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("a lab command is already pending")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(command)); stream.flush(); os.fsync(stream.fileno())
    except Exception:
        pending.unlink(missing_ok=True)
        raise
    return command


def verify_runtime(instance: Path, run_id: str) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("lab instance must not be a symbolic link")
    instance = instance.resolve()
    contract, _ = validate(instance)
    active = load_object(instance / ACTIVE_FILE)
    if active.get("run_id") != run_id or active.get("fixture_id") != contract.get("fixture_id"):
        raise SystemExit("active lab run does not match launch authority")
    quarantine_path = Path(str(active.get("quarantine", "")))
    if quarantine_path.is_symlink():
        raise SystemExit("active lab quarantine must not be a symbolic link")
    quarantine = quarantine_path.resolve()
    expected_parent = (instance / str(contract["quarantine"])).resolve()
    if quarantine.parent != expected_parent or quarantine.name != run_id or not quarantine.is_dir():
        raise SystemExit("active lab run has an invalid quarantine directory")
    return active


def create_preflight_context(instance: Path, run_id: str) -> dict[str, object]:
    """Bind an immutable, hash-checked preflight context to this exact lab run."""
    active = verify_runtime(instance, run_id)
    instance = instance.resolve()
    contract, imported = validate(instance)
    contract_path = instance / LAB_FILE
    import_path = instance / ".vragecage-world-import.json"
    active_path = instance / ACTIVE_FILE
    run_directory = Path(str(active["quarantine"])).resolve()
    run_receipt_path = run_directory / "run.json"
    command = active.get("command")
    command_path = run_directory / "command.json" if isinstance(command, dict) else None
    if (active.get("schema") != "vragecage.lab-run.v1"
            or active.get("authority") != "destructive-lab"
            or active.get("instance") != instance.name
            or active.get("contract_sha256") != hashlib.sha256(contract_path.read_bytes()).hexdigest()
            or imported.get("tree_sha256") != contract.get("source_tree_sha256")):
        raise SystemExit("preflight context requires a fully matched lab authority, import, and active run")
    if not run_receipt_path.is_file() or load_object(run_receipt_path).get("run_id") != run_id:
        raise SystemExit("preflight context requires the matching durable run receipt")
    sector_path = instance / "World/SANDBOX_0_0_0_.sbs"
    sector_sha256 = hashlib.sha256(sector_path.read_bytes()).hexdigest()
    if active.get("fixture_sector_sha256") != sector_sha256:
        raise SystemExit("fixture sector changed after activation; preflight context is invalid")
    mods_path = instance / "Mods"
    if mods_path.is_symlink():
        raise SystemExit("staged mod directory must not be a symbolic link")
    mods_tree_sha256 = tree_sha256(mods_path) if mods_path.is_dir() else ""
    command_sha256 = ""
    operation_id = ""
    action = ""
    controller_id = ""
    depth_camera_id = ""
    grid_id = ""
    agent_core_id = "0"
    discovery_run_id = ""
    discovery_tick = "0"
    discovery_fingerprint = ""
    maneuver_segments = ""
    target = ["", "", ""]
    max_speed = ""
    timeout_ticks = ""
    arrival_radius = ""
    start_delay_ticks = ""
    reverse = "false"
    if command_path is not None:
        if (command_path.is_symlink() or not command_path.is_file()
                or hashlib.sha256(command_path.read_bytes()).hexdigest() != active.get("command_sha256")):
            raise SystemExit("preflight context command does not match the active command digest")
        saved_command = load_object(command_path)
        validate_lab_command(saved_command, contract)
        if saved_command != command:
            raise SystemExit("preflight context command differs from the active run")
        command_sha256 = hashlib.sha256(command_path.read_bytes()).hexdigest()
        operation_id = str(command.get("operation_id", ""))
        action = str(command.get("action", ""))
        controller_id = str(command.get("controller_id", ""))
        depth_camera_id = str(command.get("depth_camera_id", ""))
        grid_id = str(command.get("grid_id", ""))
        if action in ("direct-maneuver", "preflight-only"):
            binding = require_direct_target_identity(instance, command.get("controller_id"),
                command.get("grid_id"), command.get("depth_camera_id"), command)
            agent_core_id = str(binding["core_id"])
            discovery_run_id = str(binding["run_id"])
            discovery_tick = str(binding["tick"])
            discovery_fingerprint = str(binding["fingerprint"])
        if action == "direct-maneuver":
            maneuver_segments = ";".join(
                ",".join((
                    {"forward-distance": "F", "reverse-distance": "R", "steer-to-heading": "H",
                     "steer-angle": "A", "stop": "S", "stationary-hold": "W"}[str(segment["kind"])],
                    format(float(segment["value"]), ".17g"),
                    format(float(segment["max_speed"]), ".17g"),
                    str(int(segment["timeout_ticks"])),
                )) for segment in command["segments"]
            )
        target = [str(value) for value in (command.get("target") or ["", "", ""])]
        max_speed = str(command.get("max_speed", ""))
        timeout_ticks = str(command.get("timeout_ticks", ""))
        arrival_radius = str(command.get("arrival_radius", ""))
        start_delay_ticks = str(command.get("start_delay_ticks", 0))
        reverse = "true" if command.get("reverse") is True else "false"
        if action in ("drive-to-coordinate-depth", "direct-maneuver", "preflight-only"):
            mods_path, mods_tree_sha256 = verify_voidwright_mod_staged(instance)
    ledger_path = instance / ".vragecage-fixture-edits.json"
    if ledger_path.is_symlink():
        raise SystemExit("preflight context fixture edit ledger must not be a symbolic link")
    ledger_hash = hashlib.sha256(ledger_path.read_bytes()).hexdigest() if ledger_path.is_file() else ""
    fields = {
        "schema": "vragecage.preflight-context.v1",
        "run_id": run_id,
        "fixture_id": str(contract["fixture_id"]),
        "instance": instance.name,
        "operation_id": operation_id,
        "action": action,
        "controller_id": controller_id,
        "depth_camera_id": depth_camera_id,
        "grid_id": grid_id,
        "agent_core_id": agent_core_id,
        "discovery_run_id": discovery_run_id,
        "discovery_tick": discovery_tick,
        "discovery_fingerprint": discovery_fingerprint,
        "maneuver_segments": maneuver_segments,
        "target_x": target[0],
        "target_y": target[1],
        "target_z": target[2],
        "max_speed": max_speed,
        "timeout_ticks": timeout_ticks,
        "arrival_radius": arrival_radius,
        "start_delay_ticks": start_delay_ticks,
        "reverse": reverse,
        "source_tree_sha256": str(contract["source_tree_sha256"]),
        "fixture_sector_path": str(sector_path.resolve()),
        "fixture_sector_sha256": sector_sha256,
        "mods_path": str(mods_path.resolve()) if mods_path.is_dir() else "",
        "mods_tree_sha256": mods_tree_sha256,
        "contract_path": str(contract_path.resolve()),
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "import_receipt_path": str(import_path.resolve()),
        "import_receipt_sha256": hashlib.sha256(import_path.read_bytes()).hexdigest(),
        "active_run_path": str(active_path.resolve()),
        "active_run_sha256": hashlib.sha256(active_path.read_bytes()).hexdigest(),
        "run_receipt_path": str(run_receipt_path.resolve()),
        "run_receipt_sha256": hashlib.sha256(run_receipt_path.read_bytes()).hexdigest(),
        "command_path": str(command_path.resolve()) if command_path is not None else "",
        "command_sha256": command_sha256,
        "edit_ledger_path": str(ledger_path.resolve()) if ledger_hash else "",
        "edit_ledger_sha256": ledger_hash,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    for key, value in fields.items():
        if not key or any(character in str(value) for character in "\r\n\t"):
            raise SystemExit("preflight context contains an unsafe field")
    payload = "".join(f"{key}={value}\n" for key, value in fields.items()).encode()
    context_path = run_directory / "preflight-context-v1.txt"
    temporary = context_path.with_name(context_path.name + ".tmp")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    os.replace(temporary, context_path)
    return {
        "path": str(context_path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "fixture_id": fields["fixture_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "activate"):
        command = subparsers.add_parser(name)
        command.add_argument("instance")
        if name == "activate":
            command.add_argument("--expected-operation-id")
            command.add_argument("--expected-command-sha256")
            command.add_argument("--discovery-only", action="store_true")
    verify = subparsers.add_parser("verify-runtime")
    verify.add_argument("instance")
    verify.add_argument("run_id")
    context = subparsers.add_parser("preflight-context")
    context.add_argument("instance")
    context.add_argument("run_id")
    stage_two = subparsers.add_parser("activate-preflight-from-discovery")
    stage_two.add_argument("instance")
    stage_two.add_argument("--wait-seconds", type=int, default=45)
    zero_hold = subparsers.add_parser("activate-zero-hold-from-discovery")
    zero_hold.add_argument("instance")
    zero_hold.add_argument("--wait-seconds", type=int, default=45)
    drive = subparsers.add_parser("plan-drive")
    drive.add_argument("instance")
    drive.add_argument("controller_id", type=int)
    drive.add_argument("--distance", type=float, required=True)
    drive.add_argument("--max-speed", type=float, required=True)
    drive.add_argument("--timeout-ticks", type=int, required=True)
    drive.add_argument("--reverse", action="store_true")
    maneuver = subparsers.add_parser("plan-direct-maneuver")
    maneuver.add_argument("instance")
    maneuver.add_argument("controller_id", type=int, nargs="?")
    maneuver.add_argument("grid_id", type=int, nargs="?")
    maneuver.add_argument("camera_id", type=int, nargs="?")
    maneuver.add_argument("--segment", action="append", required=True,
                          help="kind:value:max-speed:timeout-ticks; repeat in execution order")
    preflight = subparsers.add_parser("plan-preflight-only")
    preflight.add_argument("instance")
    preflight.add_argument("controller_id", type=int, nargs="?")
    preflight.add_argument("grid_id", type=int, nargs="?")
    preflight.add_argument("camera_id", type=int, nargs="?")
    preflight.add_argument("--max-speed", type=float, default=0.8)
    preflight.add_argument("--timeout-ticks", type=int, default=300)
    sensor = subparsers.add_parser("plan-sensor-frame")
    sensor.add_argument("instance")
    sensor.add_argument("camera_id", type=int)
    sensor.add_argument("--width", type=int, required=True)
    sensor.add_argument("--height", type=int, required=True)
    sensor.add_argument("--range", dest="range_meters", type=float, required=True)
    sensor.add_argument("--fov", dest="fov_degrees", type=float, required=True)
    rotate = subparsers.add_parser("plan-rotate-stator")
    rotate.add_argument("instance")
    rotate.add_argument("stator_id", type=int)
    rotate.add_argument("camera_id", type=int)
    rotate.add_argument("expected_name")
    rotate.add_argument("desired_angle_degrees", type=float)
    rotate.add_argument("--max-rpm", type=float, required=True)
    rotate.add_argument("--timeout-ticks", type=int, required=True)
    navigate = subparsers.add_parser("plan-drive-to")
    navigate.add_argument("instance")
    navigate.add_argument("controller_id", type=int)
    navigate.add_argument("zone")
    navigate.add_argument("x", type=float)
    navigate.add_argument("y", type=float)
    navigate.add_argument("z", type=float)
    navigate.add_argument("--max-speed", type=float, required=True)
    navigate.add_argument("--arrival-radius", type=float, required=True)
    navigate.add_argument("--timeout-ticks", type=int, required=True)
    navigate.add_argument("--offset-left", type=float, default=0,
                          help="offset the named target left of the controller's initial heading, -10 to 10 meters")
    navigate.add_argument("--depth-camera-id", type=int,
                          help="enable low-speed front-camera depth steering for this disposable trial")
    navigate.add_argument("--start-delay-ticks", type=int, default=0,
                          help="hold the depth-guided rover stopped before movement, up to 3600 simulation ticks")
    recorded = subparsers.add_parser("plan-recorded-route")
    recorded.add_argument("instance")
    recorded.add_argument("controller_id", type=int)
    recorded.add_argument("recorder_id", type=int)
    recorded.add_argument("--duration-ticks", type=int, required=True)
    follow = subparsers.add_parser("plan-follow-recorder")
    follow.add_argument("instance")
    follow.add_argument("controller_id", type=int)
    follow.add_argument("recorder_id", type=int)
    follow.add_argument("--max-speed", type=float, required=True)
    follow.add_argument("--arrival-radius", type=float, required=True)
    follow.add_argument("--timeout-ticks", type=int, required=True)
    args = parser.parse_args()
    instance = Path(args.instance).expanduser()
    if not instance.is_dir():
        raise SystemExit("lab instance directory does not exist")
    lock_path = instance / LOCK_FILE
    with lock_path.open("a+b") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "create":
            result = create(instance)
        elif args.command == "activate":
            result = activate(instance, args.expected_operation_id, args.expected_command_sha256,
                              args.discovery_only)
        elif args.command == "verify-runtime":
            result = verify_runtime(instance, args.run_id)
        elif args.command == "preflight-context":
            result = create_preflight_context(instance, args.run_id)
        elif args.command == "activate-preflight-from-discovery":
            result = activate_preflight_from_discovery(instance, args.wait_seconds)
        elif args.command == "activate-zero-hold-from-discovery":
            result = activate_zero_hold_from_discovery(instance, args.wait_seconds)
        elif args.command == "plan-drive":
            result = plan_drive(instance, args.controller_id, args.distance, args.max_speed,
                                args.timeout_ticks, args.reverse)
        elif args.command == "plan-direct-maneuver":
            segments = []
            for encoded in args.segment:
                fields = encoded.split(":")
                if len(fields) != 4:
                    raise SystemExit("direct maneuver segment must be kind:value:max-speed:timeout-ticks")
                try:
                    segments.append({"kind": fields[0], "value": float(fields[1]),
                                     "max_speed": float(fields[2]), "timeout_ticks": int(fields[3])})
                except ValueError as error:
                    raise SystemExit("direct maneuver segment contains an invalid number") from error
            result = plan_direct_maneuver(instance, args.controller_id, args.grid_id,
                                          args.camera_id, segments)
        elif args.command == "plan-preflight-only":
            result = plan_preflight_only(instance, args.controller_id, args.grid_id,
                                         args.camera_id, args.max_speed, args.timeout_ticks)
        elif args.command == "plan-sensor-frame":
            result = plan_sensor_frame(instance, args.camera_id, args.width, args.height,
                                       args.range_meters, args.fov_degrees)
        elif args.command == "plan-rotate-stator":
            result = plan_rotate_stator(instance, args.stator_id, args.camera_id, args.expected_name,
                                        args.desired_angle_degrees, args.max_rpm, args.timeout_ticks)
        elif args.command == "plan-recorded-route":
            result = plan_recorded_route(instance, args.controller_id, args.recorder_id,
                                         args.duration_ticks)
        elif args.command == "plan-follow-recorder":
            result = plan_follow_recorder(instance, args.controller_id, args.recorder_id,
                                          args.max_speed, args.arrival_radius, args.timeout_ticks)
        else:
            result = plan_drive_to(instance, args.controller_id, args.zone,
                                   [args.x, args.y, args.z], args.max_speed,
                                   args.arrival_radius, args.timeout_ticks, args.offset_left,
                                   args.depth_camera_id, args.start_delay_ticks)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
