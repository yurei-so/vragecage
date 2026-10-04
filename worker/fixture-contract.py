#!/usr/bin/env python3
"""Inspect and narrowly annotate disposable Space Engineers fixture copies."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
LEDGER_FILE = ".vragecage-fixture-edits.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(encoded)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def instance_contract(path: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    if path.is_symlink():
        raise SystemExit("fixture instance must not be a symbolic link")
    instance = path.resolve()
    world = instance / "World"
    if world.is_symlink() or not world.is_dir() or world.resolve().parent != instance:
        raise SystemExit("fixture world must be a direct, non-symlink instance directory")
    try:
        lab = json.loads((instance / ".vragecage-lab.json").read_text())
        imported = json.loads((instance / ".vragecage-world-import.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid fixture authority receipt: {error}")
    if not isinstance(lab, dict) or not isinstance(imported, dict):
        raise SystemExit("invalid fixture authority receipt object")
    if (lab.get("authority") != "destructive-lab" or lab.get("disposable") is not True
            or lab.get("instance") != instance.name or lab.get("engine") != "magnetar"
            or imported.get("instance") != instance.name or imported.get("engine") != "magnetar"
            or lab.get("source_tree_sha256") != imported.get("tree_sha256")):
        raise SystemExit("fixture authority does not match the imported destructive-lab copy")
    return instance, lab, imported


def is_active(instance: Path) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", f"vragecage-{instance.name}.service"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def parse_sector(instance: Path) -> tuple[Path, ET.ElementTree]:
    sector = instance / "World/SANDBOX_0_0_0_.sbs"
    if sector.is_symlink() or not sector.is_file():
        raise SystemExit("fixture sector must be a regular non-symlink file")
    try:
        return sector, ET.parse(sector)
    except ET.ParseError as error:
        raise SystemExit(f"invalid fixture sector XML: {error}")


def grids(tree: ET.ElementTree) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for entity in tree.getroot().iter("MyObjectBuilder_EntityBase"):
        if entity.get(XSI_TYPE) != "MyObjectBuilder_CubeGrid":
            continue
        controllers = []
        blocks = entity.find("CubeBlocks")
        for block in list(blocks) if blocks is not None else []:
            if block.get(XSI_TYPE) != "MyObjectBuilder_RemoteControl":
                continue
            controller_id = block.findtext("EntityId")
            if controller_id and controller_id.isdigit():
                controllers.append({
                    "entity_id": int(controller_id),
                    "custom_name": block.findtext("CustomName"),
                    "subtype": block.findtext("SubtypeName"),
                })
        result.append({
            "entity_id": int(entity.findtext("EntityId") or 0),
            "name": entity.findtext("DisplayName") or entity.findtext("Name"),
            "grid_size": entity.findtext("GridSizeEnum"),
            "static": entity.findtext("IsStatic") == "true",
            "block_count": len(list(blocks)) if blocks is not None else 0,
            "remote_controls": controllers,
        })
    return result


def gps_targets(instance: Path) -> list[dict[str, object]]:
    checkpoint = instance / "World/Sandbox.sbc"
    if checkpoint.is_symlink() or not checkpoint.is_file():
        raise SystemExit("fixture checkpoint must be a regular non-symlink file")
    try:
        tree = ET.parse(checkpoint)
    except ET.ParseError as error:
        raise SystemExit(f"invalid fixture checkpoint XML: {error}")
    targets = []
    for entry in tree.getroot().findall(".//Gps/dictionary/item/Value/Entries/Entry"):
        name = entry.findtext("name")
        coords = entry.find("coords")
        if not name or coords is None:
            continue
        try:
            position = {axis.lower(): float(coords.findtext(axis)) for axis in ("X", "Y", "Z")}
        except (TypeError, ValueError):
            continue
        targets.append({"name": name, "description": entry.findtext("description") or "", **position})
    return targets


def inspection(instance: Path, lab: dict[str, object], imported: dict[str, object]) -> dict[str, object]:
    sector, tree = parse_sector(instance)
    ledger = instance / LEDGER_FILE
    return {
        "schema": "vragecage.fixture-inspection.v1",
        "instance": instance.name,
        "fixture_id": lab.get("fixture_id"),
        "source_tree_sha256": imported.get("tree_sha256"),
        "sector_sha256": sha256(sector),
        "edit_ledger_sha256": sha256(ledger) if ledger.is_file() else None,
        "grids": grids(tree),
        "gps_targets": gps_targets(instance),
    }


def inspect_fixture(path: Path) -> dict[str, object]:
    instance, lab, imported = instance_contract(path)
    lock_stream = (instance / ".vragecage-stage.lock").open("a+b")
    fcntl.flock(lock_stream, fcntl.LOCK_SH)
    return inspection(instance, lab, imported)


def opt_in(path: Path, controller_id: int, label: str | None) -> dict[str, object]:
    instance, lab, imported = instance_contract(path)
    lock_stream = (instance / ".vragecage-stage.lock").open("a+b")
    fcntl.flock(lock_stream, fcntl.LOCK_EX)
    if is_active(instance):
        raise SystemExit("refusing to edit a fixture while its service is active")
    sector, tree = parse_sector(instance)
    matches: list[tuple[ET.Element, ET.Element]] = []
    for entity in tree.getroot().iter("MyObjectBuilder_EntityBase"):
        if entity.get(XSI_TYPE) != "MyObjectBuilder_CubeGrid":
            continue
        blocks = entity.find("CubeBlocks")
        for block in list(blocks) if blocks is not None else []:
            if (block.get(XSI_TYPE) == "MyObjectBuilder_RemoteControl"
                    and block.findtext("EntityId") == str(controller_id)):
                matches.append((entity, block))
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one Remote Control with entity ID {controller_id}; found {len(matches)}")
    grid, block = matches[0]
    old_name = block.findtext("CustomName") or ""
    base_name = label or old_name or grid.findtext("DisplayName") or "Remote Control"
    if len(base_name) > 80 or "\n" in base_name or "\r" in base_name:
        raise SystemExit("fixture controller label must be at most 80 characters on one line")
    new_name = base_name if base_name.startswith("[Voidwright]") else f"[Voidwright] {base_name}"
    if old_name == new_name:
        return {**inspection(instance, lab, imported), "status": "already_opted_in", "controller_id": controller_id}
    ledger_path = instance / LEDGER_FILE
    if ledger_path.is_symlink():
        raise SystemExit("fixture edit ledger must not be a symbolic link")
    if ledger_path.is_file():
        try:
            ledger = json.loads(ledger_path.read_text())
        except json.JSONDecodeError as error:
            raise SystemExit(f"invalid fixture edit ledger: {error}")
    else:
        ledger = {
            "schema": "vragecage.fixture-edits.v1",
            "instance": instance.name,
            "fixture_id": lab.get("fixture_id"),
            "source_tree_sha256": imported.get("tree_sha256"),
            "edits": [],
        }
    if (not isinstance(ledger, dict) or ledger.get("schema") != "vragecage.fixture-edits.v1"
            or ledger.get("fixture_id") != lab.get("fixture_id") or not isinstance(ledger.get("edits"), list)):
        raise SystemExit("fixture edit ledger does not match this fixture")
    sector_raw = sector.read_bytes()
    before = sha256(sector)
    custom_name = block.find("CustomName")
    if custom_name is None:
        custom_name = ET.Element("CustomName")
        children = list(block)
        insert_at = next(
            (index for index, child in enumerate(children)
             if child.tag in {"ComponentContainer", "ShowOnHUD", "ShowInTerminal", "Enabled"}),
            len(children),
        )
        block.insert(insert_at, custom_name)
    custom_name.text = new_name
    temporary = sector.with_name(sector.name + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    os.chmod(temporary, sector.stat().st_mode & 0o777)
    ET.parse(temporary)
    os.replace(temporary, sector)
    after = sha256(sector)
    edit = {
        "operation_id": secrets.token_hex(16),
        "action": "voidwright-opt-in",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "controller_id": controller_id,
        "grid_id": int(grid.findtext("EntityId") or 0),
        "old_name": old_name,
        "new_name": new_name,
        "sector_sha256_before": before,
        "sector_sha256_after": after,
        "previous_operation_id": ledger["edits"][-1].get("operation_id") if ledger["edits"] else None,
    }
    ledger["edits"].append(edit)
    try:
        atomic_json(ledger_path, ledger)
    except Exception:
        rollback = sector.with_name(sector.name + ".rollback")
        rollback.write_bytes(sector_raw)
        os.chmod(rollback, sector.stat().st_mode & 0o777)
        os.replace(rollback, sector)
        raise
    return {**inspection(instance, lab, imported), "status": "opted_in", "edit": edit}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("instance")
    opt_parser = subparsers.add_parser("opt-in-voidwright")
    opt_parser.add_argument("instance")
    opt_parser.add_argument("controller_id", type=int)
    opt_parser.add_argument("--label")
    args = parser.parse_args()
    instance = Path(args.instance).expanduser()
    result = inspect_fixture(instance) if args.command == "inspect" else opt_in(
        instance, args.controller_id, args.label
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
