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
INSTRUMENT = ROOT / "instrument-holoview-capture.py"
VIDEO = ROOT / "holoview-frame-video.py"
REPLACE_MOD = ROOT / "replace-published-mod.py"


def instrumenter_test(temporary: Path) -> None:
    instance = temporary / "instance"
    source = instance / "content" / "mod"
    scripts = source / "Data/Scripts/HoloView3D"
    scripts.mkdir(parents=True)
    (instance / ".vragecage-lab.json").write_text(json.dumps({
        "authority": "destructive-lab", "disposable": True, "engine": "magnetar",
    }), encoding="utf-8")
    worker = scripts / "BoundedHoloCamWorker.cs"
    worker.write_text(
        "namespace HoloCamRestored2D { class WorkerRasterJob {\n"
        "            public long OutputId;\n"
        "        } void Enqueue() {\n"
        "            job.OutputId = output.EntityId;\n"
        "            job.FinalPixels = null;\n"
        "            SnapshotGridSceneForWorker(job);\n"
        "            job.SnapshotTick = ticks;\n"
        "        } }\n", encoding="utf-8")
    session = scripts / "BoundedHoloCamSession.cs"
    session.write_text(
        "using System;\nnamespace HoloCamRestored2D { partial class PortalLcdSession {\n"
        "        static readonly object sharedRasterFramesLock = new object();\n\n"
        "        static int sharedRasterFrameSerialSeed;\n"
        "        void PublishRetainedLcdFrame(WorkerRasterJob job) {\n"
        "                }\n            }\n        }\n\n"
        "        void FanOutRetainedLcdFrame(WorkerRasterJob job)\n"
        "    } }\n", encoding="utf-8")
    # A prior attempt may have instrumented a different downloaded source tree
    # in the same disposable instance. It must not block this clean staged copy.
    (instance / ".vragecage-holoview-capture.json").write_text(json.dumps({
        "source": str(instance / "content" / "published-mod"),
        "source_files_before_sha256": {},
    }), encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(INSTRUMENT), str(instance), str(source),
        "--camera-id", "101", "--output-id", "202", "--capture-id", "test-capture",
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["interval_ticks"] == 120
    assert receipt["camera_entity_id"] == 101
    assert receipt["scene_metadata"] is True and receipt["scene_max_objects"] == 64
    assert "job.CameraEntityId = camera.EntityId;" in worker.read_text(encoding="utf-8")
    assert "job.VrageCageSceneMetadataJson = VrageCageBuildSceneMetadata(job);" in worker.read_text(encoding="utf-8")
    assert "VrageCageShouldBuildSceneMetadata(job, ticks)" in worker.read_text(encoding="utf-8")
    assert worker.read_text(encoding="utf-8").index("job.SnapshotTick = ticks;") < worker.read_text(encoding="utf-8").index("VrageCageShouldBuildSceneMetadata(job, ticks)")
    patched = session.read_text(encoding="utf-8")
    assert "VrageCageCaptureRetainedFrame(job);" in patched
    assert "job.CameraEntityId == 101L && job.OutputId == 202L" in patched
    assert "vragecage.holoview-scene-metadata.v1" in patched
    assert "renderer_frustum_candidates" in patched
    assert "gridBlockCandidateScanLimit = 4096" in patched
    assert "LastSceneMetadataTick = -1" in patched
    assert "string.IsNullOrEmpty(job.VrageCageSceneMetadataJson)" in patched
    assert "VrageCageAppendVector" in patched
    assert "job.Width > VrageCageCaptureMaxWidth" in patched
    assert "job.Width * job.Height * 4L != job.FinalPixels.Length" in patched
    assert (instance / ".vragecage-holoview-capture-test-capture.json").is_file()
    retry = subprocess.run([
        sys.executable, str(INSTRUMENT), str(instance), str(source),
        "--camera-id", "101", "--output-id", "202", "--capture-id", "test-capture",
    ], capture_output=True, text=True)
    assert retry.returncode != 0 and "already has a request receipt" in retry.stderr

    dual_instance = temporary / "dual-instance"
    dual_source = dual_instance / "content" / "mod"
    dual_scripts = dual_source / "Data/Scripts/HoloView3D"
    dual_scripts.mkdir(parents=True)
    (dual_instance / ".vragecage-lab.json").write_text(json.dumps({
        "authority": "destructive-lab", "disposable": True, "engine": "magnetar",
    }), encoding="utf-8")
    (dual_scripts / "BoundedHoloCamWorker.cs").write_text(
        "namespace HoloCamRestored2D { class WorkerRasterJob {\n"
        "            public long OutputId;\n"
        "        } void Enqueue() {\n"
        "            job.OutputId = output.EntityId;\n"
        "            job.FinalPixels = null;\n"
        "            SnapshotGridSceneForWorker(job);\n"
        "            job.SnapshotTick = ticks;\n"
        "        } }\n", encoding="utf-8")
    (dual_scripts / "BoundedHoloCamSession.cs").write_text(
        "using System;\nnamespace HoloCamRestored2D { partial class PortalLcdSession {\n"
        "        static readonly object sharedRasterFramesLock = new object();\n\n"
        "        static int sharedRasterFrameSerialSeed;\n"
        "        void PublishRetainedLcdFrame(WorkerRasterJob job) {\n"
        "                }\n            }\n        }\n\n"
        "        void FanOutRetainedLcdFrame(WorkerRasterJob job)\n"
        "    } }\n", encoding="utf-8")
    dual = subprocess.run([
        sys.executable, str(INSTRUMENT), str(dual_instance), str(dual_source),
        "--pair", "303:404", "--pair", "505:606", "--capture-id", "dual-capture",
    ], capture_output=True, text=True)
    assert dual.returncode == 0, dual.stderr
    dual_receipt = json.loads(dual.stdout)
    assert dual_receipt["camera_output_pairs"] == [
        {"camera_entity_id": 303, "lcd_output_entity_id": 404},
        {"camera_entity_id": 505, "lcd_output_entity_id": 606},
    ]
    dual_patched = (dual_scripts / "BoundedHoloCamSession.cs").read_text(encoding="utf-8")
    assert "job.CameraEntityId == 303L && job.OutputId == 404L" in dual_patched
    assert "job.CameraEntityId == 505L && job.OutputId == 606L" in dual_patched
    assert "VrageCageCaptureStreamState" in dual_patched


def raw_frame(width: int, height: int, tick: int, value: int) -> bytes:
    rgba = bytes((value, 255 - value, 32, 255)) * (width * height)
    header = f"HVRAW1|{width}|{height}|101|202|0|64|64|{tick}|60|1|2|3|0|-1|0\n"
    return header.encode("ascii") + base64.b64encode(rgba) + b"\n"


def converter_test(temporary: Path) -> None:
    if not shutil.which("ffmpeg"):
        print("ffmpeg unavailable; skipping MP4 encoding assertion")
        return
    input_dir = temporary / "raw frames"
    input_dir.mkdir()
    first = input_dir / "vragecage-hv-test-00001.hvraw.txt"
    second = input_dir / "vragecage-hv-test-00002.hvraw.txt"
    first.write_bytes(raw_frame(8, 6, 100, 40))
    second.write_bytes(raw_frame(8, 6, 220, 180))
    for sequence, path, tick in ((1, first, 100), (2, second, 220)):
        scene_path = path.with_name(path.name.removesuffix(".hvraw.txt") + ".scene.json")
        scene_path.write_text(json.dumps({
            "schema": "vragecage.holoview-scene-metadata.v1",
            "provenance": "HoloView renderer frustum candidates; mod-side world metadata, not sensor detection or simulator truth",
            "capture_id": "test", "frame_sequence": sequence,
            "associated_raw_frame_file": path.name,
            "camera_entity_id": 101, "lcd_output_entity_id": 202,
            "surface_index": 0, "snapshot_tick": tick,
            "fov_degrees": 60, "camera_position_world": [1, 2, 3],
            "camera_forward_world": [0, -1, 0], "native_raster": [8, 6],
            "display_size": [64, 64],
            "scene_semantics": "renderer_frustum_candidates",
            "summary": {
                "grid_entity_candidate_count": 1, "grid_block_candidate_count": 4,
                "grid_block_candidates_scanned": 4, "grid_block_candidate_scan_limit": 4096,
                "dynamic_entity_candidate_count": 0, "object_count": 1,
                "object_limit": 64, "truncated": False,
            },
            "objects": [{"kind": "grid", "entity_id": 303}],
        }), encoding="utf-8")
    output = temporary / "capture.mp4"
    result = subprocess.run([
        sys.executable, str(VIDEO), str(input_dir), "--output", str(output),
        "--instance", "test-instance", "--run-id", "test-run",
        "--server-receipt-sha256", "a" * 64,
    ], capture_output=True, text=True, check=True)
    receipt = json.loads(result.stdout)
    assert receipt["frame_count"] == 2
    assert output.read_bytes()[4:8] == b"ftyp"
    frame_dir = temporary / "capture-frames"
    assert (frame_dir / "frame-00001.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    manifest = json.loads((frame_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provenance"].startswith("HoloView 3D software raster")
    assert manifest["capture_id"] == "test"
    assert manifest["instance"] == "test-instance" and manifest["server_run_id"] == "test-run"
    assert manifest["server_terminal_receipt_sha256"] == "a" * 64
    assert manifest["scene_metadata_frame_count"] == 2
    assert [entry["scene_metadata"]["object_count"] for entry in manifest["frames"]] == [1, 1]
    assert all(len(entry["scene_metadata"]["sha256"]) == 64 for entry in manifest["frames"])
    assert [entry["snapshot_tick"] for entry in manifest["frames"]] == [100, 220]
    assert manifest["raster_width"] == 8 and manifest["display_width"] == 64
    assert [entry["display_width"] for entry in manifest["frames"]] == [64, 64]
    assert first.exists() and second.exists(), "conversion must preserve source frames"
    bad_dir = temporary / "bad scene metadata"
    bad_dir.mkdir()
    for source in (first, second):
        shutil.copy2(source, bad_dir / source.name)
    bad_sidecar = bad_dir / (first.name.removesuffix(".hvraw.txt") + ".scene.json")
    shutil.copy2(first.with_name(first.name.removesuffix(".hvraw.txt") + ".scene.json"), bad_sidecar)
    bad_metadata = json.loads(bad_sidecar.read_text(encoding="utf-8"))
    bad_metadata["snapshot_tick"] = 101
    bad_sidecar.write_text(json.dumps(bad_metadata), encoding="utf-8")
    shutil.copy2(second.with_name(second.name.removesuffix(".hvraw.txt") + ".scene.json"), bad_dir)
    mismatch = subprocess.run([
        sys.executable, str(VIDEO), str(bad_dir), "--output", str(temporary / "bad-metadata.mp4"),
    ], capture_output=True, text=True)
    assert mismatch.returncode != 0 and "does not match its raw frame" in mismatch.stderr
    assert not (temporary / "bad-metadata.mp4").exists()
    assert not (temporary / "bad-metadata-frames").exists()
    partial_dir = temporary / "partial scene metadata"
    partial_dir.mkdir()
    for source in (first, second):
        shutil.copy2(source, partial_dir / source.name)
    shutil.copy2(first.with_name(first.name.removesuffix(".hvraw.txt") + ".scene.json"), partial_dir)
    partial = subprocess.run([
        sys.executable, str(VIDEO), str(partial_dir), "--output", str(temporary / "partial-metadata.mp4"),
    ], capture_output=True, text=True)
    assert partial.returncode != 0 and "scene metadata for only some frames" in partial.stderr
    assert not (temporary / "partial-metadata.mp4").exists()
    assert not (temporary / "partial-metadata-frames").exists()
    overwrite = subprocess.run([
        sys.executable, str(VIDEO), str(input_dir), "--output", str(output),
    ], capture_output=True, text=True)
    assert overwrite.returncode != 0 and "refusing to overwrite" in overwrite.stderr
    (input_dir / "vragecage-hv-other-00003.hvraw.txt").write_bytes(raw_frame(8, 6, 340, 220))
    mixed = subprocess.run([
        sys.executable, str(VIDEO), str(input_dir), "--output", str(temporary / "mixed.mp4"),
    ], capture_output=True, text=True)
    assert mixed.returncode != 0 and "mix capture IDs" in mixed.stderr


def replacement_test(temporary: Path) -> None:
    instance = temporary / "replace-instance"
    world = instance / "World"
    package = temporary / "replacement-package"
    (world).mkdir(parents=True)
    (package / "Data").mkdir(parents=True)
    (instance / ".vragecage-lab.json").write_text(json.dumps({
        "authority": "destructive-lab", "disposable": True,
        "instance": instance.name,
    }), encoding="utf-8")
    (instance / ".vragecage-world-import.json").write_text(json.dumps({
        "engine": "magnetar", "instance": instance.name, "tree_sha256": "a" * 64,
    }), encoding="utf-8")
    (instance / ".vragecage-engine").write_text("magnetar\n", encoding="utf-8")
    (package / "Data/Test.sbc").write_text("<Definitions />\n", encoding="utf-8")
    digest = __import__("hashlib").sha256()
    digest.update(len(b"Data/Test.sbc").to_bytes(4, "big"))
    digest.update(b"Data/Test.sbc")
    digest.update(b"<Definitions />\n")
    (instance / ".vragecage-mod-stage.json").write_text(json.dumps({
        "engine": "magnetar", "instance": instance.name,
        "name": "HoloViewCapture", "path": str(package),
        "synthetic_workshop_id": 90000000000000001,
        "tree_sha256": digest.hexdigest(),
    }), encoding="utf-8")
    original = ('<?xml version="1.0"?><Checkpoint xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                'xmlns:xsd="http://www.w3.org/2001/XMLSchema"><Script xsi:type="xsd:Type"/><Mods>'
                '<ModItem><Name>3795893318.sbm</Name><PublishedFileId>3795893318</PublishedFileId></ModItem>'
                '<ModItem><Name>Other</Name><PublishedFileId>123</PublishedFileId></ModItem>'
                '</Mods></Checkpoint>\n')
    (world / "Sandbox.sbc").write_text(original, encoding="utf-8")
    (world / "Sandbox_config.sbc").write_text(original, encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(REPLACE_MOD), str(instance), "3795893318", "HoloViewCapture",
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["workshop_id_removed"] == 3795893318
    assert receipt["replacement_mod_name"] == "HoloViewCapture"
    assert len(list((instance / "Quarantine/mod-replacements").glob("*/Sandbox.sbc"))) == 1
    for path in (world / "Sandbox.sbc", world / "Sandbox_config.sbc"):
        text = path.read_text(encoding="utf-8")
        import xml.etree.ElementTree as ET
        ET.parse(path)
        assert "3795893318" not in text
        assert 'xsi:type="xsd:Type"' in text and "xmlns:xsd=" in text
        assert "<PublishedFileId>123</PublishedFileId>" in text
    operation_id = receipt["operation_id"]
    restored = subprocess.run([
        sys.executable, str(REPLACE_MOD), str(instance), "--restore-operation", operation_id,
    ], capture_output=True, text=True)
    assert restored.returncode == 0, restored.stderr
    restoration = json.loads(restored.stdout)
    assert restoration["workshop_id_restored"] == 3795893318
    for path in (world / "Sandbox.sbc", world / "Sandbox_config.sbc"):
        assert path.read_text(encoding="utf-8") == original
    second_restore = subprocess.run([
        sys.executable, str(REPLACE_MOD), str(instance), "--restore-operation", operation_id,
    ], capture_output=True, text=True)
    assert second_restore.returncode != 0 and "already has a restoration receipt" in second_restore.stderr
    second_replace = subprocess.run([
        sys.executable, str(REPLACE_MOD), str(instance), "3795893318", "HoloViewCapture",
    ], capture_output=True, text=True)
    assert second_replace.returncode == 0, second_replace.stderr
    second_operation_id = json.loads(second_replace.stdout)["operation_id"]
    assert second_operation_id != operation_id
    final_restore = subprocess.run([
        sys.executable, str(REPLACE_MOD), str(instance), "--restore-operation", second_operation_id,
    ], capture_output=True, text=True)
    assert final_restore.returncode == 0, final_restore.stderr


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vragecage-holoview-test-") as directory:
        temporary = Path(directory)
        instrumenter_test(temporary)
        converter_test(temporary)
        replacement_test(temporary)
    print("HoloView periodic capture tests passed")


if __name__ == "__main__":
    main()
