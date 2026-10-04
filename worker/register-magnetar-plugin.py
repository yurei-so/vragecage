#!/usr/bin/env python3
"""Atomically stage one Magnetar development plugin into a lab instance."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_FILES = 10_000
MAX_BYTES = 256 * 1024 * 1024


def validate_source(root: Path) -> None:
    files = 0
    size = 0
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise SystemExit(f"plugin contains unsupported filesystem entry: {relative}")
        files += 1
        size += path.stat().st_size
        if files > MAX_FILES or size > MAX_BYTES:
            raise SystemExit("plugin exceeds worker safety bound")


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_xml(tree: ET.ElementTree, path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance")
    parser.add_argument("plugin_id")
    parser.add_argument("source")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not NAME.fullmatch(args.plugin_id):
        raise SystemExit("invalid plugin id")
    instance = Path(args.instance).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    worker_root = instance.parent.parent.resolve()
    incoming = (worker_root / "incoming").resolve()
    try:
        source.relative_to(incoming)
    except ValueError:
        raise SystemExit("plugin source must be beneath the worker incoming directory")
    validate_source(source)
    if (instance / ".vragecage-engine").read_text().strip() != "magnetar":
        raise SystemExit("plugin staging requires a Magnetar instance")
    lab = json.loads((instance / ".vragecage-lab.json").read_text())
    if lab.get("authority") != "destructive-lab" or lab.get("instance") != instance.name:
        raise SystemExit("plugin staging requires a destructive-lab instance")
    lock_stream = (instance / ".vragecage-stage.lock").open("a+b")
    fcntl.flock(lock_stream, fcntl.LOCK_EX)
    manifests = [path for path in source.glob("*.xml") if path.is_file()]
    if len(manifests) != 1:
        raise SystemExit("plugin source must contain exactly one root XML manifest")
    manifest = manifests[0]
    manifest_root = ET.parse(manifest).getroot()
    friendly_name = manifest_root.findtext("FriendlyName") or args.plugin_id
    contract_path = source / "vragecage.plugin.json"
    contract = json.loads(contract_path.read_text()) if contract_path.is_file() else None
    if not isinstance(contract, dict) or contract.get("schema") != "vragecage.plugin-contract.v1":
        raise SystemExit("plugin source lacks a valid vragecage.plugin.json contract")

    target_root = instance / "Plugins"
    target_root.mkdir(exist_ok=True)
    target = target_root / args.plugin_id
    if target.exists() and not args.replace:
        raise SystemExit("plugin is already staged; pass --replace")
    sources_path = instance / "MagnetarConfig/Sources/sources.xml"
    profile_path = instance / "MagnetarConfig/Profiles/Current.xml"
    sources_backup = sources_path.read_bytes()
    profile_backup = profile_path.read_bytes()
    old_target = target.with_name(target.name + ".previous")
    if old_target.exists():
        shutil.rmtree(old_target)
    temporary = Path(tempfile.mkdtemp(prefix=target.name + ".", dir=target_root))
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True)
        if target.exists():
            os.replace(target, old_target)
        os.replace(temporary, target)

        sources = ET.parse(sources_path)
        local = sources.getroot().find("LocalPluginSources")
        if local is None:
            raise RuntimeError("LocalPluginSources missing")
        for node in list(local):
            if node.findtext("Name") == args.plugin_id:
                local.remove(node)
        node = ET.SubElement(local, "LocalPlugin")
        for key, value in (("Name", args.plugin_id), ("Folder", str(target)),
                           ("File", manifest.name), ("Enabled", "true")):
            ET.SubElement(node, key).text = value
        write_xml(sources, sources_path)

        profile = ET.parse(profile_path)
        folders = profile.getroot().find("DevFolder")
        if folders is None:
            raise RuntimeError("DevFolder missing")
        for node in list(folders):
            if node.findtext("Id") == args.plugin_id:
                folders.remove(node)
        node = ET.SubElement(folders, "LocalFolderConfig")
        ET.SubElement(node, "Id").text = args.plugin_id
        ET.SubElement(node, "DebugBuild").text = "true"
        write_xml(profile, profile_path)

        receipt = {
            "schema": "vragecage.plugin-stage.v1",
            "instance": instance.name,
            "plugin_id": args.plugin_id,
            "friendly_name": friendly_name,
            "path": str(target),
            "manifest": manifest.name,
            "tree_sha256": tree_hash(target),
            "contract": contract,
        }
        receipt_path = instance / ".vragecage-plugin-stage.json"
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        if old_target.exists():
            shutil.rmtree(old_target)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        sources_path.write_bytes(sources_backup)
        profile_path.write_bytes(profile_backup)
        if target.exists():
            shutil.rmtree(target)
        if old_target.exists():
            os.replace(old_target, target)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


if __name__ == "__main__":
    sys.exit(main())
