#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from local_vehicle_test_packages import (
    INSTANCE as LOCAL_TEST_INSTANCE,
    SCRIPT_REQUIRED_MODS,
    loaded_marker_valid,
    verify_run,
)


def latest(paths: list[Path], description: str) -> Path:
    if not paths:
        raise SystemExit(f"no {description} log found")
    return max(paths, key=lambda path: path.stat().st_mtime_ns)


def tree_hash(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else None


def file_hash(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique_bounded(lines: list[str], limit: int = 16) -> tuple[list[str], int]:
    # Timestamps make repeated engine diagnostics textually unique. Receipts
    # bind the full log by hash, so keep only the stable payload after `->`.
    compact = (line.split("->", 1)[-1].strip() for line in lines if line.strip())
    unique = list(dict.fromkeys(compact))
    return unique[:limit], max(0, len(unique) - limit)


def operation_event_is_authoritative(command_action: str | None, event_type: str) -> bool:
    # A preflight-only command is a one-shot observation. Its host's later
    # handshake deadline is not a second depth-controller operation result.
    return command_action != "preflight-only" or event_type == "preflight-only"


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: magnetar-receipt.py INSTANCE_DIRECTORY")
    instance = Path(sys.argv[1]).resolve()
    game_log = latest(list(instance.glob("SpaceEngineersDedicated_*.log")), "dedicated-server")
    info_log = latest(list((instance / "MagnetarConfig").glob("info*.log")), "Magnetar")
    game_raw = game_log.read_bytes()
    info_raw = info_log.read_bytes()
    # A new lab activation can precede creation of the new game's log. Do not
    # borrow readiness, plugin markers, or shutdown evidence from the old run.
    log_freshness = {"game": True, "magnetar": True}
    activation_path = instance / ".vragecage-active-run.json"
    if activation_path.exists() or (instance / ".vragecage-lab.json").exists():
        try:
            activation = json.loads(activation_path.read_text())
            started = datetime.fromisoformat(activation["started_at"])
            if started.tzinfo is None:
                raise ValueError("activation timestamp must include timezone")
            started_ns = int(started.timestamp() * 1_000_000_000)
            log_freshness = {
                "game": game_log.stat().st_mtime_ns >= started_ns,
                "magnetar": info_log.stat().st_mtime_ns >= started_ns,
            }
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            log_freshness = {"game": False, "magnetar": False}
    game = game_raw.decode("utf-8", errors="replace") if log_freshness["game"] else ""
    info = info_raw.decode("utf-8", errors="replace") if log_freshness["magnetar"] else ""

    stage_path = instance / ".vragecage-mod-stage.json"
    loaded_stage = json.loads(stage_path.read_text()) if stage_path.is_file() else None
    stage = loaded_stage if isinstance(loaded_stage, dict) else None
    plugin_stage_path = instance / ".vragecage-plugin-stage.json"
    loaded_plugin_stage = json.loads(plugin_stage_path.read_text()) if plugin_stage_path.is_file() else None
    plugin_stage = loaded_plugin_stage if isinstance(loaded_plugin_stage, dict) else None
    plugin_path = plugin_stage.get("path") if plugin_stage else None
    actual_plugin_hash = tree_hash(Path(plugin_path)) if isinstance(plugin_path, str) else None
    plugin_unchanged = bool(plugin_stage and actual_plugin_hash == plugin_stage.get("tree_sha256"))
    workshop_id = str(stage.get("synthetic_workshop_id")) if stage and stage.get("synthetic_workshop_id") else None
    staged_path = stage.get("path") if stage else None
    actual_tree_hash = tree_hash(Path(staged_path)) if isinstance(staged_path, str) else None
    engine_fatal_patterns = (
        r"Exception while loading world",
        r"Fatal error",
        r"An error occured while loading client mods",
    )
    all_lines = (game + "\n" + info).splitlines()
    mod_name = stage.get("name") if stage and isinstance(stage.get("name"), str) else None
    target_patterns = (
        rf"MOD_ERROR:\s*{re.escape(mod_name)}(?:\s|$)",
        rf"Compilation of .*{re.escape(mod_name)}.* failed",
        rf"Failed to build\s+{re.escape(mod_name)}(?:\s|$)",
    ) if mod_name else ()
    plugin_name = plugin_stage.get("friendly_name") if plugin_stage and isinstance(plugin_stage.get("friendly_name"), str) else None
    plugin_patterns = (rf"Failed to build\s+{re.escape(plugin_name)}(?:\s|$)",) if plugin_name else ()
    target_fatal, target_fatal_omitted = unique_bounded([
        line for line in all_lines
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in (*engine_fatal_patterns, *target_patterns, *plugin_patterns))
    ])
    environment_warnings, environment_warnings_omitted = unique_bounded([
        line for line in all_lines
        if re.search(r"MOD_ERROR|Compilation of .* failed", line, re.IGNORECASE)
        and not any(re.search(pattern, line, re.IGNORECASE) for pattern in (*target_patterns, *plugin_patterns))
    ])
    definitions_loaded = bool(workshop_id and f"Loading client mod definitions for {workshop_id}" in info)
    scripts_loaded = bool(workshop_id and f"Loading client mod scripts for {workshop_id}" in info)
    stage_unchanged = bool(stage and actual_tree_hash == stage.get("tree_sha256"))
    contract = stage.get("contract") if stage else None
    expected_markers = contract.get("expected_log_markers", []) if isinstance(contract, dict) else []
    if not isinstance(expected_markers, list) or any(not isinstance(marker, str) for marker in expected_markers):
        expected_markers = []
    observed_expected_markers = {
        marker: marker in game or marker in info for marker in expected_markers
    }
    plugin_contract = plugin_stage.get("contract") if plugin_stage else None
    plugin_markers = plugin_contract.get("expected_log_markers", []) if isinstance(plugin_contract, dict) else []
    if not isinstance(plugin_markers, list) or any(not isinstance(marker, str) for marker in plugin_markers):
        plugin_markers = []
    observed_plugin_markers = {marker: marker in game or marker in info for marker in plugin_markers}
    published_mod_packages = None
    published_mod_packages_valid = None
    published_mod_package_error = None
    if instance.name == LOCAL_TEST_INSTANCE:
        try:
            package_check = verify_run(instance, instance.parent.parent)
            published_mod_packages = []
            for package in package_check["published_mod_packages"]:
                mod_id = package["published_file_id"]
                definitions_marker = f"Loading client mod definitions for {mod_id}" in info
                scripts_marker = f"Loading client mod scripts for {mod_id}" in info
                required_marker = "scripts" if mod_id in SCRIPT_REQUIRED_MODS else "definitions"
                published_mod_packages.append({
                    **package, "cache_hash_matches": True,
                    "definitions_loaded_marker": definitions_marker,
                    "scripts_loaded_marker": scripts_marker,
                    "required_load_marker": required_marker,
                    "loaded_marker_seen": loaded_marker_valid(
                        mod_id, definitions_marker, scripts_marker),
                })
            published_mod_packages_valid = all(
                item["cache_hash_matches"] and item["loaded_marker_seen"]
                for item in published_mod_packages
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            published_mod_package_error = str(error)
            published_mod_packages_valid = False
    observation_prefix = contract.get("observation_prefix") if isinstance(contract, dict) else None
    observations: list[str] = []
    observations_omitted = 0
    if isinstance(observation_prefix, str) and observation_prefix:
        observations, observations_omitted = unique_bounded([
            line.split(observation_prefix, 1)[1].strip()
            for line in all_lines
            if observation_prefix in line
        ], limit=32)
    lab_path = instance / ".vragecage-lab.json"
    active_path = instance / ".vragecage-active-run.json"
    lab_present = lab_path.exists() or active_path.exists()
    lab_valid = False
    lab_authority = None
    lab_run_id = None
    quarantine_run = None
    lab_contract_sha256 = None
    lab_operation_id = None
    fixture_edit_ledger_sha256 = None
    fixture_edits_valid = None
    fixture_edit_count = 0
    fixture_sector_sha256_at_activation = None
    fixture_sector_sha256_current = None
    fixture_sector_changed_by_engine = None
    lab_command_action = None
    lab_quarantine = None
    if lab_present and lab_path.is_file() and active_path.is_file():
        lab_raw = lab_path.read_bytes()
        try:
            lab = json.loads(lab_raw)
            active = json.loads(active_path.read_text())
            imported = json.loads((instance / ".vragecage-world-import.json").read_text())
        except (json.JSONDecodeError, OSError):
            lab = active = imported = None
        if isinstance(lab, dict) and isinstance(active, dict) and isinstance(imported, dict):
            lab_contract_sha256 = hashlib.sha256(lab_raw).hexdigest()
            run_id = active.get("run_id")
            quarantine_path = Path(str(active.get("quarantine", "")))
            quarantine = quarantine_path.resolve()
            expected_parent = (instance / "Quarantine").resolve()
            lab_valid = bool(
                lab.get("schema") == "vragecage.lab-authority.v1"
                and lab.get("authority") == "destructive-lab"
                and lab.get("disposable") is True
                and lab.get("instance") == instance.name
                and lab.get("engine") == "magnetar"
                and lab.get("source_tree_sha256") == imported.get("tree_sha256")
                and imported.get("instance") == instance.name
                and imported.get("engine") == "magnetar"
                and active.get("schema") == "vragecage.lab-run.v1"
                and active.get("authority") == "destructive-lab"
                and active.get("instance") == instance.name
                and active.get("fixture_id") == lab.get("fixture_id")
                and active.get("contract_sha256") == lab_contract_sha256
                and isinstance(run_id, str)
                and len(run_id) == 32
                and all(character in "0123456789abcdef" for character in run_id)
                and not quarantine_path.is_symlink()
                and quarantine.resolve().parent == expected_parent
                and quarantine.name == run_id
                and quarantine.is_dir()
            )
            if lab_valid:
                lab_quarantine = quarantine
                lab_authority = "destructive-lab"
                lab_run_id = run_id
                quarantine_run = f"Quarantine/{run_id}"
                command = active.get("command")
                lab_command_action = command.get("action") if isinstance(command, dict) else None
                operation_id = command.get("operation_id") if isinstance(command, dict) else None
                if (isinstance(operation_id, str) and len(operation_id) == 32
                        and all(character in "0123456789abcdef" for character in operation_id)):
                    lab_operation_id = operation_id
                ledger_path = instance / ".vragecage-fixture-edits.json"
                if ledger_path.exists():
                    fixture_edit_ledger_sha256 = file_hash(ledger_path)
                    try:
                        ledger = json.loads(ledger_path.read_text())
                    except (OSError, json.JSONDecodeError):
                        ledger = None
                    edits = ledger.get("edits") if isinstance(ledger, dict) else None
                    fixture_edit_count = len(edits) if isinstance(edits, list) else 0
                    chain_valid = isinstance(edits, list) and bool(edits)
                    previous_operation_id = None
                    seen_operation_ids: set[str] = set()
                    if chain_valid:
                        for edit in edits:
                            operation = edit.get("operation_id") if isinstance(edit, dict) else None
                            if (not isinstance(edit, dict)
                                    or edit.get("action") != "voidwright-opt-in"
                                    or edit.get("previous_operation_id") != previous_operation_id
                                    or not isinstance(operation, str) or len(operation) != 32
                                    or any(character not in "0123456789abcdef" for character in operation)
                                    or operation in seen_operation_ids
                                    or not isinstance(edit.get("sector_sha256_before"), str)
                                    or not isinstance(edit.get("sector_sha256_after"), str)):
                                chain_valid = False
                                break
                            seen_operation_ids.add(operation)
                            previous_operation_id = operation
                    fixture_sector_sha256_at_activation = active.get("fixture_sector_sha256")
                    fixture_sector_sha256_current = file_hash(instance / "World/SANDBOX_0_0_0_.sbs")
                    fixture_sector_changed_by_engine = bool(
                        fixture_sector_sha256_at_activation and fixture_sector_sha256_current
                        and fixture_sector_sha256_at_activation != fixture_sector_sha256_current
                    )
                    fixture_edits_valid = bool(
                        fixture_edit_ledger_sha256
                        and isinstance(ledger, dict)
                        and ledger.get("schema") == "vragecage.fixture-edits.v1"
                        and ledger.get("instance") == instance.name
                        and ledger.get("fixture_id") == lab.get("fixture_id")
                        and ledger.get("source_tree_sha256") == lab.get("source_tree_sha256")
                        and chain_valid and edits
                        and active.get("fixture_edit_ledger_sha256") == fixture_edit_ledger_sha256
                        and active.get("fixture_edit_count") == len(edits)
                        and isinstance(fixture_sector_sha256_at_activation, str)
                    )
    lab_operation_events: list[str] = []
    lab_operation_status = None
    if lab_operation_id:
        terminal_operation_statuses = {
            "complete", "timeout", "controller-unavailable", "controller-lost", "unsupported-controller",
            "camera-unavailable", "unsupported-camera", "capture-failed", "invalid-guidance", "stalled",
            "attitude-limit", "stator-unavailable", "unsupported-stator", "stator-name-mismatch",
            "stator-not-attached", "camera-not-attached", "stator-not-ready", "stator-control-failed",
            "human-connected", "players-api-unavailable", "depth-camera-unavailable",
            "depth-camera-unsupported", "depth-camera-frame-invalid", "depth-camera-not-front-facing",
            "depth-scan-failed", "depth-scan-stale", "depth-target-out-of-view", "depth-no-clear-corridor",
            "depth-scan-invalid", "depth-overspeed", "depth-startup-motion", "depth-actuation-stalled",
            "depth-stalled", "depth-motion-divergence", "preflight-blocked", "actuator-conflict",
            "evidence-write-failed", "bounded-window-complete", "playback-ended", "entity-unavailable",
            "unsupported-recorder-or-controller", "invalid-waypoint-count", "entity-lost",
            "stop-not-confirmed", "stop-failed", "dispose-stop-not-confirmed", "dispose-stopped",
        "fixture-stopped-before-terminal",
        }
        presence_terminal = None
        presence_event = None
        terminal_status_by_type: dict[str, str] = {}
        successful_terminal_statuses = {"complete", "bounded-window-complete"}
        operation_pattern = re.compile(
            r"Voidwright evidence: type=(server-control|sensor-frame|recorded-route|depth-controller|preflight-only) operation="
            + re.escape(lab_operation_id) + r"\b.*\bstatus=([a-z-]+)"
        )
        for line in all_lines:
            match = operation_pattern.search(line)
            if match:
                event_type = match.group(1)
                if not operation_event_is_authoritative(lab_command_action, event_type):
                    continue
                status = match.group(2)
                event = line.split("->", 1)[-1].strip()
                previous_terminal = terminal_status_by_type.get(event_type)
                if previous_terminal is not None and status not in terminal_operation_statuses:
                    continue
                # A later timeout from a still-running mod must not erase a
                # host-confirmed success. A late success cannot erase a prior
                # failure either; failures may refine one another.
                if previous_terminal is not None and status in terminal_operation_statuses and (
                    (previous_terminal in successful_terminal_statuses and status == "timeout")
                    or status in successful_terminal_statuses
                ):
                    continue
                lab_operation_events.append(event)
                lab_operation_status = status
                if status in terminal_operation_statuses:
                    terminal_status_by_type[event_type] = status
                if status == "human-connected" or (status == "players-api-unavailable" and presence_terminal != "human-connected"):
                    presence_terminal = status
                    presence_event = line.split("->", 1)[-1].strip()
        lab_operation_events = list(dict.fromkeys(lab_operation_events))[-32:]
        # Game and host logs are separate streams. An older host progress line
        # must not erase the same operation's human/API-loss terminal event.
        if presence_terminal is not None:
            lab_operation_status = presence_terminal
            if presence_event not in lab_operation_events:
                lab_operation_events = lab_operation_events[-31:] + [presence_event]
    lab_operation_terminal = lab_operation_status in terminal_operation_statuses if lab_operation_id else False
    # A worker-confirmed clean shutdown is terminal for the operation even if
    # the mod never emitted a matching terminal event (for example, a stale
    # run-bound activation caused the mod to report under an older operation
    # ID). Keep the game-log status separate so this never masquerades as a
    # successful controller result.
    lab_operation_event_status = lab_operation_status
    lab_operation_terminal_source = "game-log" if lab_operation_terminal else None
    clean_shutdown = (
        all(marker in game for marker in ("Saving world - END", "Steam closed", "Log Closed"))
        or (
            "Received SIGTERM: saving world and quitting" in info
            and "Saving world before shutdown" in info
            and "Saving world - END" in game
        )
    )
    if (lab_operation_id and not lab_operation_terminal and clean_shutdown
            and log_freshness["game"] and log_freshness["magnetar"]):
        lab_operation_status = "fixture-stopped-before-terminal"
        lab_operation_terminal = True
        lab_operation_terminal_source = "worker-confirmed-clean-shutdown"
    sensor_artifact = None
    sensor_artifact_valid = None
    if lab_command_action == "sensor-frame" and lab_operation_id and lab_quarantine is not None:
        depth_path = lab_quarantine / f"sensor-{lab_operation_id}.pgm"
        metadata_path = lab_quarantine / f"sensor-{lab_operation_id}.json"
        depth_size = depth_path.stat().st_size if depth_path.is_file() and not depth_path.is_symlink() else -1
        metadata_size = metadata_path.stat().st_size if metadata_path.is_file() and not metadata_path.is_symlink() else -1
        depth_bounded = 0 <= depth_size <= 64 + 2304 * 2
        metadata_bounded = 0 <= metadata_size <= 1024 * 1024
        depth_sha256 = file_hash(depth_path) if depth_bounded else None
        metadata_sha256 = file_hash(metadata_path) if metadata_bounded else None
        depth_raw = depth_path.read_bytes() if depth_sha256 and depth_bounded else b""
        metadata_raw = metadata_path.read_bytes() if metadata_sha256 and metadata_bounded else b""
        try:
            metadata = json.loads(metadata_raw.decode("utf-8")) if metadata_sha256 else None
        except (OSError, json.JSONDecodeError):
            metadata = None
        except UnicodeDecodeError:
            metadata = None
        logged_hashes = None
        logged_entity_ids_sha256 = None
        logged_normal_sha256 = None
        for event in reversed(lab_operation_events):
            match = re.search(
                r"\bstatus=complete\b.*\bdepthSha256=([0-9a-f]{64})\b.*\bmetadataSha256=([0-9a-f]{64})\b",
                event,
            )
            if match:
                logged_hashes = {"depth_sha256": match.group(1), "metadata_sha256": match.group(2)}
                entity_match = re.search(r"\bentityIdsSha256=([0-9a-f]{64})\b", event)
                logged_entity_ids_sha256 = entity_match.group(1) if entity_match else None
                normal_match = re.search(r"\bnormalSha256=([0-9a-f]{64})\b", event)
                logged_normal_sha256 = normal_match.group(1) if normal_match else None
                break
        entity_ids_path = lab_quarantine / f"sensor-{lab_operation_id}.i64le"
        entity_ids_size = (
            entity_ids_path.stat().st_size
            if entity_ids_path.is_file() and not entity_ids_path.is_symlink() else -1
        )
        entity_ids_size_bounded = 0 <= entity_ids_size <= 2304 * 8
        entity_ids_sha256 = file_hash(entity_ids_path) if entity_ids_size_bounded else None
        entity_ids_present = isinstance(metadata, dict) and "entity_id_path" in metadata
        surface_normals_present = isinstance(metadata, dict) and "normal_path" in metadata
        entity_ids_valid = None
        entity_summaries_valid = None
        surface_normals_valid = None
        gravity_reference_present = isinstance(metadata, dict) and "natural_gravity_world" in metadata
        gravity_reference_valid = None
        if gravity_reference_present:
            gravity = metadata.get("natural_gravity_world")
            natural_up = metadata.get("natural_up_world")
            magnitude = metadata.get("natural_gravity_magnitude")
            interference = metadata.get("natural_gravity_interference")
            vectors_well_formed = all(
                isinstance(vector, list) and len(vector) == 3
                and all(type(component) in (int, float) and math.isfinite(component) for component in vector)
                for vector in (gravity, natural_up)
            )
            gravity_reference_valid = bool(
                vectors_well_formed
                and type(magnitude) in (int, float) and math.isfinite(magnitude) and magnitude >= 0
                and type(interference) in (int, float) and math.isfinite(interference)
                and abs(math.sqrt(sum(component * component for component in gravity)) - magnitude) <= 0.02
                and (
                    (magnitude <= 0.000001 and all(abs(component) <= 0.000001 for component in natural_up))
                    or (
                        magnitude > 0.000001
                        and abs(math.sqrt(sum(component * component for component in natural_up)) - 1.0) <= 0.02
                        and math.sqrt(sum((up + down / magnitude) ** 2 for up, down in zip(natural_up, gravity))) <= 0.02
                    )
                )
            )
        width = metadata.get("width") if isinstance(metadata, dict) else None
        height = metadata.get("height") if isinstance(metadata, dict) else None
        frame_shape_valid = bool(
            type(width) is int and type(height) is int
            and 4 <= width <= 64 and 4 <= height <= 36
            and width * height <= 2304
        )
        pixel_count = width * height if frame_shape_valid else -1
        expected_depth_header = f"P5\n{width} {height}\n65535\n".encode() if frame_shape_valid else b""
        depth_pixels = depth_raw[len(expected_depth_header):] if expected_depth_header and depth_raw.startswith(expected_depth_header) else b""
        depth_map_valid = bool(
            depth_raw and expected_depth_header and depth_raw.startswith(expected_depth_header)
            and len(depth_pixels) == pixel_count * 2
        )
        depth_hit_pixels = (
            sum(sample[0] != 0 for sample in struct.iter_unpack(">H", depth_pixels))
            if depth_map_valid else None
        )
        if surface_normals_present:
            surface_normal_path = lab_quarantine / f"sensor-{lab_operation_id}.n16le"
            surface_normal_size = (
                surface_normal_path.stat().st_size
                if surface_normal_path.is_file() and not surface_normal_path.is_symlink() else -1
            )
            surface_normal_size_bounded = 0 <= surface_normal_size <= 2304 * 6
            surface_normal_sha256 = (
                file_hash(surface_normal_path) if surface_normal_size_bounded else None
            )
            surface_normal_raw = (
                surface_normal_path.read_bytes()
                if surface_normal_sha256 and surface_normal_size_bounded else b""
            )
            normal_vectors = (
                list(struct.iter_unpack("<hhh", surface_normal_raw))
                if surface_normal_raw and len(surface_normal_raw) % 6 == 0 else []
            )
            normal_nonzero_count = sum(any(component != 0 for component in vector) for vector in normal_vectors)
            normal_invalid_count = sum(
                depth != 0 and not any(component != 0 for component in vector)
                for depth, vector in zip(
                    (sample[0] for sample in struct.iter_unpack(">H", depth_pixels)) if depth_map_valid else [],
                    normal_vectors,
                )
            )
            normal_alignment_valid = bool(
                depth_map_valid and len(normal_vectors) == pixel_count
                and all(
                    depth != 0 or vector == (0, 0, 0)
                    for depth, vector in zip(
                        (sample[0] for sample in struct.iter_unpack(">H", depth_pixels)), normal_vectors
                    )
                )
                and all(
                    (32767 * 0.95) ** 2 <= sum(component * component for component in vector) <= (32767 * 1.05) ** 2
                    for vector in normal_vectors if any(component != 0 for component in vector)
                )
            )
            surface_normals_valid = bool(
                metadata.get("normal_path") == f"sensor-{lab_operation_id}.n16le"
                and metadata.get("normal_encoding") == "xyz-i16le-unit32767-zero-is-invalid-or-miss"
                and surface_normal_size_bounded
                and len(surface_normal_raw) == pixel_count * 6
                and metadata.get("normal_sha256") == surface_normal_sha256
                and logged_normal_sha256 == surface_normal_sha256
                and normal_alignment_valid
                and type(metadata.get("valid_normal_hits")) is int
                and metadata.get("valid_normal_hits") == normal_nonzero_count
                and type(metadata.get("invalid_normal_hits")) is int
                and metadata.get("invalid_normal_hits") == normal_invalid_count
                and normal_nonzero_count + normal_invalid_count == depth_hit_pixels
            )
        else:
            surface_normal_path = lab_quarantine / f"sensor-{lab_operation_id}.n16le"
            surface_normal_sha256 = None
        if entity_ids_present:
            entity_ids_raw = entity_ids_path.read_bytes() if entity_ids_sha256 and entity_ids_size_bounded else b""
            entity_values = (
                [value[0] for value in struct.iter_unpack("<q", entity_ids_raw)]
                if entity_ids_raw and len(entity_ids_raw) % 8 == 0 else []
            )
            grid_id = metadata.get("grid_id")
            summaries = metadata.get("entity_summaries")
            entity_summaries_present = "entity_summaries" in metadata
            if entity_summaries_present:
                summary_counts: dict[int, int] = {}
                summaries_well_formed = isinstance(summaries, list) and len(summaries) <= 2304
                if summaries_well_formed:
                    for summary in summaries:
                        if (not isinstance(summary, dict)
                                or type(summary.get("entity_id")) is not int
                                or summary.get("entity_id", 0) <= 0
                                or type(summary.get("hit_count")) is not int
                                or summary.get("hit_count", 0) <= 0
                                or not isinstance(summary.get("display_name"), str)
                                or len(summary.get("display_name", "")) > 128
                                or not isinstance(summary.get("runtime_type"), str)
                                or len(summary.get("runtime_type", "")) > 128
                                or summary["entity_id"] in summary_counts):
                            summaries_well_formed = False
                            break
                        summary_counts[summary["entity_id"]] = summary["hit_count"]
                entity_summaries_valid = bool(
                    summaries_well_formed
                    and summary_counts == dict(Counter(value for value in entity_values if value != 0))
                )
            entity_ids_valid = bool(
                metadata.get("entity_id_path") == f"sensor-{lab_operation_id}.i64le"
                and metadata.get("entity_id_encoding") == "i64le-zero-is-no-entity"
                and entity_ids_size_bounded
                and frame_shape_valid
                and len(entity_ids_raw) == pixel_count * 8
                and 16 <= pixel_count <= 2304
                and 4 <= width <= 64 and 4 <= height <= 36
                and metadata.get("entity_id_sha256") == entity_ids_sha256
                and logged_entity_ids_sha256 == entity_ids_sha256
                and type(metadata.get("hit_pixels")) is int
                and metadata.get("hit_pixels") == depth_hit_pixels
                and type(metadata.get("known_entity_hits")) is int
                and metadata.get("known_entity_hits") == sum(value != 0 for value in entity_values)
                and type(grid_id) is int and grid_id > 0
                and type(metadata.get("self_grid_hits")) is int
                and metadata.get("self_grid_hits") == sum(value == grid_id for value in entity_values)
                and (not entity_summaries_present or entity_summaries_valid is True)
            )
        sensor_artifact_valid = bool(
            isinstance(metadata, dict)
            and metadata.get("schema") == "vragecage.sensor-frame.v1"
            and metadata.get("operation_id") == lab_operation_id
            and metadata.get("kind") == "depth"
            and metadata.get("depth_sha256") == depth_sha256
            and depth_map_valid is True
            and type(metadata.get("hit_pixels")) is int
            and metadata.get("hit_pixels") == depth_hit_pixels
            and logged_hashes is not None
            and logged_hashes.get("depth_sha256") == depth_sha256
            and logged_hashes.get("metadata_sha256") == metadata_sha256
            and frame_shape_valid
            and (not entity_ids_present or entity_ids_valid is True)
            and (not surface_normals_present or surface_normals_valid is True)
            and (not gravity_reference_present or gravity_reference_valid is True)
        )
        sensor_artifact = {
            "depth_path": str(depth_path.relative_to(instance)),
            "depth_sha256": depth_sha256,
            "metadata_path": str(metadata_path.relative_to(instance)),
            "metadata_sha256": metadata_sha256,
            "metadata": metadata,
            "logged_hashes": logged_hashes,
            "entity_id_path": str(entity_ids_path.relative_to(instance)) if entity_ids_present else None,
            "entity_id_sha256": entity_ids_sha256 if entity_ids_present else None,
            "entity_ids_valid": entity_ids_valid,
            "entity_summaries_valid": entity_summaries_valid,
            "depth_map_valid": depth_map_valid,
            "surface_normal_path": str(surface_normal_path.relative_to(instance)) if surface_normals_present else None,
            "surface_normal_sha256": surface_normal_sha256 if surface_normals_present else None,
            "surface_normals_valid": surface_normals_valid,
            "gravity_reference_valid": gravity_reference_valid,
        }
    receipt = {
        "schema": "vragecage.magnetar-receipt.v1",
        "engine": "magnetar",
        "logs_updated_since_activation": log_freshness,
        "game_log": str(game_log),
        "game_log_bytes": len(game_raw),
        "game_log_sha256": hashlib.sha256(game_raw).hexdigest(),
        "magnetar_log": str(info_log),
        "magnetar_log_bytes": len(info_raw),
        "magnetar_log_sha256": hashlib.sha256(info_raw).hexdigest(),
        "app_version": first(r"App Version:\s*(.+)$", game),
        "session_loaded": "Session loaded" in game,
        "game_ready": "Game ready..." in game,
        "clean_shutdown": clean_shutdown,
        "staged_mod": stage,
        "staged_mod_tree_sha256": actual_tree_hash,
        "staged_mod_unchanged": stage_unchanged,
        "staged_plugin": plugin_stage,
        "staged_plugin_tree_sha256": actual_plugin_hash,
        "staged_plugin_unchanged": plugin_unchanged,
        "published_mod_packages": published_mod_packages,
        "published_mod_packages_valid": published_mod_packages_valid,
        "published_mod_package_error": published_mod_package_error,
        "plugin_expected_log_markers": observed_plugin_markers,
        "definitions_loaded": definitions_loaded,
        "scripts_loaded": scripts_loaded,
        "expected_log_markers": observed_expected_markers,
        "observation_prefix": observation_prefix,
        "observations": observations,
        "observations_omitted": observations_omitted,
        "target_fatal_markers": target_fatal,
        "target_fatal_markers_omitted": target_fatal_omitted,
        "environment_warnings": environment_warnings,
        "environment_warnings_omitted": environment_warnings_omitted,
        "lab_authority": lab_authority,
        "lab_contract_sha256": lab_contract_sha256,
        "lab_contract_valid": lab_valid,
        "lab_run_id": lab_run_id,
        "quarantine_run": quarantine_run,
        "lab_operation_id": lab_operation_id,
        "lab_command_action": lab_command_action,
        "lab_operation_status": lab_operation_status,
        "lab_operation_terminal": lab_operation_terminal,
        "lab_operation_event_status": lab_operation_event_status,
        "lab_operation_terminal_source": lab_operation_terminal_source,
        "lab_operation_events": lab_operation_events,
        "sensor_artifact": sensor_artifact,
        "sensor_artifact_valid": sensor_artifact_valid,
        "fixture_edit_ledger_sha256": fixture_edit_ledger_sha256,
        "fixture_edit_count": fixture_edit_count,
        "fixture_edits_valid": fixture_edits_valid,
        "fixture_sector_sha256_at_activation": fixture_sector_sha256_at_activation,
        "fixture_sector_sha256_current": fixture_sector_sha256_current,
        "fixture_sector_changed_by_engine": fixture_sector_changed_by_engine,
    }
    receipt["passed"] = bool(
        all(log_freshness.values())
        and receipt["session_loaded"]
        and receipt["game_ready"]
        and not target_fatal
        and all(observed_expected_markers.values())
        and all(observed_plugin_markers.values())
        and (not lab_present or lab_valid)
        and (stage is None or (definitions_loaded and scripts_loaded and stage_unchanged))
        and (plugin_stage is None or plugin_unchanged)
        and (instance.name != LOCAL_TEST_INSTANCE or published_mod_packages_valid is True)
        and (lab_operation_id is None or lab_operation_status in {"complete", "bounded-window-complete"})
        and (lab_command_action != "sensor-frame" or sensor_artifact_valid is True)
        and (fixture_edits_valid is not False)
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
