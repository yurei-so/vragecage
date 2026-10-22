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
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_FILES = 10_000
MAX_BYTES = 256 * 1024 * 1024
LOCAL_VEHICLE_TEST_INSTANCE = "land-local-test-hydro-scout-20261022-c"


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


def initialize_fresh_magnetar_plugin_config(instance: Path, sources_path: Path,
                                            profile_path: Path) -> None:
    """Create empty per-instance Magnetar registration files for the exact offline candidate."""
    if instance.name != LOCAL_VEHICLE_TEST_INSTANCE:
        raise SystemExit("Magnetar registration config may only be initialized for the exact Hydro Scout clone")
    if sources_path.exists() or profile_path.exists():
        raise SystemExit("partial Magnetar config initialization requires manual review")
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    sources = ET.Element("SourcesConfig")
    ET.SubElement(sources, "ShowWarning").text = "true"
    ET.SubElement(sources, "MaxSourceAge").text = "2"
    for name in ("LocalHubSources", "RemoteHubSources", "RemotePluginSources",
                 "LocalPluginSources", "ModSources"):
        ET.SubElement(sources, name)
    profile = ET.Element("Profile")
    ET.SubElement(profile, "Name").text = "Current"
    ET.SubElement(profile, "GitHub")
    ET.SubElement(profile, "DevFolder")
    ET.SubElement(profile, "Local")
    ET.SubElement(profile, "Mods")
    write_xml(ET.ElementTree(sources), sources_path)
    write_xml(ET.ElementTree(profile), profile_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance")
    parser.add_argument("plugin_id")
    parser.add_argument("source")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--passive-telemetry", action="store_true")
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
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                       f"vragecage-{instance.name}.service"], check=False).returncode == 0:
        raise SystemExit(f"refusing to stage a plugin while instance is running: {instance.name}")
    passive_contract_path = instance / ".vragecage-passive-telemetry.json"
    if args.passive_telemetry:
        if args.plugin_id != "voidwright-passive-telemetry":
            raise SystemExit("read-only passive telemetry staging accepts only its dedicated plugin ID")
        if (instance / ".vragecage-lab.json").exists() or (instance / ".vragecage-active-run.json").exists():
            raise SystemExit("read-only passive telemetry cannot share destructive-lab authority")
    else:
        if passive_contract_path.exists():
            raise SystemExit("a read-only passive telemetry instance cannot stage a general lab plugin")
        try:
            lab = json.loads((instance / ".vragecage-lab.json").read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit("plugin staging requires a destructive-lab instance") from error
        if lab.get("authority") != "destructive-lab" or lab.get("instance") != instance.name:
            raise SystemExit("plugin staging requires a destructive-lab instance")
    lock_stream = (instance / ".vragecage-stage.lock").open("a+b")
    fcntl.flock(lock_stream, fcntl.LOCK_EX)
    manifests = [path for path in source.glob("*.xml") if path.is_file()]
    if len(manifests) != 1:
        raise SystemExit("plugin source must contain exactly one root XML manifest")
    manifest = manifests[0]
    manifest_root = ET.parse(manifest).getroot()
    manifest_id = (manifest_root.findtext("Id") or "").strip()
    if manifest_id != args.plugin_id:
        raise SystemExit(
            f"plugin id does not match manifest Id: requested {args.plugin_id!r}, manifest {manifest_id!r}"
        )
    friendly_name = manifest_root.findtext("FriendlyName") or args.plugin_id
    contract_path = source / ("vragecage.passive-telemetry-plugin.json" if args.passive_telemetry
                              else "vragecage.plugin.json")
    contract = json.loads(contract_path.read_text()) if contract_path.is_file() else None
    if args.passive_telemetry:
        if (not isinstance(contract, dict)
                or contract.get("schema") != "vragecage.passive-telemetry-plugin.v1"
                or contract.get("permissionScope") != "read_only_observation"
                or contract.get("pluginId") != args.plugin_id
                or contract.get("entryType") != "Voidwright.ServerPlugin.DrivingLabPassiveTelemetryPlugin"
                or contract.get("capabilities") != ["player-consented-passive-grid-telemetry"]
                or contract.get("deniedCapabilities") != ["movement", "actuator-control",
                                                           "programmable-block-script-control", "world-entity-mutation"]):
            raise SystemExit("plugin source lacks the exact read-only passive telemetry package contract")
    elif not isinstance(contract, dict) or contract.get("schema") != "vragecage.plugin-contract.v1":
        raise SystemExit("plugin source lacks a valid vragecage.plugin.json contract")

    target_root = instance / "Plugins"
    target_root.mkdir(exist_ok=True)
    target = target_root / args.plugin_id
    if target.exists() and not args.replace:
        raise SystemExit("plugin is already staged; pass --replace")
    sources_path = instance / "MagnetarConfig/Sources/sources.xml"
    profile_path = instance / "MagnetarConfig/Profiles/Current.xml"
    if not sources_path.is_file() or not profile_path.is_file():
        if sources_path.exists() or profile_path.exists():
            raise SystemExit("partial Magnetar config initialization requires manual review")
        initialize_fresh_magnetar_plugin_config(instance, sources_path, profile_path)
    sources_backup = sources_path.read_bytes()
    profile_backup = profile_path.read_bytes()
    existing_sources = ET.parse(sources_path).getroot().find("LocalPluginSources")
    if existing_sources is None:
        raise SystemExit("LocalPluginSources missing")
    for existing in existing_sources:
        existing_name = existing.findtext("Name")
        if existing_name == args.plugin_id:
            continue
        existing_folder = existing.findtext("Folder")
        existing_file = existing.findtext("File")
        if not existing_folder or not existing_file:
            continue
        existing_manifest = Path(existing_folder) / existing_file
        if not existing_manifest.is_file():
            continue
        try:
            existing_id = (ET.parse(existing_manifest).getroot().findtext("Id") or "").strip()
        except ET.ParseError:
            continue
        if existing_id == manifest_id:
            raise SystemExit(
                f"plugin manifest Id {manifest_id!r} is already registered under {existing_name!r}"
            )
    old_target = target.with_name(target.name + ".previous")
    cache_root = instance / "MagnetarConfig/DevFolder"
    cache_targets = (
        sorted(path for path in cache_root.glob(args.plugin_id + "-*") if path.is_dir())
        if cache_root.is_dir() else []
    )
    cache_backup = Path(tempfile.mkdtemp(prefix=".vragecage-plugin-cache.", dir=instance))
    receipt_path = instance / (".vragecage-passive-telemetry-stage.json" if args.passive_telemetry
                               else ".vragecage-plugin-stage.json")
    receipt_backup = receipt_path.read_bytes() if receipt_path.is_file() else None
    passive_contract_backup = passive_contract_path.read_bytes() if passive_contract_path.is_file() else None
    invalidated_caches: list[str] = []
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

        # Magnetar's development-plugin cache is keyed by plugin identity, not by the
        # staged source tree. Leaving it in place after --replace can silently execute
        # the previous plugin.dll. Move only this plugin's derived caches aside until
        # the staging transaction has completed; the next launch recompiles the source.
        for cache in cache_targets:
            os.replace(cache, cache_backup / cache.name)
            invalidated_caches.append(cache.name)

        receipt = {
            "schema": "vragecage.plugin-stage.v1",
            "instance": instance.name,
            "plugin_id": args.plugin_id,
            "friendly_name": friendly_name,
            "path": str(target),
            "manifest": manifest.name,
            "tree_sha256": tree_hash(target),
            "contract": contract,
            "invalidated_dev_caches": invalidated_caches,
        }
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        if args.passive_telemetry:
            observer_contract = {
                "schema": "vragecage.passive-telemetry-authority.v1",
                "authority": "read_only_observation",
                "instance": instance.name,
                "engine": "magnetar",
                "plugin_id": args.plugin_id,
                "plugin_tree_sha256": receipt["tree_sha256"],
                "capabilities": ["player-consented-passive-grid-telemetry"],
                "denied_capabilities": ["movement", "actuator-control",
                                        "programmable-block-script-control", "world-entity-mutation"],
            }
            contract_tmp = passive_contract_path.with_name(passive_contract_path.name + ".tmp")
            contract_tmp.write_text(json.dumps(observer_contract, sort_keys=True, separators=(",", ":")) + "\n")
            os.chmod(contract_tmp, 0o600)
            os.replace(contract_tmp, passive_contract_path)
        if old_target.exists():
            shutil.rmtree(old_target)
        shutil.rmtree(cache_backup)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        sources_path.write_bytes(sources_backup)
        profile_path.write_bytes(profile_backup)
        if receipt_backup is None:
            try:
                receipt_path.unlink()
            except FileNotFoundError:
                pass
        else:
            receipt_path.write_bytes(receipt_backup)
        if args.passive_telemetry:
            if passive_contract_backup is None:
                try:
                    passive_contract_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                passive_contract_path.write_bytes(passive_contract_backup)
        if target.exists():
            shutil.rmtree(target)
        if old_target.exists():
            os.replace(old_target, target)
        for cache in cache_backup.iterdir():
            os.replace(cache, cache_root / cache.name)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if cache_backup.exists():
            shutil.rmtree(cache_backup)


if __name__ == "__main__":
    sys.exit(main())
