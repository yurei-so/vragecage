#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


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


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: magnetar-receipt.py INSTANCE_DIRECTORY")
    instance = Path(sys.argv[1]).resolve()
    game_log = latest(list(instance.glob("SpaceEngineersDedicated_*.log")), "dedicated-server")
    info_log = latest(list((instance / "MagnetarConfig").glob("info*.log")), "Magnetar")
    game_raw = game_log.read_bytes()
    info_raw = info_log.read_bytes()
    game = game_raw.decode("utf-8", errors="replace")
    info = info_raw.decode("utf-8", errors="replace")

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
                lab_authority = "destructive-lab"
                lab_run_id = run_id
                quarantine_run = f"Quarantine/{run_id}"
                command = active.get("command")
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
                    chain_valid = isinstance(edits, list) and all(
                        isinstance(edit, dict)
                        and edit.get("action") == "voidwright-opt-in"
                        and isinstance(edit.get("operation_id"), str)
                        and len(edit["operation_id"]) == 32
                        and all(character in "0123456789abcdef" for character in edit["operation_id"])
                        and isinstance(edit.get("sector_sha256_before"), str)
                        and isinstance(edit.get("sector_sha256_after"), str)
                        for edit in edits
                    )
                    if chain_valid:
                        chain_valid = all(
                            edits[index - 1]["sector_sha256_after"] == edits[index]["sector_sha256_before"]
                            for index in range(1, len(edits))
                        )
                    current_sector_hash = file_hash(instance / "World/SANDBOX_0_0_0_.sbs")
                    fixture_edits_valid = bool(
                        fixture_edit_ledger_sha256
                        and isinstance(ledger, dict)
                        and ledger.get("schema") == "vragecage.fixture-edits.v1"
                        and ledger.get("instance") == instance.name
                        and ledger.get("fixture_id") == lab.get("fixture_id")
                        and ledger.get("source_tree_sha256") == lab.get("source_tree_sha256")
                        and chain_valid and edits
                        and edits[-1].get("sector_sha256_after") == current_sector_hash
                    )
    lab_operation_events: list[str] = []
    lab_operation_status = None
    if lab_operation_id:
        operation_pattern = re.compile(
            r"Voidwright evidence: type=server-control operation="
            + re.escape(lab_operation_id) + r"\b.*\bstatus=([a-z-]+)"
        )
        for line in all_lines:
            match = operation_pattern.search(line)
            if match:
                lab_operation_events.append(line.split("->", 1)[-1].strip())
                lab_operation_status = match.group(1)
        lab_operation_events = list(dict.fromkeys(lab_operation_events))[-32:]
    lab_operation_terminal = lab_operation_status in {
        "complete", "timeout", "controller-unavailable", "controller-lost", "unsupported-controller"
    }
    receipt = {
        "schema": "vragecage.magnetar-receipt.v1",
        "engine": "magnetar",
        "game_log": str(game_log),
        "game_log_bytes": len(game_raw),
        "game_log_sha256": hashlib.sha256(game_raw).hexdigest(),
        "magnetar_log": str(info_log),
        "magnetar_log_bytes": len(info_raw),
        "magnetar_log_sha256": hashlib.sha256(info_raw).hexdigest(),
        "app_version": first(r"App Version:\s*(.+)$", game),
        "session_loaded": "Session loaded" in game,
        "game_ready": "Game ready..." in game,
        "clean_shutdown": (
            all(marker in game for marker in ("Saving world - END", "Steam closed", "Log Closed"))
            or (
                "Received SIGTERM: saving world and quitting" in info
                and "Saving world before shutdown" in info
                and "Saving world - END" in game
            )
        ),
        "staged_mod": stage,
        "staged_mod_tree_sha256": actual_tree_hash,
        "staged_mod_unchanged": stage_unchanged,
        "staged_plugin": plugin_stage,
        "staged_plugin_tree_sha256": actual_plugin_hash,
        "staged_plugin_unchanged": plugin_unchanged,
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
        "lab_operation_status": lab_operation_status,
        "lab_operation_terminal": lab_operation_terminal,
        "lab_operation_events": lab_operation_events,
        "fixture_edit_ledger_sha256": fixture_edit_ledger_sha256,
        "fixture_edit_count": fixture_edit_count,
        "fixture_edits_valid": fixture_edits_valid,
    }
    receipt["passed"] = bool(
        receipt["session_loaded"]
        and receipt["game_ready"]
        and not target_fatal
        and all(observed_expected_markers.values())
        and all(observed_plugin_markers.values())
        and (not lab_present or lab_valid)
        and (stage is None or (definitions_loaded and scripts_loaded and stage_unchanged))
        and (plugin_stage is None or plugin_unchanged)
        and (lab_operation_id is None or lab_operation_status == "complete")
        and (fixture_edits_valid is not False)
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
