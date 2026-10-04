#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def build_world_tree(world_file: Path, mod_name: str) -> ET.ElementTree:
    ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    tree = ET.parse(world_file)
    root = tree.getroot()
    mods = root.find("Mods")
    if mods is None:
        raise SystemExit(f"world file has no Mods element: {world_file}")
    existing = [item for item in mods.findall("ModItem") if item.findtext("Name") == mod_name]
    for item in existing:
        mods.remove(item)
    item = ET.SubElement(mods, "ModItem", {"FriendlyName": mod_name})
    ET.SubElement(item, "Name").text = mod_name
    ET.SubElement(item, "PublishedFileId").text = "0"
    ET.SubElement(item, "PublishedServiceName").text = "Steam"
    ET.SubElement(item, "IsDependency").text = "false"
    return tree


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance", type=Path)
    parser.add_argument("mod_name")
    parser.add_argument("source", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not args.mod_name or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in args.mod_name):
        raise SystemExit("invalid mod name")
    instance = args.instance.resolve()
    source = args.source.expanduser().resolve()
    world = instance / "World"
    target = instance / "Mods" / args.mod_name
    if not source.is_dir() or not (source / "Data").is_dir():
        raise SystemExit(f"mod package has no Data directory: {source}")
    if target.exists() and not args.replace:
        raise SystemExit(f"refusing to replace staged mod: {target}")
    world_files = [world / filename for filename in ("Sandbox.sbc", "Sandbox_config.sbc")]
    trees = [(path, build_world_tree(path, args.mod_name)) for path in world_files]
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{args.mod_name}.", dir=target.parent) as temporary:
        staged = Path(temporary) / args.mod_name
        shutil.copytree(source, staged, symlinks=False)
        if target.exists():
            shutil.rmtree(target)
        staged.rename(target)
    rendered: list[tuple[Path, Path]] = []
    try:
        for path, tree in trees:
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            tree.write(temporary_path, encoding="utf-8", xml_declaration=True)
            rendered.append((path, temporary_path))
        for path, temporary_path in rendered:
            temporary_path.replace(path)
    finally:
        for _, temporary_path in rendered:
            temporary_path.unlink(missing_ok=True)
    print(f"registered local mod {args.mod_name} in {instance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
