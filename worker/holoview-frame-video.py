#!/usr/bin/env python3
"""Validate HoloView HVRAW1 frames, preserve per-frame receipts, and encode a VFR MP4."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path


TICKS_PER_SECOND = 60.0
PROVENANCE = "HoloView 3D software raster RGBA; not native framebuffer or simulator ground truth"


@dataclass(frozen=True)
class Frame:
    path: Path
    width: int
    height: int
    camera_id: int
    output_id: int
    surface_index: int
    presentation_width: int
    presentation_height: int
    snapshot_tick: int
    fov_degrees: float
    position: tuple[float, float, float]
    forward: tuple[float, float, float]
    pixels: bytes
    sha256: str


def read_frame(path: Path, max_file_bytes: int) -> Frame:
    size = path.stat().st_size
    if size < 1 or size > max_file_bytes:
        raise ValueError(f"{path.name}: file size {size} is outside 1..{max_file_bytes} bytes")
    raw = path.read_bytes()
    lines = raw.decode("ascii").splitlines()
    if len(lines) != 2:
        raise ValueError(f"{path.name}: expected exactly one HVRAW1 header and one RGBA payload line")
    fields = lines[0].split("|")
    if len(fields) != 16 or fields[0] != "HVRAW1":
        raise ValueError(f"{path.name}: unsupported HVRAW header")
    try:
        width, height = int(fields[1]), int(fields[2])
        camera_id, output_id, surface_index = int(fields[3]), int(fields[4]), int(fields[5])
        presentation_width, presentation_height = int(fields[6]), int(fields[7])
        snapshot_tick = int(fields[8])
        fov = float(fields[9])
        position = (float(fields[10]), float(fields[11]), float(fields[12]))
        forward = (float(fields[13]), float(fields[14]), float(fields[15]))
        rgba = base64.b64decode(lines[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{path.name}: malformed numeric or base64 payload") from exc
    if not 1 <= width <= 2048 or not 1 <= height <= 2048:
        raise ValueError(f"{path.name}: dimensions outside 1..2048")
    if camera_id <= 0 or output_id <= 0 or surface_index < 0 or snapshot_tick < 0:
        raise ValueError(f"{path.name}: invalid entity identity, surface, or tick")
    if presentation_width <= 0 or presentation_height <= 0:
        raise ValueError(f"{path.name}: invalid presentation dimensions")
    if not math.isfinite(fov) or not 0.0 < fov < 180.0:
        raise ValueError(f"{path.name}: invalid FOV")
    if not all(math.isfinite(value) for value in (*position, *forward)):
        raise ValueError(f"{path.name}: non-finite camera pose")
    if len(rgba) != width * height * 4:
        raise ValueError(f"{path.name}: expected {width * height * 4} RGBA bytes, found {len(rgba)}")
    return Frame(
        path=path,
        width=width,
        height=height,
        camera_id=camera_id,
        output_id=output_id,
        surface_index=surface_index,
        presentation_width=presentation_width,
        presentation_height=presentation_height,
        snapshot_tick=snapshot_tick,
        fov_degrees=fov,
        position=position,
        forward=forward,
        pixels=rgba,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def read_scene_metadata(path: Path, frame: Frame, capture_id: str) -> dict[str, object] | None:
    scene_path = path.with_name(path.name[:-len(".hvraw.txt")] + ".scene.json")
    if not scene_path.exists():
        return None
    if scene_path.stat().st_size < 1 or scene_path.stat().st_size > 512 * 1024:
        raise ValueError(f"{scene_path.name}: scene metadata exceeds the 512 KiB safety bound")
    try:
        metadata = json.loads(scene_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{scene_path.name}: invalid scene metadata JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"{scene_path.name}: scene metadata root must be an object")
    try:
        frame_sequence = int(path.name.rsplit("-", 1)[1].removesuffix(".hvraw.txt"))
    except (IndexError, ValueError) as exc:
        raise ValueError(f"{path.name}: capture filename has no numeric frame sequence") from exc
    if metadata.get("schema") != "vragecage.holoview-scene-metadata.v1":
        raise ValueError(f"{scene_path.name}: unsupported scene metadata schema")
    expected = {
        "capture_id": capture_id,
        "associated_raw_frame_file": path.name,
        "camera_entity_id": frame.camera_id,
        "lcd_output_entity_id": frame.output_id,
        "surface_index": frame.surface_index,
        "snapshot_tick": frame.snapshot_tick,
        "frame_sequence": frame_sequence,
    }
    for field, value in expected.items():
        if metadata.get(field) != value:
            raise ValueError(f"{scene_path.name}: {field} does not match its raw frame")
    objects = metadata.get("objects")
    summary = metadata.get("summary")
    if not isinstance(objects, list) or not isinstance(summary, dict):
        raise ValueError(f"{scene_path.name}: missing bounded scene objects or summary")
    if len(objects) > 512:
        raise ValueError(f"{scene_path.name}: object count exceeds the supported safety bound")
    object_limit = summary.get("object_limit")
    if not isinstance(object_limit, int) or not 1 <= object_limit <= 512 or len(objects) > object_limit:
        raise ValueError(f"{scene_path.name}: object list exceeds its declared bound")
    if any(not isinstance(item, dict) for item in objects):
        raise ValueError(f"{scene_path.name}: scene object entries must be objects")
    if metadata.get("scene_semantics") != "renderer_frustum_candidates":
        raise ValueError(f"{scene_path.name}: unexpected scene semantics")
    if summary.get("object_count") != len(objects) or not isinstance(summary.get("truncated"), bool):
        raise ValueError(f"{scene_path.name}: scene summary does not match its serialized objects")
    return {
        "file": scene_path.name,
        "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest(),
        "scene_semantics": metadata["scene_semantics"],
        "provenance": metadata.get("provenance"),
        "object_count": len(objects),
        "summary": summary,
    }


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    row_bytes = width * 4
    scanlines = b"".join(b"\x00" + rgba[row * row_bytes:(row + 1) * row_bytes]
                         for row in range(height))
    image = (b"\x89PNG\r\n\x1a\n" +
             png_chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0)) +
             png_chunk(b"IDAT", zlib.compress(scanlines, level=6)) +
             png_chunk(b"IEND", b""))
    path.write_bytes(image)


def ffconcat_quote(path: Path) -> str:
    return "'" + str(path.resolve()).replace("\\", "\\\\").replace("'", "'\\''") + "'"


def convert(input_dir: Path, output: Path, args: argparse.Namespace) -> dict[str, object]:
    input_dir = input_dir.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"input is not a directory: {input_dir}")
    if output.exists():
        raise SystemExit(f"refusing to overwrite video: {output}")
    frame_dir = output.with_name(output.stem + "-frames")
    if frame_dir.exists():
        raise SystemExit(f"refusing to overwrite frame directory: {frame_dir}")
    paths = sorted(input_dir.glob("vragecage-hv-*.hvraw.txt"))
    if not paths:
        raise SystemExit(f"no vragecage-hv-*.hvraw.txt files found in {input_dir}")
    if len(paths) > args.max_frames:
        raise SystemExit(f"frame count {len(paths)} exceeds limit {args.max_frames}")
    total_bytes = sum(path.stat().st_size for path in paths)
    if total_bytes > args.max_bytes:
        raise SystemExit(f"input bytes {total_bytes} exceeds limit {args.max_bytes}")

    frames = sorted((read_frame(path, args.max_bytes) for path in paths), key=lambda item: item.snapshot_tick)
    capture_ids = set()
    for path in paths:
        if not path.name.startswith("vragecage-hv-") or not path.name.endswith(".hvraw.txt"):
            raise SystemExit(f"unsupported capture filename: {path.name}")
        capture_id = path.name[len("vragecage-hv-"):-len(".hvraw.txt")].rsplit("-", 1)[0]
        if not capture_id:
            raise SystemExit(f"capture filename has no capture ID: {path.name}")
        capture_ids.add(capture_id)
    if len(capture_ids) != 1:
        raise SystemExit("input frames mix capture IDs")
    capture_id = next(iter(capture_ids))
    first = frames[0]
    if len(frames) < 2:
        raise SystemExit("at least two distinct simulation-tick frames are required for a video")
    identity = (first.camera_id, first.output_id, first.surface_index, first.width, first.height)
    previous_tick = -1
    for frame in frames:
        if (frame.camera_id, frame.output_id, frame.surface_index, frame.width, frame.height) != identity:
            raise SystemExit("frames mix camera/output/surface identities or raster dimensions")
        if frame.snapshot_tick <= previous_tick:
            raise SystemExit("snapshot ticks must be unique and strictly increasing")
        previous_tick = frame.snapshot_tick
    scene_metadata_by_raw = {
        frame.path.name: read_scene_metadata(frame.path, frame, capture_id) for frame in frames
    }
    scene_metadata_count = sum(value is not None for value in scene_metadata_by_raw.values())
    if scene_metadata_count not in (0, len(frames)):
        raise SystemExit("input capture has scene metadata for only some frames")

    ffmpeg = shutil.which(args.ffmpeg)
    if not ffmpeg:
        raise SystemExit(f"ffmpeg not found: {args.ffmpeg}")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_dir.mkdir()
    frame_names: list[str] = []
    for index, frame in enumerate(frames, start=1):
        name = f"frame-{index:05d}.png"
        write_png(frame_dir / name, frame.width, frame.height, frame.pixels)
        frame_names.append(name)

    durations = [
        (frames[index + 1].snapshot_tick - frame.snapshot_tick) / TICKS_PER_SECOND
        for index, frame in enumerate(frames[:-1])
    ]
    durations.append(durations[-1] if durations else 1.0)
    concat_path = frame_dir / ".capture.ffconcat"
    lines = ["ffconcat version 1.0"]
    for name, duration in zip(frame_names, durations):
        lines.extend((f"file {ffconcat_quote(frame_dir / name)}", f"duration {duration:.9f}"))
    # The concat demuxer needs the final file repeated to honor its final duration.
    lines.append(f"file {ffconcat_quote(frame_dir / frame_names[-1])}")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    manifest: dict[str, object] = {
        "schema": "vragecage.holoview-software-video.v1",
        "provenance": PROVENANCE,
        "capture_id": capture_id,
        "instance": args.instance,
        "server_run_id": args.run_id,
        "server_terminal_receipt_sha256": args.server_receipt_sha256,
        "source_tick_rate_assumption": TICKS_PER_SECOND,
        "source_camera_entity_id": first.camera_id,
        "lcd_output_entity_id": first.output_id,
        "surface_index": first.surface_index,
        "raster_width": first.width,
        "raster_height": first.height,
        "display_width": first.presentation_width,
        "display_height": first.presentation_height,
        "frame_count": len(frames),
        "scene_metadata_frame_count": 0,
        "duration_seconds_from_simulation_ticks": (frames[-1].snapshot_tick - first.snapshot_tick + durations[-1] * TICKS_PER_SECOND) / TICKS_PER_SECOND,
        "frames": [],
    }
    frame_entries = []
    for index, (frame, name, duration) in enumerate(zip(frames, frame_names, durations)):
        scene_metadata = scene_metadata_by_raw[frame.path.name]
        if scene_metadata is not None:
            manifest["scene_metadata_frame_count"] += 1
        frame_entries.append({
            "sequence": index + 1,
            "raw_file": frame.path.name,
            "raw_sha256": frame.sha256,
            "png_file": name,
            "png_sha256": hashlib.sha256((frame_dir / name).read_bytes()).hexdigest(),
            "snapshot_tick": frame.snapshot_tick,
            "elapsed_simulation_seconds": (frame.snapshot_tick - first.snapshot_tick) / TICKS_PER_SECOND,
            "held_for_seconds": duration,
            "display_width": frame.presentation_width,
            "display_height": frame.presentation_height,
            "camera_position": frame.position,
            "camera_forward": frame.forward,
            "fov_degrees": frame.fov_degrees,
            "scene_metadata": scene_metadata,
        })
    manifest["frames"] = frame_entries
    (frame_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-fps_mode", "vfr", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SystemExit(f"ffmpeg failed ({result.returncode}): {result.stderr.strip()}")
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    manifest["video_file"] = output.name
    manifest["video_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    (frame_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"video": str(output), "frames_directory": str(frame_dir), "frame_count": len(frames), "sha256": manifest["video_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new MP4 path; existing files are never overwritten")
    parser.add_argument("--max-frames", type=int, default=120)
    parser.add_argument("--max-bytes", type=int, default=128 * 1024 * 1024)
    parser.add_argument("--instance", help="disposable lab instance name for the output manifest")
    parser.add_argument("--run-id", help="VrageCage server run ID for the output manifest")
    parser.add_argument("--server-receipt-sha256", help="terminal server receipt hash for correlation")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    if not 2 <= args.max_frames <= 600:
        parser.error("max-frames must be 2..600")
    if not 4096 <= args.max_bytes <= 1024 * 1024 * 1024:
        parser.error("max-bytes must be 4 KiB..1 GiB")
    if args.server_receipt_sha256 is not None and (
        len(args.server_receipt_sha256) != 64 or
        any(character not in "0123456789abcdef" for character in args.server_receipt_sha256)
    ):
        parser.error("server-receipt-sha256 must be 64 lowercase hexadecimal characters")
    print(json.dumps(convert(args.input_dir, args.output, args), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
