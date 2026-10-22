#!/usr/bin/env python3
"""Bind the read-only block lifecycle diagnostic to one instrumented V4 clone."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


EXPECTED_SOURCE_SHA256 = "9ae4286ebb6fad94516612cfb083c347ccb197c62276d5658a49d65ede373f8f"
TARGET_FILENAME = "vragecage-block-lifecycle-target-v1.json"
MOD_ASSEMBLY_NAME = "Voidwright"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def package_digest(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("registered Magnetar package cache is missing or unsafe")
    value = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise SystemExit("registered Magnetar package cache contains a symbolic link")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        value.update(len(relative).to_bytes(4, "big"))
        value.update(relative)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
    return value.hexdigest()


def read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"required clone receipt is missing or unsafe: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"clone receipt is unreadable: {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"clone receipt must be an object: {path.name}")
    return value


def validate_magnetar_registration(instance: Path, world: Path, mod: dict, mod_id: int) -> None:
    if mod.get("load_world") != str(world):
        raise SystemExit("Magnetar mod receipt does not bind to the configured LoadWorld")
    cache = instance.parent.parent / "magnetar-steam/steamapps/workshop/content/244850" / str(mod_id)
    if (Path(str(mod.get("path", ""))) != cache or
            package_digest(cache) != mod.get("tree_sha256")):
        raise SystemExit("Magnetar mod cache differs from the registration receipt")
    source = instance / "MagnetarConfig/Sources/sources.xml"
    profile = instance / "MagnetarConfig/Profiles/Current.xml"
    for path, receipt_field in ((source, "sources_sha256"), (profile, "profile_sha256")):
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Magnetar registration file is missing or unsafe: {path.name}")
        if digest(path) != mod.get(receipt_field):
            raise SystemExit(f"Magnetar registration file differs from its receipt: {path.name}")
    try:
        source_root = ET.parse(source).getroot()
        profile_root = ET.parse(profile).getroot()
    except ET.ParseError as error:
        raise SystemExit(f"Magnetar Sources or Current profile is invalid XML: {error}") from error
    registrations = [node for node in source_root.findall("./ModSources/Mod")
                     if node.findtext("Name") == mod.get("name") or
                     node.findtext("ID") == str(mod_id)]
    if (len(registrations) != 1 or registrations[0].findtext("Name") != mod.get("name") or
            registrations[0].findtext("ID") != str(mod_id) or
            registrations[0].findtext("Enabled", "true").lower() != "true"):
        raise SystemExit("Magnetar Sources does not enable the receipt-bound local mod exactly once")
    profile_ids = [node.text for node in profile_root.findall("./Mods/unsignedLong")]
    if profile_ids.count(str(mod_id)) != 1:
        raise SystemExit("Magnetar Current profile does not select the receipt-bound local mod exactly once")
    for filename in ("Sandbox.sbc", "Sandbox_config.sbc"):
        path = world / filename
        try:
            raw = path.read_bytes()
            root = ET.fromstring(raw)
        except (OSError, ET.ParseError) as error:
            raise SystemExit(f"configured checkpoint is invalid XML: {path.name}: {error}") from error
        if b"xsd:" in raw and b"xmlns:xsd=" not in raw:
            raise SystemExit(f"configured checkpoint lost its xsd QName namespace: {path.name}")
        mods = root.find("Mods")
        if mods is None:
            raise SystemExit(f"configured checkpoint has no Mods element: {path.name}")
        duplicate = [node for node in mods.findall("ModItem")
                     if (node.findtext("PublishedFileId") or "0").strip() == str(mod_id) or
                     ((node.findtext("Name") or "").strip() == mod.get("name") and
                      (node.findtext("PublishedFileId") or "0").strip() in ("", "0"))]
        if duplicate:
            raise SystemExit(f"unpublished local mod remains in the game's Workshop list: {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True, type=Path)
    args = parser.parse_args()
    instance = args.instance.resolve(strict=True)
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                       f"vragecage-{instance.name}.service"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("refusing to bind diagnostic target while the disposable clone is running")
    if (instance / ".vragecage-pending-command.json").exists():
        raise SystemExit("refusing to bind diagnostic target while a command is pending")
    lab = read_json(instance / ".vragecage-lab.json")
    imported = read_json(instance / ".vragecage-world-import.json")
    ledger_path = instance / ".vragecage-diagnostic-instrumentation.json"
    ledger = read_json(ledger_path)
    mod = read_json(instance / ".vragecage-mod-stage.json")
    if (lab.get("authority") != "destructive-lab" or lab.get("disposable") is not True or
            lab.get("engine") != "magnetar" or lab.get("instance") != instance.name or
            lab.get("source_tree_sha256") != EXPECTED_SOURCE_SHA256 or
            imported.get("instance") != instance.name or imported.get("engine") != "magnetar" or
            imported.get("tree_sha256") != EXPECTED_SOURCE_SHA256 or
            ledger.get("instance") != instance.name or ledger.get("authority") != "disposable-clone-only" or
            ledger.get("source_tree_sha256") != EXPECTED_SOURCE_SHA256 or
            ledger.get("schema") != "vragecage.diagnostic-instrumentation.v1" or
            mod.get("instance") != instance.name or mod.get("engine") != "magnetar" or
            not isinstance(mod.get("name"), str) or not mod.get("name") or
            any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for character in mod["name"]) or
            not isinstance(mod.get("synthetic_workshop_id"), int) or
            not 90_000_000_000_000_000 <= mod.get("synthetic_workshop_id") < 90_100_000_000_000_000):
        raise SystemExit("clone, instrumentation, or mod staging receipts do not match the pinned disposable V4 target")
    sector = instance / "World/SANDBOX_0_0_0_.sbs"
    if sector.is_symlink() or digest(sector) != ledger.get("sector_after_sha256"):
        raise SystemExit("instrumented sector no longer matches its ledger; refusing target binding")
    blocks = ledger.get("added_blocks")
    controller = ledger.get("controller")
    if not isinstance(blocks, list) or len(blocks) != 2 or not isinstance(controller, dict):
        raise SystemExit("instrumentation ledger does not describe exactly one Core and one camera")
    core = next((block for block in blocks if block.get("subtype") == "VoidwrightNavigationControllerSmall"), None)
    camera = next((block for block in blocks if block.get("subtype") == "SmallCameraBlock"), None)
    grid_id = core.get("grid_id") if core else None
    if (core is None or camera is None or core.get("grid_id") != camera.get("grid_id") or
            core.get("grid_name") != "Blue Truck" or camera.get("grid_name") != "Blue Truck" or
            controller.get("entity_id") != "83468039688685570" or
            controller.get("type") != "MyObjectBuilder_Cockpit" or
            not controller.get("subtype") or not str(core.get("entity_id", "")).isdigit() or
            not str(camera.get("entity_id", "")).isdigit()):
        raise SystemExit("instrumentation ledger is not bound to the pinned Blue Truck controller/Core/camera")
    config_path = instance / "SpaceEngineers-Dedicated.cfg"
    if config_path.is_symlink() or not config_path.is_file():
        raise SystemExit("clone dedicated-server config is missing or unsafe")
    try:
        load_world = ET.parse(config_path).getroot().findtext("LoadWorld")
    except (OSError, ET.ParseError) as error:
        raise SystemExit(f"could not read configured LoadWorld path: {error}") from error
    if not load_world:
        raise SystemExit("clone config has no LoadWorld path")
    world = Path(load_world)
    if not world.is_absolute():
        world = instance / world
    world = world.resolve(strict=True)
    try:
        world.relative_to(instance)
    except ValueError as error:
        raise SystemExit("configured LoadWorld path escaped the disposable clone") from error
    sandbox = world / "Sandbox.sbc"
    if sandbox.is_symlink() or not sandbox.is_file():
        raise SystemExit("configured LoadWorld Sandbox.sbc is missing or unsafe")
    try:
        session_name = ET.parse(sandbox).getroot().findtext("SessionName")
    except (OSError, ET.ParseError) as error:
        raise SystemExit(f"could not read loaded world's SessionName: {error}") from error
    if (not session_name or session_name in {".", ".."} or
            Path(session_name).name != session_name or "/" in session_name or "\\" in session_name):
        raise SystemExit("loaded world has no safe SessionName")
    mod_id = mod["synthetic_workshop_id"]
    for world_mod_list in (world / "Sandbox.sbc", world / "Sandbox_config.sbc"):
        if world_mod_list.is_symlink() or not world_mod_list.is_file():
            raise SystemExit(f"configured checkpoint is missing or unsafe: {world_mod_list.name}")
    validate_magnetar_registration(instance, world, mod, mod_id)
    storage = instance / "Saves" / session_name / "Storage"
    if storage.is_symlink():
        raise SystemExit("configured session Storage directory must not be a symbolic link")
    storage.mkdir(mode=0o700, parents=True, exist_ok=True)
    storage = storage / f"{mod_id}.sbm_{MOD_ASSEMBLY_NAME}"
    if storage.is_symlink():
        raise SystemExit("mod world storage must not be a symbolic link")
    storage.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        storage.resolve(strict=True).relative_to(instance)
    except ValueError as error:
        raise SystemExit("mod world storage escaped the disposable clone") from error
    target_path = storage / TARGET_FILENAME
    if target_path.exists() or target_path.is_symlink():
        raise SystemExit("diagnostic target manifest already exists")
    manifest = {
        "schema": "vragecage.block-lifecycle-target.v1",
        "targetGridEntityId": str(grid_id),
        "targetGridName": "Blue Truck",
        "controllerEntityId": str(controller["entity_id"]),
        "controllerType": controller["type"],
        "controllerSubtype": controller["subtype"],
        "coreEntityId": str(core["entity_id"]),
        "coreSubtype": core["subtype"],
        "cameraEntityId": str(camera["entity_id"]),
        "cameraSubtype": camera["subtype"],
        "sourceTreeSha256": EXPECTED_SOURCE_SHA256,
        "loadWorld": str(world),
        "instrumentationLedgerSha256": digest(ledger_path),
    }
    raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".block-lifecycle-target-", dir=storage)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    print(json.dumps({"status": "target-bound", "instance": instance.name,
                      "target": str(target_path), "target_sha256": hashlib.sha256(raw).hexdigest(),
                      "ledger_sha256": manifest["instrumentationLedgerSha256"],
                      "grid_id": grid_id, "controller_id": controller["entity_id"],
                      "core_id": core["entity_id"], "camera_id": camera["entity_id"]},
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
