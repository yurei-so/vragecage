#!/usr/bin/env python3
"""Verify and activate the narrow read-only passive telemetry plugin contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path


PLUGIN_ID = "voidwright-passive-telemetry"
CONTRACT_NAME = ".vragecage-passive-telemetry.json"
ACTIVE_NAME = ".vragecage-passive-telemetry-active.json"


def load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"required passive telemetry file is missing or unsafe: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid passive telemetry file {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"invalid passive telemetry object: {path.name}")
    return value


def tree_hash(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("passive telemetry plugin directory is missing or unsafe")
    digest = hashlib.sha256()
    files = 0
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise SystemExit("passive telemetry plugin contains a symbolic link")
        if not path.is_file():
            continue
        files += 1
        total_bytes += path.stat().st_size
        if files > 10_000 or total_bytes > 256 * 1024 * 1024:
            raise SystemExit("passive telemetry plugin exceeds its staging bounds")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verify(instance: Path) -> tuple[dict[str, object], str]:
    if instance.is_symlink() or not instance.is_dir():
        raise SystemExit("passive telemetry requires an existing non-symlink instance")
    instance = instance.resolve(strict=True)
    engine_path = instance / ".vragecage-engine"
    if engine_path.is_symlink() or not engine_path.is_file() or engine_path.read_text().strip() != "magnetar":
        raise SystemExit("passive telemetry requires a Magnetar instance")
    if (instance / ".vragecage-lab.json").exists() or (instance / ".vragecage-active-run.json").exists():
        raise SystemExit("read-only passive telemetry cannot share a destructive-lab authority contract")
    contract = load_object(instance / CONTRACT_NAME)
    expected = {
        "schema": "vragecage.passive-telemetry-authority.v1",
        "authority": "read_only_observation",
        "instance": instance.name,
        "engine": "magnetar",
        "plugin_id": PLUGIN_ID,
        "capabilities": ["player-consented-passive-grid-telemetry"],
        "denied_capabilities": ["movement", "actuator-control",
                                "programmable-block-script-control", "world-entity-mutation"],
    }
    if any(contract.get(key) != value for key, value in expected.items()):
        raise SystemExit("passive telemetry authority contract does not match this instance")
    plugins_root = instance / "Plugins"
    if plugins_root.is_symlink() or not plugins_root.is_dir():
        raise SystemExit("passive telemetry plugin root is missing or unsafe")
    package = plugins_root / PLUGIN_ID
    package_hash = tree_hash(package)
    if contract.get("plugin_tree_sha256") != package_hash:
        raise SystemExit("passive telemetry plugin hash does not match its authority contract")
    receipt = load_object(instance / ".vragecage-passive-telemetry-stage.json")
    staged_contract = receipt.get("contract")
    if (receipt.get("schema") != "vragecage.plugin-stage.v1"
            or receipt.get("instance") != instance.name or receipt.get("plugin_id") != PLUGIN_ID
            or receipt.get("tree_sha256") != package_hash
            or not isinstance(staged_contract, dict)
            or staged_contract.get("schema") != "vragecage.passive-telemetry-plugin.v1"
            or staged_contract.get("permissionScope") != "read_only_observation"
            or staged_contract.get("pluginId") != PLUGIN_ID):
        raise SystemExit("passive telemetry plugin stage receipt does not match its package")
    source_root = instance / "MagnetarConfig" / "Sources"
    profile_root = instance / "MagnetarConfig" / "Profiles"
    if any(path.is_symlink() or not path.is_dir() for path in (source_root, profile_root)):
        raise SystemExit("Magnetar plugin configuration directories are missing or unsafe")
    source_path = source_root / "sources.xml"
    profile_path = profile_root / "Current.xml"
    if any(path.is_symlink() or not path.is_file() for path in (source_path, profile_path)):
        raise SystemExit("Magnetar plugin registration configuration is missing or unsafe")
    # Parse registration with the same stdlib XML implementation as staging.
    import xml.etree.ElementTree as ET
    try:
        sources = ET.parse(source_path).getroot()
        profile = ET.parse(profile_path).getroot()
    except ET.ParseError as error:
        raise SystemExit(f"invalid Magnetar plugin registration: {error}") from error
    registrations = [node for node in sources.findall("./LocalPluginSources/LocalPlugin")
                     if node.findtext("Name") == PLUGIN_ID]
    selected = [node for node in profile.findall("./DevFolder/LocalFolderConfig")
                if node.findtext("Id") == PLUGIN_ID]
    if (len(registrations) != 1 or registrations[0].findtext("Enabled", "true").lower() != "true"
            or registrations[0].findtext("Folder") != str(package)
            or len(selected) != 1):
        raise SystemExit("Magnetar does not enable exactly the contract-bound passive telemetry plugin")
    return contract, hashlib.sha256((instance / CONTRACT_NAME).read_bytes()).hexdigest()


def activate(instance: Path) -> str:
    contract, contract_hash = verify(instance)
    instance = instance.resolve(strict=True)
    run_id = secrets.token_hex(16)
    value = {
        "schema": "vragecage.passive-telemetry-run.v1",
        "authority": "read_only_observation",
        "instance": instance.name,
        "run_id": run_id,
        "contract_sha256": contract_hash,
        "plugin_tree_sha256": contract["plugin_tree_sha256"],
    }
    target = instance / ACTIVE_NAME
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return run_id


def status(instance: Path) -> dict[str, object]:
    contract, contract_hash = verify(instance)
    instance = instance.resolve(strict=True)
    active = load_object(instance / ACTIVE_NAME)
    run_id = active.get("run_id")
    if (active.get("schema") != "vragecage.passive-telemetry-run.v1"
            or active.get("authority") != "read_only_observation"
            or active.get("instance") != instance.name
            or active.get("contract_sha256") != contract_hash
            or active.get("plugin_tree_sha256") != contract.get("plugin_tree_sha256")
            or not isinstance(run_id, str) or len(run_id) != 32
            or any(character not in "0123456789abcdef" for character in run_id)):
        raise SystemExit("passive telemetry run identity does not match its authority contract")
    return {"schema": "vragecage.passive-telemetry-status.v1", "instance": instance.name,
            "permission_scope": "read_only_observation", "run_id": run_id,
            "plugin_tree_sha256": active.get("plugin_tree_sha256"),
            "contract_sha256": contract_hash}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("verify", "activate", "status"))
    parser.add_argument("instance", type=Path)
    args = parser.parse_args()
    if args.action == "verify":
        contract, contract_hash = verify(args.instance)
        print(json.dumps({"authority": contract["authority"], "instance": args.instance.name,
                          "plugin_tree_sha256": contract["plugin_tree_sha256"],
                          "contract_sha256": contract_hash}, sort_keys=True, separators=(",", ":")))
    elif args.action == "activate":
        print(activate(args.instance))
    else:
        print(json.dumps(status(args.instance), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
