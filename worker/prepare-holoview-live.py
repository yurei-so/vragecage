#!/usr/bin/env python3
"""Prepare a disposable HoloView package with the bounded live mailbox renderer.

This copies an already virtual-instrumented HoloView package and replaces only its
one-shot pose-list partial class. The physical camera/LCD code, scheduling and
rasterizer remain from the source package. Registration/replacement is separate
and must use the guarded vragecage fixture commands.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = Path("Data/Scripts/HoloView3D")


def prepare(source: Path, destination: Path) -> dict[str, str]:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if not (source / "Data").is_dir():
        raise ValueError("source is not a mod package")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("source package contains a symlink")
    session = source / SCRIPT / "BoundedHoloCamSession.cs"
    worker = source / SCRIPT / "BoundedHoloCamWorker.cs"
    virtual = source / SCRIPT / "VirtualCapture.cs"
    if not all(path.is_file() for path in (session, worker, virtual)):
        raise ValueError("source must contain the previously verified virtual-camera HoloView proof")
    session_text = session.read_text(encoding="utf-8")
    worker_text = worker.read_text(encoding="utf-8")
    if "VrageCageTickVirtualCapture();" not in session_text:
        raise ValueError("HoloView update hook for virtual captures is absent")
    if "VrageCageCompleteVirtualFrame(job);" not in worker_text or "public int VirtualIndex = -1;" not in worker_text:
        raise ValueError("HoloView virtual worker completion hook is absent")
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination already exists; refusing to overwrite")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=False)
    template = (HERE / "holoview-virtual/LiveCapture.cs.tmpl").read_text(encoding="utf-8")
    target = destination / SCRIPT / "VirtualCapture.cs"
    target.write_text(template, encoding="utf-8")
    return {
        "package": str(destination),
        "replaced_file": target.relative_to(destination).as_posix(),
        "preserved_physical_path": "BoundedHoloCamSession.cs and BoundedHoloCamWorker.cs unchanged",
    }


def main() -> None:
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_mod", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source_mod, args.destination), sort_keys=True))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
