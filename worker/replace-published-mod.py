#!/usr/bin/env python3
"""Replace one published Workshop dependency in a stopped disposable Magnetar world copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSD = "http://www.w3.org/2001/XMLSchema"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def remove_exact_mod(path: Path, workshop_id: int) -> tuple[bytes, int, str]:
    ET.register_namespace("xsd", XSD)
    ET.register_namespace("xsi", XSI)
    tree = ET.parse(path)
    mods = tree.getroot().find("Mods")
    if mods is None:
        raise SystemExit(f"world file has no Mods list: {path.name}")
    matches = [node for node in mods.findall("ModItem")
               if (node.findtext("PublishedFileId") or "").strip() == str(workshop_id)]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one Workshop ID {workshop_id} in {path.name}, found {len(matches)}")
    removed_name = (matches[0].findtext("Name") or "").strip()
    mods.remove(matches[0])
    # VRage object-builder XML stores some xsd types as QName-valued strings in
    # xsi:type. ElementTree does not retain an otherwise-unused xmlns:xsd binding.
    if any("xsd:" in value for node in tree.getroot().iter() for value in node.attrib.values()):
        tree.getroot().set("xmlns:xsd", XSD)
    ET.indent(tree, space="  ")
    rendered = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)
    return rendered, 1, removed_name


def replace(instance: Path, workshop_id: int, replacement_name: str) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("instance path must not be a symbolic link")
    instance = instance.expanduser().resolve(strict=True)
    lab_path = instance / ".vragecage-lab.json"
    import_path = instance / ".vragecage-world-import.json"
    engine_path = instance / ".vragecage-engine"
    if not all(path.is_file() for path in (lab_path, import_path, engine_path)):
        raise SystemExit("replacement requires an imported destructive-lab instance")
    lab = json.loads(lab_path.read_text(encoding="utf-8"))
    imported = json.loads(import_path.read_text(encoding="utf-8"))
    if (lab.get("authority") != "destructive-lab" or lab.get("disposable") is not True
            or lab.get("instance") != instance.name or imported.get("instance") != instance.name
            or imported.get("engine") != "magnetar"
            or engine_path.read_text(encoding="utf-8").strip() != "magnetar"):
        raise SystemExit("replacement is restricted to the matching disposable Magnetar lab")
    unit = f"vragecage-{instance.name}.service"
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if active.returncode == 0:
        raise SystemExit(f"refusing to edit a running instance: {unit}")

    stage_path = instance / ".vragecage-mod-stage.json"
    if not stage_path.is_file():
        raise SystemExit("stage the replacement local mod before replacing the published dependency")
    staged = json.loads(stage_path.read_text(encoding="utf-8"))
    stage_root = Path(str(staged.get("path", "")))
    if (staged.get("engine") != "magnetar" or staged.get("instance") != instance.name
            or staged.get("name") != replacement_name or not stage_root.is_dir()
            or tree_sha256(stage_root) != staged.get("tree_sha256")):
        raise SystemExit("replacement mod receipt or staged package does not match the requested mod")

    for prior_path in instance.glob(f".vragecage-mod-replacement-*-{workshop_id}.json"):
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        operation_id = prior.get("operation_id")
        restoration_path = instance / f".vragecage-mod-restoration-{operation_id}.json"
        if not restoration_path.is_file():
            raise SystemExit(f"Workshop ID {workshop_id} already has an unrestored replacement: {prior_path.name}")
        restoration = json.loads(restoration_path.read_text(encoding="utf-8"))
        if (restoration.get("schema") != "vragecage.published-mod-restoration.v1"
                or restoration.get("operation_id") != operation_id
                or restoration.get("instance") != instance.name
                or restoration.get("world_files_after_sha256") != prior.get("world_files_before_sha256")):
            raise SystemExit(f"prior replacement restoration receipt is invalid: {restoration_path.name}")
        for filename, expected in prior.get("world_files_before_sha256", {}).items():
            if sha256(instance / "World" / filename) != expected:
                raise SystemExit(f"prior replacement was restored but the world changed since: {filename}")
    world = instance / "World"
    paths = (world / "Sandbox.sbc", world / "Sandbox_config.sbc")
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise SystemExit("world checkpoint/config must be regular non-symlink files")
    rendered: dict[Path, bytes] = {}
    names: dict[str, str] = {}
    for path in paths:
        data, _, removed_name = remove_exact_mod(path, workshop_id)
        rendered[path] = data
        names[path.name] = removed_name

    operation_id = uuid.uuid4().hex
    backup_dir = instance / "Quarantine" / "mod-replacements" / operation_id
    if backup_dir.exists():
        raise SystemExit("replacement backup directory unexpectedly exists")
    backup_dir.mkdir(parents=True)
    before_hashes: dict[str, str] = {}
    after_hashes: dict[str, str] = {}
    for path in paths:
        backup = backup_dir / path.name
        shutil.copy2(path, backup)
        before_hashes[path.name] = sha256(path)
        after_hashes[path.name] = hashlib.sha256(rendered[path]).hexdigest()

    receipt_path = instance / f".vragecage-mod-replacement-{operation_id}-{workshop_id}.json"
    receipt = {
        "schema": "vragecage.published-mod-replacement.v1",
        "operation_id": operation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instance": instance.name,
        "workshop_id_removed": workshop_id,
        "removed_names": names,
        "replacement_mod_name": replacement_name,
        "replacement_synthetic_workshop_id": staged.get("synthetic_workshop_id"),
        "replacement_tree_sha256": staged.get("tree_sha256"),
        "world_files_before_sha256": before_hashes,
        "world_files_after_sha256": after_hashes,
        "backups": {path.name: str(backup_dir / path.name) for path in paths},
        "source_world_import_sha256": imported.get("tree_sha256"),
    }
    committed: list[Path] = []
    try:
        for path in paths:
            atomic_bytes(path, rendered[path])
            committed.append(path)
        atomic_bytes(receipt_path, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    except BaseException:
        for path in committed:
            atomic_bytes(path, (backup_dir / path.name).read_bytes())
        raise
    return receipt


def restore(instance: Path, operation_id: str) -> dict[str, object]:
    if instance.is_symlink():
        raise SystemExit("instance path must not be a symbolic link")
    instance = instance.expanduser().resolve(strict=True)
    matches = list(instance.glob(f".vragecage-mod-replacement-{operation_id}-*.json"))
    if len(matches) != 1:
        raise SystemExit("replacement operation receipt is missing or ambiguous")
    receipt_path = matches[0]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("schema") != "vragecage.published-mod-replacement.v1" or receipt.get("instance") != instance.name:
        raise SystemExit("replacement receipt does not belong to this instance")
    if receipt.get("operation_id") != operation_id:
        raise SystemExit("replacement receipt operation ID mismatch")
    if (instance / f".vragecage-mod-restoration-{operation_id}.json").exists():
        raise SystemExit("replacement operation already has a restoration receipt")
    unit = f"vragecage-{instance.name}.service"
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if active.returncode == 0:
        raise SystemExit(f"refusing to restore a running instance: {unit}")

    workshop_id = receipt.get("workshop_id_removed")
    if not isinstance(workshop_id, int) or workshop_id <= 0:
        raise SystemExit("replacement receipt has an invalid Workshop ID")
    backup_dir = instance / "Quarantine" / "mod-replacements" / operation_id
    world = instance / "World"
    paths = (world / "Sandbox.sbc", world / "Sandbox_config.sbc")
    previous: dict[Path, bytes] = {}
    backups: dict[Path, bytes] = {}
    current_hashes: dict[str, str] = {}
    before = receipt.get("world_files_before_sha256")
    after = receipt.get("world_files_after_sha256")
    for path in paths:
        backup = backup_dir / path.name
        if path.is_symlink() or not path.is_file() or backup.is_symlink() or not backup.is_file():
            raise SystemExit(f"world or recovery copy is missing/unsafe: {path.name}")
        if sha256(path) != after.get(path.name):
            raise SystemExit(f"world file changed since replacement; refusing restore: {path.name}")
        if sha256(backup) != before.get(path.name):
            raise SystemExit(f"recovery copy hash mismatch; refusing restore: {backup.name}")
        previous[path] = path.read_bytes()
        backups[path] = backup.read_bytes()
        current_hashes[path.name] = sha256(path)

    restored: list[Path] = []
    try:
        for path in paths:
            atomic_bytes(path, backups[path])
            restored.append(path)
        restore_receipt = {
            "schema": "vragecage.published-mod-restoration.v1",
            "operation_id": operation_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "instance": instance.name,
            "replacement_receipt": receipt_path.name,
            "workshop_id_restored": workshop_id,
            "world_files_before_sha256": current_hashes,
            "world_files_after_sha256": {path.name: hashlib.sha256(backups[path]).hexdigest() for path in paths},
        }
        atomic_bytes(
            instance / f".vragecage-mod-restoration-{operation_id}.json",
            (json.dumps(restore_receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
    except BaseException:
        for path in restored:
            atomic_bytes(path, previous[path])
        raise
    return restore_receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("workshop_id", type=int, nargs="?")
    parser.add_argument("replacement_name", nargs="?")
    parser.add_argument("--restore-operation")
    args = parser.parse_args()
    if args.restore_operation:
        if args.workshop_id is not None or args.replacement_name is not None:
            parser.error("do not pass Workshop ID or replacement name with --restore-operation")
        if len(args.restore_operation) != 32 or any(c not in "0123456789abcdef" for c in args.restore_operation):
            parser.error("restore operation must be a 32-character lowercase hex ID")
        result = restore(args.instance, args.restore_operation)
    else:
        if args.workshop_id is None or args.workshop_id <= 0:
            parser.error("Workshop ID must be positive")
        if not args.replacement_name or len(args.replacement_name) > 64:
            parser.error("replacement name must contain 1..64 characters")
        result = replace(args.instance, args.workshop_id, args.replacement_name)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
