#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSER = ROOT / "holoview-multiview-video.py"


def write_frame(path: Path, camera: int, output: int, tick: int, value: int) -> None:
    rgba = bytes((value, 255 - value, 32, 255)) * (8 * 6)
    header = f"HVRAW1|8|6|{camera}|{output}|0|64|64|{tick}|60|1|2|3|0|-1|0\n"
    path.write_bytes(header.encode("ascii") + base64.b64encode(rgba) + b"\n")


def main() -> None:
    if not shutil.which("ffmpeg"):
        print("ffmpeg unavailable; skipping synchronized MP4 assertion")
        return
    with tempfile.TemporaryDirectory(prefix="holoview-multiview-") as temporary:
        root = Path(temporary)
        raw = root / "raw"
        raw.mkdir()
        write_frame(raw / "vragecage-hv-dual-00001.hvraw.txt", 101, 202, 100, 20)
        write_frame(raw / "vragecage-hv-dual-00002.hvraw.txt", 303, 404, 100, 90)
        write_frame(raw / "vragecage-hv-dual-00003.hvraw.txt", 101, 202, 220, 40)
        write_frame(raw / "vragecage-hv-dual-00004.hvraw.txt", 303, 404, 220, 110)
        write_frame(raw / "vragecage-hv-dual-00005.hvraw.txt", 101, 202, 340, 60)
        write_frame(raw / "vragecage-hv-dual-00006.hvraw.txt", 303, 404, 400, 130)
        output = root / "side-by-side.mp4"
        result = subprocess.run([
            sys.executable, str(COMPOSER), str(raw), "--output", str(output),
            "--left-pair", "101:202", "--right-pair", "303:404",
            "--left-label", "ONBOARD", "--right-label", "OVERHEAD",
        ], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert output.is_file() and output.stat().st_size > 0
        manifest = json.loads(Path(receipt["manifest"]).read_text(encoding="utf-8"))
        assert manifest["frame_count"] == 2
        assert manifest["left_unmatched_frames"] == 1
        assert manifest["right_unmatched_frames"] == 1
        assert [frame["snapshot_tick"] for frame in manifest["frames"]] == [100, 220]
        assert manifest["join_rule"].startswith("exact snapshot_tick match")
        assert len(manifest["video_sha256"]) == 64
        print("HoloView synchronized multiview video tests passed")


if __name__ == "__main__":
    main()
