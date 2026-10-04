#!/usr/bin/env python3
"""Create and verify disposable destructive-lab authority contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


LAB_FILE = ".vragecage-lab.json"
ACTIVE_FILE = ".vragecage-active-run.json"
PENDING_FILE = ".vragecage-pending-command.json"


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


def ensure_instance(instance: Path) -> tuple[Path, dict[str, object]]:
    instance = instance.resolve()
    world = (instance / "World").resolve()
    if world.parent != instance or not world.is_dir():
        raise SystemExit("lab world must be the instance's direct World directory")
    if instance.is_symlink() or world.is_symlink():
        raise SystemExit("lab instance and world must not be symbolic links")
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


def create(instance: Path) -> dict[str, object]:
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


def activate(instance: Path) -> dict[str, object]:
    instance = instance.resolve()
    contract, _ = validate(instance)
    quarantine = (instance / str(contract["quarantine"])).resolve()
    if quarantine.parent != instance or quarantine.is_symlink():
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
    pending_path = instance / PENDING_FILE
    if pending_path.is_file():
        command = load_object(pending_path)
        validate_drive_command(command, contract)
        command_path = run_directory / "command.json"
        os.replace(pending_path, command_path)
        active["command"] = command
        active["command_sha256"] = hashlib.sha256(command_path.read_bytes()).hexdigest()
    atomic_write(instance / ACTIVE_FILE, active)
    atomic_write(run_directory / "run.json", active)
    return active


def validate_drive_command(command: dict[str, object], contract: dict[str, object]) -> None:
    if command.get("schema") != "vragecage.lab-command.v1" or command.get("action") != "drive-distance":
        raise SystemExit("unsupported lab command")
    if command.get("fixture_id") != contract.get("fixture_id"):
        raise SystemExit("lab command belongs to another fixture")
    operation_id = command.get("operation_id")
    if (not isinstance(operation_id, str) or len(operation_id) != 32 or
            any(c not in "0123456789abcdef" for c in operation_id)):
        raise SystemExit("invalid lab command operation identity")
    controller_id = command.get("controller_id")
    distance = command.get("distance_meters")
    speed = command.get("max_speed")
    ticks = command.get("timeout_ticks")
    if not isinstance(controller_id, int) or controller_id <= 0:
        raise SystemExit("invalid lab command controller identity")
    if not isinstance(distance, (int, float)) or not 0.25 <= float(distance) <= 25:
        raise SystemExit("lab drive distance must be 0.25-25 meters")
    if not isinstance(speed, (int, float)) or not 0.1 <= float(speed) <= 5:
        raise SystemExit("lab drive speed must be 0.1-5 m/s")
    if not isinstance(ticks, int) or not 60 <= ticks <= 3600:
        raise SystemExit("lab drive timeout must be 60-3600 simulation ticks")


def plan_drive(instance: Path, controller_id: int, distance: float, speed: float, ticks: int) -> dict[str, object]:
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
        "max_speed": speed,
        "timeout_ticks": ticks,
    }
    validate_drive_command(command, contract)
    atomic_write(pending, command)
    return command


def verify_runtime(instance: Path, run_id: str) -> dict[str, object]:
    instance = instance.resolve()
    contract, _ = validate(instance)
    active = load_object(instance / ACTIVE_FILE)
    if active.get("run_id") != run_id or active.get("fixture_id") != contract.get("fixture_id"):
        raise SystemExit("active lab run does not match launch authority")
    quarantine = Path(str(active.get("quarantine", ""))).resolve()
    expected_parent = (instance / str(contract["quarantine"])).resolve()
    if quarantine.parent != expected_parent or quarantine.name != run_id or not quarantine.is_dir():
        raise SystemExit("active lab run has an invalid quarantine directory")
    return active


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "activate"):
        command = subparsers.add_parser(name)
        command.add_argument("instance")
    verify = subparsers.add_parser("verify-runtime")
    verify.add_argument("instance")
    verify.add_argument("run_id")
    drive = subparsers.add_parser("plan-drive")
    drive.add_argument("instance")
    drive.add_argument("controller_id", type=int)
    drive.add_argument("--distance", type=float, required=True)
    drive.add_argument("--max-speed", type=float, required=True)
    drive.add_argument("--timeout-ticks", type=int, required=True)
    args = parser.parse_args()
    instance = Path(args.instance).expanduser()
    if args.command == "create":
        result = create(instance)
    elif args.command == "activate":
        result = activate(instance)
    elif args.command == "verify-runtime":
        result = verify_runtime(instance, args.run_id)
    else:
        result = plan_drive(instance, args.controller_id, args.distance, args.max_speed, args.timeout_ticks)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
