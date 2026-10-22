#!/usr/bin/env python3
"""Join two bounded HoloView RGB streams by exact simulation tick and encode side by side."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


VIDEO_MODULE_PATH = Path(__file__).with_name("holoview-frame-video.py")
VIDEO_SPEC = importlib.util.spec_from_file_location("vragecage_holoview_video", VIDEO_MODULE_PATH)
if VIDEO_SPEC is None or VIDEO_SPEC.loader is None:
    raise RuntimeError(f"cannot load HoloView frame validator: {VIDEO_MODULE_PATH}")
video = importlib.util.module_from_spec(VIDEO_SPEC)
sys.modules[VIDEO_SPEC.name] = video
VIDEO_SPEC.loader.exec_module(video)


def parse_pair(value: str) -> tuple[int, int]:
    try:
        camera_id, output_id = (int(part) for part in value.split(":", 1))
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("pair must be CAMERA:OUTPUT using integer entity IDs") from error
    if camera_id <= 0 or output_id <= 0:
        raise argparse.ArgumentTypeError("camera and output entity IDs must be positive")
    return camera_id, output_id


def capture_identity(path: Path) -> tuple[str, int]:
    prefix = "vragecage-hv-"
    suffix = ".hvraw.txt"
    if not path.name.startswith(prefix) or not path.name.endswith(suffix):
        raise ValueError(f"unsupported raw frame name: {path.name}")
    try:
        capture_id, sequence = path.name[len(prefix):-len(suffix)].rsplit("-", 1)
        return capture_id, int(sequence)
    except (ValueError, IndexError) as error:
        raise ValueError(f"raw frame has no numeric sequence: {path.name}") from error


def require_stable_stream(frames: list[object], pair: tuple[int, int]) -> list[object]:
    if not frames:
        raise SystemExit(f"no raw frames found for camera/output pair {pair[0]}:{pair[1]}")
    frames = sorted(frames, key=lambda frame: frame.snapshot_tick)
    first = frames[0]
    identity = (first.camera_id, first.output_id, first.surface_index, first.width, first.height)
    previous_tick = -1
    for frame in frames:
        current = (frame.camera_id, frame.output_id, frame.surface_index, frame.width, frame.height)
        if current != identity:
            raise SystemExit(f"camera/output pair {pair[0]}:{pair[1]} changed surface or raster dimensions")
        if frame.snapshot_tick <= previous_tick:
            raise SystemExit(f"camera/output pair {pair[0]}:{pair[1]} has duplicate or unordered ticks")
        previous_tick = frame.snapshot_tick
    return frames


def concat_quote(path: Path) -> str:
    return "'" + str(path.resolve()).replace("\\", "\\\\").replace("'", "'\\''") + "'"


def duration_list(frames: list[object]) -> list[float]:
    intervals = [
        (frames[index + 1].snapshot_tick - frame.snapshot_tick) / video.TICKS_PER_SECOND
        for index, frame in enumerate(frames[:-1])
    ]
    intervals.append(intervals[-1] if intervals else 1.0)
    return intervals


def write_concat(path: Path, pngs: list[Path], durations: list[float]) -> None:
    lines = ["ffconcat version 1.0"]
    for png, duration in zip(pngs, durations):
        lines.extend((f"file {concat_quote(png)}", f"duration {duration:.9f}"))
    lines.append(f"file {concat_quote(pngs[-1])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_font(value: str | None) -> Path:
    if value:
        font = Path(value).expanduser().resolve(strict=True)
        if not font.is_file():
            raise SystemExit(f"font is not a regular file: {font}")
        return font
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    fc_match = shutil.which("fc-match")
    if fc_match:
        result = subprocess.run([fc_match, "-f", "%{file}", "DejaVu Sans Mono"],
                                capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip() and Path(result.stdout.strip()).is_file():
            return Path(result.stdout.strip())
    raise SystemExit("could not find a font for the side-by-side video labels; pass --font")


def drawtext_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def compose(input_dir: Path, output: Path, args: argparse.Namespace) -> dict[str, object]:
    input_dir = input_dir.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"input is not a directory: {input_dir}")
    manifest_path = output.with_suffix(".manifest.json")
    frame_dir = output.with_name(output.stem + "-frames")
    if output.exists() or manifest_path.exists() or frame_dir.exists():
        raise SystemExit("refusing to overwrite a video, manifest, or frame directory")
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,48}", args.left_label):
        raise SystemExit("left label may contain only ASCII letters, digits, spaces, underscores, and dashes")
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,48}", args.right_label):
        raise SystemExit("right label may contain only ASCII letters, digits, spaces, underscores, and dashes")
    if args.pair_left == args.pair_right:
        raise SystemExit("left and right camera/output pairs must be different")

    paths = sorted(input_dir.glob("vragecage-hv-*.hvraw.txt"))
    if not paths:
        raise SystemExit(f"no vragecage-hv-*.hvraw.txt files found in {input_dir}")
    if len(paths) > args.max_frames * 2:
        raise SystemExit(f"raw frame count {len(paths)} exceeds the {args.max_frames * 2} frame total bound")
    input_bytes = sum(path.stat().st_size for path in paths)
    if input_bytes > args.max_bytes:
        raise SystemExit(f"raw input bytes {input_bytes} exceeds limit {args.max_bytes}")

    capture_ids: set[str] = set()
    sequences: dict[str, set[int]] = {}
    groups: dict[tuple[int, int], list[object]] = {args.pair_left: [], args.pair_right: []}
    raw_by_tick: dict[tuple[int, int, int], tuple[Path, str, int]] = {}
    for path in paths:
        capture_id, sequence = capture_identity(path)
        capture_ids.add(capture_id)
        sequence_set = sequences.setdefault(capture_id, set())
        if sequence in sequence_set:
            raise SystemExit(f"duplicate frame sequence in capture: {path.name}")
        sequence_set.add(sequence)
        frame = video.read_frame(path, args.max_bytes)
        pair = (frame.camera_id, frame.output_id)
        if pair not in groups:
            raise SystemExit(f"input contains an unrequested camera/output pair: {pair[0]}:{pair[1]}")
        tick_key = (frame.snapshot_tick, pair[0], pair[1])
        if tick_key in raw_by_tick:
            raise SystemExit(f"duplicate simulation tick for {path.name}")
        groups[pair].append(frame)
        raw_by_tick[tick_key] = (path, capture_id, sequence)
    if len(capture_ids) != 1:
        raise SystemExit("input frames mix capture IDs")
    capture_id = next(iter(capture_ids))
    left = require_stable_stream(groups[args.pair_left], args.pair_left)
    right = require_stable_stream(groups[args.pair_right], args.pair_right)
    for frames in (left, right):
        if len(frames) > args.max_frames:
            raise SystemExit(f"per-view frame count {len(frames)} exceeds limit {args.max_frames}")
    left_by_tick = {frame.snapshot_tick: frame for frame in left}
    right_by_tick = {frame.snapshot_tick: frame for frame in right}
    common_ticks = sorted(set(left_by_tick) & set(right_by_tick))
    if len(common_ticks) < 2:
        raise SystemExit(f"need at least two exactly matching simulation ticks; found {len(common_ticks)}")
    paired_left = [left_by_tick[tick] for tick in common_ticks]
    paired_right = [right_by_tick[tick] for tick in common_ticks]
    metadata_presence = {args.pair_left: [], args.pair_right: []}
    for pair_frames in (paired_left, paired_right):
        for frame in pair_frames:
            raw_info = raw_by_tick[(frame.snapshot_tick, frame.camera_id, frame.output_id)]
            metadata_presence[(frame.camera_id, frame.output_id)].append(
                video.read_scene_metadata(raw_info[0], frame, capture_id) is not None
            )
    if any(any(values) and not all(values) for values in metadata_presence.values()):
        raise SystemExit("scene metadata is present for only some synchronized frames in a view")

    ffmpeg = shutil.which(args.ffmpeg)
    if not ffmpeg:
        raise SystemExit(f"ffmpeg not found: {args.ffmpeg}")
    font = resolve_font(args.font)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_dir.mkdir()
    left_dir = frame_dir / "left"
    right_dir = frame_dir / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    left_pngs: list[Path] = []
    right_pngs: list[Path] = []
    frame_entries: list[dict[str, object]] = []
    for index, tick in enumerate(common_ticks, start=1):
        left_frame = left_by_tick[tick]
        right_frame = right_by_tick[tick]
        left_name = f"frame-{index:05d}.png"
        right_name = f"frame-{index:05d}.png"
        left_png = left_dir / left_name
        right_png = right_dir / right_name
        video.write_png(left_png, left_frame.width, left_frame.height, left_frame.pixels)
        video.write_png(right_png, right_frame.width, right_frame.height, right_frame.pixels)
        left_pngs.append(left_png)
        right_pngs.append(right_png)
        left_raw = raw_by_tick[(tick, args.pair_left[0], args.pair_left[1])]
        right_raw = raw_by_tick[(tick, args.pair_right[0], args.pair_right[1])]
        frame_entries.append({
            "sequence": index,
            "snapshot_tick": tick,
            "elapsed_simulation_seconds": (tick - common_ticks[0]) / video.TICKS_PER_SECOND,
            "left": {
                "raw_file": left_raw[0].name,
                "raw_sha256": left_frame.sha256,
                "png_file": f"left/{left_name}",
                "camera_entity_id": left_frame.camera_id,
                "lcd_output_entity_id": left_frame.output_id,
                "surface_index": left_frame.surface_index,
                "raster": [left_frame.width, left_frame.height],
                "display_size": [left_frame.presentation_width, left_frame.presentation_height],
                "fov_degrees": left_frame.fov_degrees,
                "camera_position_world": left_frame.position,
                "camera_forward_world": left_frame.forward,
            },
            "right": {
                "raw_file": right_raw[0].name,
                "raw_sha256": right_frame.sha256,
                "png_file": f"right/{right_name}",
                "camera_entity_id": right_frame.camera_id,
                "lcd_output_entity_id": right_frame.output_id,
                "surface_index": right_frame.surface_index,
                "raster": [right_frame.width, right_frame.height],
                "display_size": [right_frame.presentation_width, right_frame.presentation_height],
                "fov_degrees": right_frame.fov_degrees,
                "camera_position_world": right_frame.position,
                "camera_forward_world": right_frame.forward,
            },
        })

    durations = duration_list(paired_left)
    left_concat = frame_dir / ".left.ffconcat"
    right_concat = frame_dir / ".right.ffconcat"
    write_concat(left_concat, left_pngs, durations)
    write_concat(right_concat, right_pngs, durations)
    target_height = max(64, max(max(frame.height for frame in paired_left), max(frame.height for frame in paired_right)))
    if target_height % 2:
        target_height += 1
    left_width = max(2, round(paired_left[0].width * target_height / paired_left[0].height))
    if left_width % 2:
        left_width += 1
    font_path = drawtext_escape(font)
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,scale=-2:{target_height}[left];"
        f"[1:v]setpts=PTS-STARTPTS,scale=-2:{target_height}[right];"
        "[left][right]hstack=inputs=2:shortest=1,"
        "drawbox=x=0:y=0:w=iw:h=36:color=black@0.88:t=fill,"
        f"drawbox=x={left_width}:y=0:w=3:h=ih:color=white@0.85:t=fill,"
        f"drawtext=fontfile='{font_path}':text='{args.left_label}':x=12:y=9:fontsize=18:fontcolor=white,"
        f"drawtext=fontfile='{font_path}':text='{args.right_label}':x={left_width + 12}:y=9:fontsize=18:fontcolor=white[out]"
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(left_concat),
        "-f", "concat", "-safe", "0", "-i", str(right_concat),
        "-filter_complex", graph, "-map", "[out]", "-fps_mode", "vfr",
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

    duration = (common_ticks[-1] - common_ticks[0] + durations[-1] * video.TICKS_PER_SECOND) / video.TICKS_PER_SECOND
    manifest: dict[str, object] = {
        "schema": "vragecage.holoview-synchronized-multiview.v1",
        "provenance": video.PROVENANCE,
        "capture_id": capture_id,
        "instance": args.instance,
        "server_run_id": args.run_id,
        "server_terminal_receipt_sha256": args.server_receipt_sha256,
        "join_rule": "exact snapshot_tick match; unmatched frames are omitted, no interpolation",
        "source_tick_rate_assumption": video.TICKS_PER_SECOND,
        "target_height": target_height,
        "frame_count": len(common_ticks),
        "left_unmatched_frames": len(left) - len(common_ticks),
        "right_unmatched_frames": len(right) - len(common_ticks),
        "duration_seconds_from_simulation_ticks": duration,
        "left_stream": {"label": args.left_label, "camera_entity_id": args.pair_left[0], "lcd_output_entity_id": args.pair_left[1]},
        "right_stream": {"label": args.right_label, "camera_entity_id": args.pair_right[0], "lcd_output_entity_id": args.pair_right[1]},
        "frames": frame_entries,
        "video_file": output.name,
        "video_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    (frame_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "video": str(output),
        "manifest": str(manifest_path),
        "frames_directory": str(frame_dir),
        "frame_count": len(common_ticks),
        "left_unmatched_frames": len(left) - len(common_ticks),
        "right_unmatched_frames": len(right) - len(common_ticks),
        "duration_seconds_from_simulation_ticks": duration,
        "sha256": manifest["video_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path, help="directory of raw frames from one multiview capture")
    parser.add_argument("--output", type=Path, required=True, help="new MP4 path; existing outputs are never overwritten")
    parser.add_argument("--left-pair", dest="pair_left", type=parse_pair, required=True, metavar="CAMERA:OUTPUT")
    parser.add_argument("--right-pair", dest="pair_right", type=parse_pair, required=True, metavar="CAMERA:OUTPUT")
    parser.add_argument("--left-label", default="ONBOARD")
    parser.add_argument("--right-label", default="OVERHEAD")
    parser.add_argument("--max-frames", type=int, default=240, help="maximum raw frames per view")
    parser.add_argument("--max-bytes", type=int, default=128 * 1024 * 1024)
    parser.add_argument("--instance")
    parser.add_argument("--run-id")
    parser.add_argument("--server-receipt-sha256")
    parser.add_argument("--font", help="font file for on-video labels")
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
    print(json.dumps(compose(args.input_dir, args.output, args), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
