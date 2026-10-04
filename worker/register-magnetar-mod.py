#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def synthetic_id(name: str) -> int:
    # Positive Int64, deliberately outside currently issued Workshop IDs. The
    # stable name-derived suffix makes retries idempotent without a registry.
    suffix = int.from_bytes(hashlib.sha256(name.encode()).digest()[:6], "big")
    return 90_000_000_000_000_000 + suffix % 100_000_000_000_000


def integration_contract(source: Path) -> dict[str, object]:
    path = source / "vragecage.integration.json"
    if not path.is_file():
        return {"schema": "vragecage.mod-contract.v1", "expected_log_markers": [], "observation_prefix": None}
    value = json.loads(path.read_text())
    if value.get("schema") != "vragecage.mod-contract.v1":
        raise SystemExit(f"unsupported integration contract schema: {path}")
    markers = value.get("expected_log_markers")
    if not isinstance(markers, list) or len(markers) > 16 or any(
        not isinstance(marker, str) or not marker or len(marker) > 256 for marker in markers
    ):
        raise SystemExit(f"invalid expected_log_markers in {path}")
    observation_prefix = value.get("observation_prefix")
    if observation_prefix is not None and (
        not isinstance(observation_prefix, str)
        or not observation_prefix
        or len(observation_prefix) > 128
        or "\n" in observation_prefix
        or "\r" in observation_prefix
    ):
        raise SystemExit(f"invalid observation_prefix in {path}")
    return {
        "schema": value["schema"],
        "expected_log_markers": markers,
        "observation_prefix": observation_prefix,
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_xml(path: Path, tree: ET.ElementTree) -> None:
    ET.indent(tree, space="  ")
    output = io.BytesIO()
    tree.write(output, encoding="utf-8", xml_declaration=True)
    atomic_write(path, output.getvalue())


def ensure_sources(path: Path, name: str, workshop_id: int) -> set[str]:
    if path.is_file():
        tree = ET.parse(path)
        root = tree.getroot()
    else:
        root = ET.Element("SourcesConfig")
        ET.SubElement(root, "ShowWarning").text = "false"
        ET.SubElement(root, "MaxSourceAge").text = "2"
        ET.SubElement(root, "LocalHubSources")
        remote_hubs = ET.SubElement(root, "RemoteHubSources")
        hub = ET.SubElement(remote_hubs, "RemoteHub")
        for key, value in (("Name", "MagnetarHub"), ("Repo", "CometWorks/magnetar-hub"), ("Branch", "main"), ("Enabled", "true"), ("Trusted", "true")):
            ET.SubElement(hub, key).text = value
        ET.SubElement(root, "RemotePluginSources")
        ET.SubElement(root, "LocalPluginSources")
        ET.SubElement(root, "ModSources")
        tree = ET.ElementTree(root)
    mods = root.find("ModSources")
    if mods is None:
        mods = ET.SubElement(root, "ModSources")
    found = None
    replaced_ids: set[str] = set()
    for node in mods.findall("Mod"):
        if node.findtext("ID") == str(workshop_id) or node.findtext("Name") == name:
            found = node
            if node.findtext("ID"):
                replaced_ids.add(node.findtext("ID"))
            break
    if found is None:
        found = ET.SubElement(mods, "Mod")
        ET.SubElement(found, "Name")
        ET.SubElement(found, "ID")
        ET.SubElement(found, "Enabled")
    found.find("Name").text = name
    found.find("ID").text = str(workshop_id)
    found.find("Enabled").text = "true"
    write_xml(path, tree)
    return replaced_ids


def ensure_profile(path: Path, workshop_id: int, replaced_ids: set[str]) -> None:
    if path.is_file():
        tree = ET.parse(path)
        root = tree.getroot()
    else:
        root = ET.Element("Profile")
        ET.SubElement(root, "Name").text = "Current"
        ET.SubElement(root, "GitHub")
        ET.SubElement(root, "DevFolder")
        ET.SubElement(root, "Local")
        ET.SubElement(root, "Mods")
        tree = ET.ElementTree(root)
    mods = root.find("Mods")
    if mods is None:
        mods = ET.SubElement(root, "Mods")
    for node in list(mods.findall("unsignedLong")):
        if node.text in replaced_ids and node.text != str(workshop_id):
            mods.remove(node)
    if str(workshop_id) not in {node.text for node in mods.findall("unsignedLong")}:
        ET.SubElement(mods, "unsignedLong").text = str(workshop_id)
    write_xml(path, tree)


def remove_unpublished_world_entry(path: Path, name: str) -> int:
    """Remove only the matching local entry from an imported fixture copy."""
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    tree = ET.parse(path)
    mods = tree.getroot().find("Mods")
    if mods is None:
        return 0
    removed = 0
    for node in list(mods.findall("ModItem")):
        published = (node.findtext("PublishedFileId") or "0").strip()
        if node.findtext("Name") == name and published in ("", "0"):
            mods.remove(node)
            removed += 1
    if removed:
        write_xml(path, tree)
    return removed


def main() -> int:
    if len(sys.argv) not in (4, 5) or (len(sys.argv) == 5 and sys.argv[4] != "--replace"):
        raise SystemExit("usage: register-magnetar-mod.py INSTANCE NAME SOURCE [--replace]")
    instance = Path(sys.argv[1]).resolve()
    name = sys.argv[2]
    source = Path(sys.argv[3]).expanduser().resolve()
    replace = len(sys.argv) == 5
    if not (source / "Data").is_dir():
        raise SystemExit(f"mod package has no Data directory: {source}")
    contract = integration_contract(source)
    if (instance / ".vragecage-engine").read_text(errors="replace").strip() != "magnetar":
        raise SystemExit(f"not a Magnetar instance: {instance}")

    lock_stream = (instance / ".vragecage-stage.lock").open("a+b")
    fcntl.flock(lock_stream, fcntl.LOCK_EX)

    worker_root = instance.parent.parent
    workshop_id = synthetic_id(name)
    cache = worker_root / "magnetar-steam/steamapps/workshop/content/244850"
    target = cache / str(workshop_id)
    backup = cache / f".{workshop_id}.previous"
    cache.mkdir(parents=True, exist_ok=True)
    if backup.exists() and not target.exists():
        os.replace(backup, target)
    if target.exists() and not replace:
        raise SystemExit(f"mod is already staged; pass --replace: {target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{workshop_id}.", dir=cache))
    sources_path = instance / "MagnetarConfig/Sources/sources.xml"
    profile_path = instance / "MagnetarConfig/Profiles/Current.xml"
    receipt_path = instance / ".vragecage-mod-stage.json"
    world_paths = (
        instance / "World/Sandbox.sbc",
        instance / "World/Sandbox_config.sbc",
    )
    previous_files = {
        path: path.read_bytes() if path.is_file() else None
        for path in (sources_path, profile_path, receipt_path, *world_paths)
    }
    committed = False
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True)
        if target.exists():
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(target, backup)
        os.replace(temporary, target)
        replaced_ids = ensure_sources(sources_path, name, workshop_id)
        ensure_profile(profile_path, workshop_id, replaced_ids)
        removed_world_entries = sum(remove_unpublished_world_entry(path, name) for path in world_paths)
        receipt = {
            "schema": "vragecage.mod-stage.v1",
            "engine": "magnetar",
            "instance": instance.name,
            "name": name,
            "synthetic_workshop_id": workshop_id,
            "path": str(target),
            "tree_sha256": tree_hash(target),
            "contract": contract,
            "removed_unpublished_world_entries": removed_world_entries,
        }
        atomic_write(receipt_path, (json.dumps(receipt, sort_keys=True) + "\n").encode())
        committed = True
    finally:
        if not committed:
            if target.exists():
                shutil.rmtree(target)
            if backup.exists():
                os.replace(backup, target)
            for path, previous in previous_files.items():
                if previous is None:
                    if path.is_file():
                        path.unlink()
                else:
                    atomic_write(path, previous)
        elif backup.exists():
            shutil.rmtree(backup)
        if temporary.exists():
            shutil.rmtree(temporary)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
