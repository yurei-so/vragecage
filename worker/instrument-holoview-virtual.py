#!/usr/bin/env python3
"""Stage a bounded spectator view or multi-pose grid-only HoloView proof in a stopped disposable instance.

This does not alter the source mod, checkpoint, or live service. The staged mod still
needs to be selected by a disposable-world test harness before it can compile/run.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from xml.etree import ElementTree


HERE = Path(__file__).resolve().parent
SCRIPT_DIR = Path("Data/Scripts/HoloView3D")


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected one source anchor, found {count}")
    return source.replace(old, new, 1)


def identifier(value: str, label: str) -> str:
    if not value or len(value) > 96 or not all(c.isalnum() or c in "-_" for c in value):
        raise ValueError(f"{label} must be 1-96 ASCII letters, digits, dashes, or underscores")
    if not value.isascii():
        raise ValueError(f"{label} must be ASCII")
    return value


def vector(value: object, label: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must be a three-number array")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError(f"{label} contains a non-finite or non-numeric value")
    return tuple(float(x) for x in value)


def normalize_pose(pose: object) -> dict:
    if not isinstance(pose, dict) or set(pose) != {"origin", "forward", "up", "fov_degrees"}:
        raise ValueError("each pose requires exactly origin, forward, up, fov_degrees")
    origin = vector(pose["origin"], "origin")
    forward = vector(pose["forward"], "forward")
    up = vector(pose["up"], "up")
    if any(abs(x) > 1e8 for x in origin):
        raise ValueError("origin is outside the bounded world-space range")
    fn = math.sqrt(sum(x * x for x in forward))
    un = math.sqrt(sum(x * x for x in up))
    if not 0.999 <= fn <= 1.001 or not 0.999 <= un <= 1.001:
        raise ValueError("forward and up must be unit vectors")
    if abs(sum(a * b for a, b in zip(forward, up))) > 0.001:
        raise ValueError("forward and up must be perpendicular")
    fov = pose["fov_degrees"]
    if isinstance(fov, bool) or not isinstance(fov, (int, float)) or not math.isfinite(fov) or not 25 <= fov <= 100:
        raise ValueError("fov_degrees must be between 25 and 100")
    return {"origin": origin, "forward": forward, "up": up, "fov_degrees": float(fov)}


def cs_vector(v: tuple[float, float, float]) -> str:
    return "new Vector3D(" + ", ".join(format(x, ".17g") + "d" for x in v) + ")"


def build_template(config: dict) -> str:
    template = (HERE / "holoview-virtual/VirtualCapture.cs.tmpl").read_text(encoding="utf-8")
    values = {
        "@@WORLD@@": config["world_name"],
        "@@REQUEST@@": config["request_id"],
        "@@CAPTURE@@": config["capture_id"],
        "@@WIDTH@@": str(config["width"]),
        "@@HEIGHT@@": str(config["height"]),
        "@@RANGE@@": format(config["range_m"], ".17g") + "d",
        "@@ALLOW_TERRAIN_OMISSION@@": "true" if config.get("allow_terrain_omission", False) else "false",
        "@@ORIGINS@@": ", ".join(cs_vector(p["origin"]) for p in config["poses"]),
        "@@FORWARDS@@": ", ".join(cs_vector(p["forward"]) for p in config["poses"]),
        "@@UPS@@": ", ".join(cs_vector(p["up"]) for p in config["poses"]),
        "@@FOVS@@": ", ".join(format(p["fov_degrees"], ".17g") + "d" for p in config["poses"]),
    }
    for old, new in values.items():
        template = template.replace(old, new)
    return template


def patch_sources(session: str, worker: str) -> tuple[str, str]:
    session = replace_once(
        session,
        "            if (MyAPIGateway.Utilities != null && MyAPIGateway.Utilities.IsDedicated)\n                return;\n            ticks++;\n",
        "            if (MyAPIGateway.Utilities != null && MyAPIGateway.Utilities.IsDedicated)\n"
        "            {\n                ticks++;\n                VrageCageTickVirtualCapture();\n                ConsumeCompletedWorkerFrames();\n                return;\n            }\n"
        "            ticks++;\n            VrageCageTickVirtualCapture();\n",
        "virtual update hook",
    )
    session = replace_once(
        session,
        "        void BuildStableRasterBlockList(MatrixD cam)\n        {\n",
        "        void BuildStableRasterBlockList(MatrixD cam)\n        {\n            BuildStableRasterBlockList(cam, proxies);\n        }\n\n"
        "        void BuildStableRasterBlockList(MatrixD cam, Dictionary<long, GridProxy> sourceProxies)\n        {\n",
        "virtual grid cache overload",
    )
    block_start = session.index("        void BuildStableRasterBlockList(MatrixD cam, Dictionary<long, GridProxy> sourceProxies)")
    block_end = session.index("        int GetRasterDetailPercent(", block_start)
    block = session[block_start:block_end]
    block = replace_once(
        block,
        "            foreach (KeyValuePair<long, GridProxy> pair in proxies)\n",
        "            foreach (KeyValuePair<long, GridProxy> pair in sourceProxies)\n",
        "virtual grid cache source",
    )
    session = session[:block_start] + block + session[block_end:]
    session = replace_once(
        session,
        "        void PrepareStableRasterBlockScene(MatrixD cam)\n        {\n            BuildStableRasterBlockList(cam);",
        "        void PrepareStableRasterBlockScene(MatrixD cam)\n        {\n            PrepareStableRasterBlockScene(cam, proxies);\n        }\n\n"
        "        void PrepareStableRasterBlockScene(MatrixD cam, Dictionary<long, GridProxy> sourceProxies)\n"
        "        {\n            BuildStableRasterBlockList(cam, sourceProxies);",
        "virtual scene preparation overload",
    )
    if "public int VirtualIndex = -1;" not in worker:
        worker = replace_once(
            worker, "            public long OutputId;\n",
            "            public long OutputId;\n            public int VirtualIndex = -1;\n",
            "virtual job identity",
        )
    if "            job.OutputId = output.EntityId;\n            job.VirtualIndex = -1;" not in worker:
        worker = replace_once(
            worker, "            job.OutputId = output.EntityId;\n",
            "            job.OutputId = output.EntityId;\n            job.VirtualIndex = -1;\n",
            "physical job preservation",
        )
    worker = replace_once(
        worker,
        "            if (job == null)\n                return;\n\n            LcdSurfaceKey completedKey",
        "            if (job == null)\n                return;\n            if (job.VirtualIndex >= 0)\n            {\n                VrageCageCompleteVirtualFrame(job);\n                job.VirtualIndex = -1;\n                return;\n            }\n\n            LcdSurfaceKey completedKey",
        "virtual completion branch",
    )
    return session, worker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("source_mod", type=Path)
    parser.add_argument("config", type=Path, help="JSON one-to-four-pose spectator view request")
    parser.add_argument("--stage-name", required=True)
    args = parser.parse_args()
    instance = args.instance.resolve(strict=True)
    source = args.source_mod.resolve(strict=True)
    if not source.is_relative_to(instance):
        raise SystemExit("source mod must be inside the disposable instance")
    marker = json.loads((instance / ".vragecage-lab.json").read_text(encoding="utf-8"))
    if not marker.get("disposable") or marker.get("authority") != "destructive-lab":
        raise SystemExit("instance is not a disposable destructive-lab copy")
    if not (instance / ".vragecage-world-import.json").is_file() or (instance / ".vragecage-engine").read_text().strip() != "magnetar":
        raise SystemExit("instance is not an imported Magnetar lab copy")
    unit = f"vragecage-{instance.name}.service"
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], check=False).returncode == 0:
        raise SystemExit(f"refusing to stage while {unit} is running")
    stage_name = identifier(args.stage_name, "stage_name")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    required = {"world_name", "request_id", "capture_id", "width", "height", "range_m", "poses"}
    if set(config) not in (required, required | {"allow_terrain_omission"}):
        raise SystemExit("request has missing or unsupported fields")
    if "allow_terrain_omission" in config and type(config["allow_terrain_omission"]) is not bool:
        raise SystemExit("allow_terrain_omission must be a boolean")
    for field in ("world_name", "request_id", "capture_id"):
        config[field] = identifier(config[field], field)
    world_name = ElementTree.parse(instance / "World/Sandbox.sbc").getroot().findtext("SessionName")
    if config["world_name"] != world_name:
        raise SystemExit(f"world name mismatch: requested {config['world_name']}, fixture has {world_name}")
    if config["width"] != 256 or config["height"] != 256:
        raise SystemExit("first proof supports exactly 256x256 pixels")
    if isinstance(config["range_m"], bool) or not isinstance(config["range_m"], (int, float)) or not 10 <= config["range_m"] <= 512:
        raise SystemExit("range_m must be within 10..512")
    if not isinstance(config["poses"], list) or not 1 <= len(config["poses"]) <= 4:
        raise SystemExit("virtual view requires one to four poses")
    config["poses"] = [normalize_pose(p) for p in config["poses"]]
    if any(path.is_symlink() for path in source.rglob("*")):
        raise SystemExit("source mod contains a symlink")
    script_root = source / SCRIPT_DIR
    session = (script_root / "BoundedHoloCamSession.cs").read_text(encoding="utf-8")
    worker = (script_root / "BoundedHoloCamWorker.cs").read_text(encoding="utf-8")
    session, worker = patch_sources(session, worker)
    stage = instance / "local-mods" / stage_name
    if stage.exists() or stage.is_symlink():
        raise SystemExit(f"stage already exists: {stage}")
    # No source-world files are modified; this is an isolated local mod copy.
    stage.parent.mkdir(exist_ok=True)
    if stage.parent.is_symlink():
        raise SystemExit("local-mods directory must not be a symlink")
    temporary = stage.parent / f".{stage_name}.tmp-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary, symlinks=False)
        target = temporary / SCRIPT_DIR
        (target / "BoundedHoloCamSession.cs").write_text(session, encoding="utf-8")
        (target / "BoundedHoloCamWorker.cs").write_text(worker, encoding="utf-8")
        (target / "VirtualCapture.cs").write_text(build_template(config), encoding="utf-8")
        (temporary / ".vragecage-virtual-request.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, stage)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    print(json.dumps({"staged_mod": str(stage), "capture_id": config["capture_id"], "world_name": config["world_name"]}))


if __name__ == "__main__":
    main()
