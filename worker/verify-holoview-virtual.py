#!/usr/bin/env python3
"""Verify bounded multi-pose grid-only HoloView frames and bind a terminal lab receipt."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def near_vector(actual: object, expected: object, label: str) -> None:
    require(isinstance(actual, list) and len(actual) == 3, f"{label} absent or malformed")
    require(all(isinstance(x, (int, float)) and math.isfinite(x) for x in actual), f"{label} non-finite")
    require(all(abs(a - b) <= 1e-6 for a, b in zip(actual, expected)), f"{label} differs from request")


def verify(folder: Path, request: dict, terminal: dict, terminal_sha256: str) -> dict:
    require(terminal.get("schema") == "vragecage.lab-terminal-receipt.v1", "invalid terminal receipt")
    server = terminal.get("receipt")
    require(isinstance(server, dict), "terminal receipt has no server receipt")
    require(server.get("lab_contract_valid") is True and server.get("lab_authority") == "destructive-lab", "lab authority not verified")
    require(server.get("clean_shutdown") is True and server.get("game_ready") is True and server.get("scripts_loaded") is True, "server run incomplete")
    run_id = terminal.get("run_id")
    require(isinstance(run_id, str) and run_id == server.get("lab_run_id"), "run ID mismatch")
    require(server.get("staged_mod_unchanged") is True, "staged HoloView mod changed during run")
    staged = server.get("staged_mod")
    require(isinstance(staged, dict), "missing staged-mod identity")
    mod_id = staged.get("synthetic_workshop_id")
    require(isinstance(mod_id, int) and folder.name == f"{mod_id}.sbm_HoloView3D", "frame storage does not belong to staged mod")
    game_log = Path(str(server.get("game_log", "")))
    require(game_log.is_file() and folder.is_relative_to(game_log.parent), "frame storage is outside the recorded instance")
    quarantine_run = server.get("quarantine_run")
    require(quarantine_run == f"Quarantine/{run_id}", "invalid quarantine run path")
    run_record = json.loads((game_log.parent / quarantine_run / "run.json").read_text(encoding="utf-8"))
    require(run_record.get("run_id") == run_id and run_record.get("instance") == game_log.parent.name,
            "lab activation record mismatch")
    started_at = datetime.fromisoformat(run_record["started_at"]).timestamp()
    log_bytes = game_log.read_bytes()
    require(hashlib.sha256(log_bytes).hexdigest() == server.get("game_log_sha256"), "game log hash mismatch")
    log_text = log_bytes.decode("utf-8", errors="replace")
    require(isinstance(request.get("poses"), list) and 2 <= len(request["poses"]) <= 4,
            "two to four poses required")
    frames = []
    pixels = []
    for index, pose in enumerate(request["poses"]):
        stem = f"vragecage-virtual-{request['capture_id']}-{index}"
        metadata = json.loads((folder / f"{stem}.json").read_text(encoding="utf-8"))
        require(metadata.get("schema") == "vragecage.virtual-holoview.v1", "unexpected frame schema")
        require(metadata.get("source_kind") == "virtual", "frame is not a virtual job")
        require(metadata.get("request_id") == request["request_id"], "request ID mismatch")
        require(metadata.get("world_name_observed") == request["world_name"], "world identity mismatch")
        require(metadata.get("pose_index") == index, "pose index mismatch")
        require(metadata.get("width") == request["width"] and metadata.get("height") == request["height"], "dimensions mismatch")
        require(abs(metadata.get("fov_degrees", float("inf")) - pose["fov_degrees"]) <= 1e-6, "FOV mismatch")
        for field in ("origin", "forward", "up"):
            near_vector(metadata.get(field), pose[field], field)
        require(isinstance(metadata.get("snapshot_tick"), int) and metadata["snapshot_tick"] > 0, "invalid tick")
        require(isinstance(metadata.get("geometry_primitives"), int) and metadata["geometry_primitives"] > 0, "empty geometry")
        marker = (f"[VRageCage] virtual HoloView capture={request['capture_id']} "
                  f"request={request['request_id']} frame={index} tick={metadata['snapshot_tick']}")
        require(log_text.count(marker) == 1, "frame marker absent or duplicated in hashed game log")
        pixels_path = folder / f"{stem}.rgba64.txt"
        metadata_path = folder / f"{stem}.json"
        require(pixels_path.stat().st_mtime >= started_at - 2 and metadata_path.stat().st_mtime >= started_at - 2,
                "frame predates this lab run")
        encoded = pixels_path.read_bytes().strip()
        image = base64.b64decode(encoded, validate=True)
        require(len(image) == request["width"] * request["height"] * 4, "RGBA length mismatch")
        visible_colors = set()
        visible_pixels = 0
        for offset in range(0, len(image), 4):
            red, green, blue, alpha = image[offset:offset + 4]
            if alpha >= 128 and max(red, green, blue) >= 16:
                visible_pixels += 1
                visible_colors.add((red, green, blue))
        require(visible_pixels >= 100 and len(visible_colors) >= 4,
                "frame is transparent, blank, or has too little visible color variation")
        pixels.append(image)
        frames.append({
            "pose_index": index,
            "snapshot_tick": metadata["snapshot_tick"],
            "actual_pose": {field: metadata[field] for field in ("origin", "forward", "up")},
            "fov_degrees": metadata["fov_degrees"],
            "width": metadata["width"],
            "height": metadata["height"],
            "geometry_primitives": metadata["geometry_primitives"],
            "visible_pixels": visible_pixels,
            "unique_visible_colors": len(visible_colors),
            "rgba_sha256": hashlib.sha256(image).hexdigest(),
            "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        })
    pairwise_changed_pixels = {}
    for first in range(len(pixels)):
        for second in range(first + 1, len(pixels)):
            changed_pixels = sum(a != b for a, b in zip(
                (pixels[first][i:i + 4] for i in range(0, len(pixels[first]), 4)),
                (pixels[second][i:i + 4] for i in range(0, len(pixels[second]), 4)),
            ))
            require(changed_pixels >= 100, f"poses {first} and {second} produced effectively identical images")
            pairwise_changed_pixels[f"{first}-{second}"] = changed_pixels
    return {
        "schema": "vragecage.virtual-holoview-verification.v1",
        "request_id": request["request_id"],
        "capture_id": request["capture_id"],
        "world_name": request["world_name"],
        "lab_run_id": run_id,
        "terminal_receipt_sha256": terminal_sha256,
        "frames": frames,
        "pairwise_changed_pixels": pairwise_changed_pixels,
        "passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("request", type=Path)
    parser.add_argument("terminal_receipt", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    terminal_bytes = args.terminal_receipt.read_bytes()
    terminal = json.loads(terminal_bytes)
    print(json.dumps(verify(args.folder, request, terminal, hashlib.sha256(terminal_bytes).hexdigest()), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
