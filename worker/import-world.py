#!/usr/bin/env python3
"""Import an uploaded Space Engineers save into a new guarded instance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def tree_receipt(root: Path) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    files = 0
    size = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise SystemExit(f"world contains unsupported filesystem entry: {relative}")
        if path.is_dir():
            continue
        files += 1
        size += path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return files, size, digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance")
    parser.add_argument("source")
    parser.add_argument("--engine", choices=("proton", "magnetar"), required=True)
    parser.add_argument("--destructive-lab", action="store_true")
    args = parser.parse_args()
    if args.destructive_lab and args.engine != "magnetar":
        raise SystemExit("destructive-lab authority currently requires --engine magnetar")

    worker_root = Path(os.environ.get("VRAGECAGE_WORKER_ROOT", "~/.local/share/vragecage-worker")).expanduser().resolve()
    incoming = (worker_root / "incoming").resolve()
    source = Path(args.source).expanduser().resolve()
    try:
        source.relative_to(incoming)
    except ValueError:
        raise SystemExit(f"world source must be beneath {incoming}")
    for required in ("Sandbox.sbc", "Sandbox_config.sbc", "SANDBOX_0_0_0_.sbs"):
        if not (source / required).is_file():
            raise SystemExit(f"world is missing required file: {required}")

    files, size, sha256 = tree_receipt(source)
    env = os.environ.copy()
    env["VRAGECAGE_SOURCE_WORLD"] = str(source)
    script_dir = Path(__file__).resolve().parent
    prepare = script_dir / ("prepare-magnetar-instance.sh" if args.engine == "magnetar" else "prepare-instance.sh")
    result = subprocess.run([str(prepare), args.instance], env=env)
    if result.returncode != 0:
        return result.returncode

    instance = worker_root / "instances" / args.instance
    receipt = {
        "schema": "vragecage.world-import.v1",
        "engine": args.engine,
        "instance": args.instance,
        "file_count": files,
        "byte_count": size,
        "tree_sha256": sha256,
    }
    receipt_path = instance / ".vragecage-world-import.json"
    temporary = receipt_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, receipt_path)
    if args.destructive_lab:
        subprocess.run([str(script_dir / "lab-contract.py"), "create", str(instance)], check=True)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
