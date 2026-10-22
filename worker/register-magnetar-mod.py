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

Q_LOCAL_VEHICLE_TEST_INSTANCE = "local-vehicle-steering-v4-20261021-q"
Q_LOCAL_VEHICLE_TEST_MOD_ID = 90127687454063603


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"mod package contains a symbolic link: {path}")
        if not path.is_file():
            continue
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


def resolve_synthetic_id(instance_name: str, name: str,
                         requested_id: int | None = None) -> int:
    if requested_id is None:
        return synthetic_id(name)
    if (instance_name != Q_LOCAL_VEHICLE_TEST_INSTANCE or name != "Voidwright" or
            requested_id != Q_LOCAL_VEHICLE_TEST_MOD_ID):
        raise ValueError("synthetic ID override is reserved for the pinned q Voidwright fixture")
    return Q_LOCAL_VEHICLE_TEST_MOD_ID


def ensure_cache_target_available(target: Path, *, replace: bool,
                                  reuse_existing_cache: bool) -> None:
    if target.exists() and not replace and not reuse_existing_cache:
        raise SystemExit(f"mod is already staged; pass --replace: {target}")


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
    declarations: dict[str, str] = {}
    if path.is_file():
        try:
            for _, (prefix, uri) in ET.iterparse(io.BytesIO(path.read_bytes()), events=("start-ns",)):
                previous = declarations.get(prefix)
                if previous is not None and previous != uri:
                    raise SystemExit(f"XML namespace prefix {prefix!r} is rebound in {path.name}; refusing to rewrite it")
                declarations[prefix] = uri
        except ET.ParseError as error:
            raise SystemExit(f"cannot preserve namespaces from {path.name}: {error}") from error
    root = tree.getroot()
    expanded_names = [node.tag for node in root.iter() if isinstance(node.tag, str)]
    expanded_names.extend(key for node in root.iter() for key in node.attrib if key.startswith("{"))
    for prefix, uri in declarations.items():
        ET.register_namespace(prefix, uri)
        used_expanded = any(name.startswith("{" + uri + "}") for name in expanded_names)
        # ElementTree cannot infer QName-valued strings such as xsi:type="xsd:string".
        # Keep declarations that are otherwise invisible to its expanded-name model.
        if not used_expanded:
            root.set("xmlns" + (":" + prefix if prefix else ""), uri)
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


def configured_world(instance: Path) -> Path:
    config = instance / "SpaceEngineers-Dedicated.cfg"
    if config.is_symlink() or not config.is_file():
        raise SystemExit("Magnetar mod registration requires a regular dedicated-server config")
    try:
        load_world = ET.parse(config).getroot().findtext("LoadWorld")
    except ET.ParseError as error:
        raise SystemExit(f"invalid dedicated-server config: {error}") from error
    if not load_world:
        raise SystemExit("dedicated-server config has no LoadWorld")
    world = Path(load_world)
    if not world.is_absolute():
        world = instance / world
    if world.is_symlink():
        raise SystemExit("configured LoadWorld must not be a symbolic link")
    try:
        resolved = world.resolve(strict=True)
        resolved.relative_to(instance.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise SystemExit("configured LoadWorld is missing or escaped the selected instance") from error
    if not resolved.is_dir():
        raise SystemExit("configured LoadWorld is not a directory")
    return resolved


def remove_unpublished_world_entry(path: Path, name: str, workshop_id: int) -> int:
    """Remove only this Magnetar-local package's duplicate from the loaded save."""
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    tree = ET.parse(path)
    mods = tree.getroot().find("Mods")
    if mods is None:
        return 0
    removed = 0
    for node in list(mods.findall("ModItem")):
        published = (node.findtext("PublishedFileId") or "0").strip()
        item_name = (node.findtext("Name") or "").strip()
        if published == str(workshop_id):
            if item_name != f"{workshop_id}.sbm":
                raise SystemExit(f"world entry for local package ID {workshop_id} has a conflicting name")
            mods.remove(node)
            removed += 1
        elif item_name == name and published in ("", "0"):
            mods.remove(node)
            removed += 1
    if removed:
        write_xml(path, tree)
    return removed


def main() -> int:
    options = sys.argv[4:]
    requested_id = None
    if "--synthetic-id" in options:
        index = options.index("--synthetic-id")
        if index + 1 >= len(options) or options.count("--synthetic-id") != 1:
            raise SystemExit("--synthetic-id requires one numeric ID")
        try:
            requested_id = int(options[index + 1])
        except ValueError as error:
            raise SystemExit("--synthetic-id requires one numeric ID") from error
        del options[index:index + 2]
    if any(option not in {"--replace", "--reuse-existing-cache"} for option in options) or \
            len(set(options)) != len(options) or \
            {"--replace", "--reuse-existing-cache"}.issubset(options):
        raise SystemExit(
            "usage: register-magnetar-mod.py INSTANCE NAME SOURCE "
            "[--replace | --reuse-existing-cache] [--synthetic-id ID]"
        )
    instance = Path(sys.argv[1]).resolve()
    name = sys.argv[2]
    source = Path(sys.argv[3]).expanduser().resolve()
    replace = "--replace" in options
    reuse_existing_cache = "--reuse-existing-cache" in options
    if not (source / "Data").is_dir():
        raise SystemExit(f"mod package has no Data directory: {source}")
    contract = integration_contract(source)
    if (instance / ".vragecage-engine").read_text(errors="replace").strip() != "magnetar":
        raise SystemExit(f"not a Magnetar instance: {instance}")

    lock_stream = (instance / ".vragecage-stage.lock").open("a+b")
    fcntl.flock(lock_stream, fcntl.LOCK_EX)

    worker_root = instance.parent.parent
    try:
        workshop_id = resolve_synthetic_id(instance.name, name, requested_id)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    cache = worker_root / "magnetar-steam/steamapps/workshop/content/244850"
    target = cache / str(workshop_id)
    backup = cache / f".{workshop_id}.previous"
    if not reuse_existing_cache:
        cache.mkdir(parents=True, exist_ok=True)
    if backup.exists() and not target.exists():
        os.replace(backup, target)
    ensure_cache_target_available(target, replace=replace,
                                  reuse_existing_cache=reuse_existing_cache)
    if reuse_existing_cache:
        if target.is_symlink() or not target.is_dir() or tree_hash(target) != tree_hash(source):
            raise SystemExit("existing Magnetar cache package does not match the requested local package")
        temporary = None
    else:
        temporary = Path(tempfile.mkdtemp(prefix=f".{workshop_id}.", dir=cache))
    mods_root = instance / "Mods"
    if mods_root.is_symlink():
        raise SystemExit("instance mod source directory must not be a symbolic link")
    mods_root.mkdir(parents=True, exist_ok=True)
    mirror = mods_root / "Voidwright" if name == "Voidwright" else None
    mirror_backup = mods_root / ".Voidwright.previous"
    mirror_temporary = None
    if mirror is not None:
        if mirror.is_symlink() or (mirror.exists() and not mirror.is_dir()):
            raise SystemExit("instance Voidwright source mirror is unsafe")
        if mirror_backup.exists():
            if not mirror.exists():
                os.replace(mirror_backup, mirror)
            else:
                raise SystemExit("an interrupted Voidwright mirror backup requires manual review")
        if mirror.exists() and not replace:
            raise SystemExit(f"Voidwright source mirror already exists; pass --replace: {mirror}")
        mirror_temporary = Path(tempfile.mkdtemp(prefix=".Voidwright.", dir=mods_root))
    sources_path = instance / "MagnetarConfig/Sources/sources.xml"
    profile_path = instance / "MagnetarConfig/Profiles/Current.xml"
    receipt_path = instance / ".vragecage-mod-stage.json"
    world = configured_world(instance)
    world_paths = (world / "Sandbox.sbc", world / "Sandbox_config.sbc")
    previous_files = {
        path: path.read_bytes() if path.is_file() else None
        for path in (sources_path, profile_path, receipt_path, *world_paths)
    }
    committed = False
    cache_changed = False
    try:
        if not reuse_existing_cache:
            assert temporary is not None
            shutil.copytree(source, temporary, dirs_exist_ok=True)
            if target.exists():
                if backup.exists():
                    shutil.rmtree(backup)
                os.replace(target, backup)
            os.replace(temporary, target)
            cache_changed = True
        if mirror is not None and mirror_temporary is not None:
            shutil.copytree(target, mirror_temporary, dirs_exist_ok=True)
            if tree_hash(mirror_temporary) != tree_hash(target):
                raise SystemExit("Voidwright source mirror differs from the staged Magnetar package")
            if mirror.exists():
                os.replace(mirror, mirror_backup)
            os.replace(mirror_temporary, mirror)
        replaced_ids = ensure_sources(sources_path, name, workshop_id)
        ensure_profile(profile_path, workshop_id, replaced_ids)
        removed_world_entries = sum(
            remove_unpublished_world_entry(path, name, workshop_id) for path in world_paths
        )
        receipt = {
            "schema": "vragecage.mod-stage.v1",
            "engine": "magnetar",
            "instance": instance.name,
            "name": name,
            "synthetic_workshop_id": workshop_id,
            "path": str(target),
            "tree_sha256": tree_hash(target),
            "cache_reused_without_writes": reuse_existing_cache,
            "load_world": str(world),
            "sources_sha256": hashlib.sha256(sources_path.read_bytes()).hexdigest(),
            "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
            "contract": contract,
            "removed_unpublished_world_entries": removed_world_entries,
        }
        atomic_write(receipt_path, (json.dumps(receipt, sort_keys=True) + "\n").encode())
        committed = True
    finally:
        if not committed:
            if mirror is not None and mirror.exists():
                shutil.rmtree(mirror)
            if mirror is not None and mirror_backup.exists():
                os.replace(mirror_backup, mirror)
            if cache_changed:
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
        elif cache_changed and backup.exists():
            shutil.rmtree(backup)
        if mirror is not None and committed and mirror_backup.exists():
            shutil.rmtree(mirror_backup)
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
        if mirror_temporary is not None and mirror_temporary.exists():
            shutil.rmtree(mirror_temporary)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
