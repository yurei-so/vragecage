#!/usr/bin/env python3
"""Add a bounded HoloView software-frame export to one disposable Magnetar instance copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


SESSION_FILE = Path("Data/Scripts/HoloView3D/BoundedHoloCamSession.cs")
WORKER_FILE = Path("Data/Scripts/HoloView3D/BoundedHoloCamWorker.cs")
PATCH_MARKER = ".vragecage-holoview-capture.json"
PATCH_MARKER_PREFIX = ".vragecage-holoview-capture-"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one {label} anchor, found {count}")
    return text.replace(old, new, 1)


def scene_metadata_helper(args: argparse.Namespace) -> str:
    return f'''        string VrageCageBuildSceneMetadata(WorkerRasterJob job)
        {{
            if (!VrageCageCaptureMatches(job))
                return null;

            var gridById = new Dictionary<long, IMyCubeGrid>();
            var blockCounts = new Dictionary<long, int>();
            int gridBlockCandidateScanLimit = 4096;
            int gridBlockCandidatesScanned = Math.Min(rasterBlockCandidates.Count, gridBlockCandidateScanLimit);
            int gridBlockCandidateCount = rasterBlockCandidates.Count;
            for (int i = 0; i < gridBlockCandidatesScanned; i++)
            {{
                RasterBlockCandidate candidate = rasterBlockCandidates[i];
                if (candidate == null || candidate.Grid == null) continue;
                long id = candidate.Grid.EntityId;
                gridById[id] = candidate.Grid;
                int count;
                blockCounts.TryGetValue(id, out count);
                blockCounts[id] = count + 1;
            }}
            for (int i = 0; i < rasterFarGridEntities.Count; i++)
            {{
                IMyCubeGrid grid = rasterFarGridEntities[i] as IMyCubeGrid;
                if (grid != null) gridById[grid.EntityId] = grid;
            }}

            var gridIds = new List<long>(gridById.Keys);
            gridIds.Sort();
            var dynamicCandidates = new List<IMyEntity>();
            for (int i = 0; i < rasterDynamicEntities.Count; i++)
            {{
                IMyEntity entity = rasterDynamicEntities[i];
                if (entity == null || entity.Closed || entity.MarkedForClose) continue;
                try
                {{
                    BoundingBoxD bounds = entity.WorldAABB;
                    double radius = Math.Max(0.05, bounds.HalfExtents.Length());
                    if (WorkerSphereIntersectsScreen(job, bounds.Center, radius, job.GridRange))
                        dynamicCandidates.Add(entity);
                }}
                catch {{ }}
            }}
            dynamicCandidates.Sort(delegate(IMyEntity a, IMyEntity b)
            {{ return a.EntityId.CompareTo(b.EntityId); }});

            int totalObjects = gridIds.Count + dynamicCandidates.Count;
            int take = Math.Min(totalObjects, VrageCageCaptureSceneMaxObjects);
            var objectJson = new StringBuilder(Math.Min(65536, take * 320));
            int written = 0;
            bool serializationSkipped = false;
            for (int i = 0; i < gridIds.Count && written < take; i++)
            {{
                IMyCubeGrid grid = gridById[gridIds[i]];
                try
                {{
                    BoundingBoxD bounds = grid.WorldAABB;
                    var item = new StringBuilder(320);
                    item.Append("{{\\\"kind\\\":\\\"grid\\\",\\\"entity_id\\\":");
                    item.Append(grid.EntityId.ToString(CultureInfo.InvariantCulture));
                    item.Append(",\\\"is_static\\\":");
                    item.Append(grid.IsStatic ? "true" : "false");
                    item.Append(",\\\"grid_size_m\\\":");
                    item.Append(grid.GridSize.ToString("R", CultureInfo.InvariantCulture));
                    int candidateCount;
                    blockCounts.TryGetValue(grid.EntityId, out candidateCount);
                    item.Append(",\\\"raster_block_candidate_count\\\":");
                    item.Append(candidateCount.ToString(CultureInfo.InvariantCulture));
                    item.Append(",\\\"aabb_min_world\\\":");
                    VrageCageAppendVector(item, bounds.Min);
                    item.Append(",\\\"aabb_max_world\\\":");
                    VrageCageAppendVector(item, bounds.Max);
                    item.Append('}}');
                    if (written > 0) objectJson.Append(',');
                    objectJson.Append(item.ToString());
                    written++;
                }}
                catch {{ serializationSkipped = true; }}
            }}
            for (int i = 0; i < dynamicCandidates.Count && written < take; i++)
            {{
                IMyEntity entity = dynamicCandidates[i];
                try
                {{
                    BoundingBoxD bounds = entity.WorldAABB;
                    var item = new StringBuilder(224);
                    item.Append("{{\\\"kind\\\":\\\"dynamic_entity\\\",\\\"entity_id\\\":");
                    item.Append(entity.EntityId.ToString(CultureInfo.InvariantCulture));
                    item.Append(",\\\"aabb_min_world\\\":");
                    VrageCageAppendVector(item, bounds.Min);
                    item.Append(",\\\"aabb_max_world\\\":");
                    VrageCageAppendVector(item, bounds.Max);
                    item.Append('}}');
                    if (written > 0) objectJson.Append(',');
                    objectJson.Append(item.ToString());
                    written++;
                }}
                catch {{ serializationSkipped = true; }}
            }}
            var sb = new StringBuilder(objectJson.Length + 512);
            sb.Append("\\\"scene_semantics\\\":\\\"renderer_frustum_candidates\\\",\\\"summary\\\":{{\\\"grid_entity_candidate_count\\\":");
            sb.Append(gridIds.Count.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\\\"grid_block_candidate_count\\\":");
            sb.Append(gridBlockCandidateCount.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\\\"grid_block_candidates_scanned\\\":");
            sb.Append(gridBlockCandidatesScanned.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\\\"grid_block_candidate_scan_limit\\\":");
            sb.Append(gridBlockCandidateScanLimit.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\\\"dynamic_entity_candidate_count\\\":");
            sb.Append(dynamicCandidates.Count.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\\\"object_count\\\":");
            sb.Append(written.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\\\"object_limit\\\":");
            sb.Append(VrageCageCaptureSceneMaxObjects.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\\\"truncated\\\":");
            sb.Append(totalObjects > written || gridBlockCandidatesScanned < gridBlockCandidateCount || serializationSkipped ? "true" : "false");
            sb.Append("}},\\\"objects\\\":[");
            sb.Append(objectJson.ToString());
            sb.Append("]");
            return sb.ToString();
        }}

        static void VrageCageAppendVector(StringBuilder sb, Vector3D value)
        {{
            sb.Append('[');
            sb.Append(value.X.ToString("R", CultureInfo.InvariantCulture)); sb.Append(',');
            sb.Append(value.Y.ToString("R", CultureInfo.InvariantCulture)); sb.Append(',');
            sb.Append(value.Z.ToString("R", CultureInfo.InvariantCulture));
            sb.Append(']');
        }}
'''


def render_helper(args: argparse.Namespace) -> str:
    pair_checks = " ||\n                ".join(
        f"(job.CameraEntityId == {camera_id}L && job.OutputId == {output_id}L)"
        for camera_id, output_id in args.capture_pairs
    )
    return f'''        const int VrageCageCaptureIntervalTicks = {args.interval_ticks};
        const int VrageCageCaptureMaxFrames = {args.max_frames};
        const long VrageCageCaptureMaxBytes = {args.max_bytes}L;
        const int VrageCageCaptureMaxWidth = {args.max_width};
        const int VrageCageCaptureMaxHeight = {args.max_height};
        const int VrageCageCaptureMaxDurationTicks = {args.duration_ticks};
        const int VrageCageCaptureSceneMaxObjects = {args.scene_max_objects};
        const string VrageCageCaptureId = "{args.capture_id}";
        static readonly object vrageCageCaptureLock = new object();
        sealed class VrageCageCaptureStreamState
        {{
            public int Count;
            public int LastTick = -1;
            public int LastSceneMetadataTick = -1;
            public int FirstTick = -1;
            public bool Stopped;
        }}
        static readonly Dictionary<long, VrageCageCaptureStreamState> vrageCageCaptureStreams =
            new Dictionary<long, VrageCageCaptureStreamState>();
        static int vrageCageCaptureSequence;
        static long vrageCageCaptureBytes;
        static bool vrageCageCaptureStopped;

        bool VrageCageCaptureMatches(WorkerRasterJob job)
        {{
            if (job == null) return false;
            return {pair_checks};
        }}

        VrageCageCaptureStreamState VrageCageGetCaptureStream(long outputId)
        {{
            VrageCageCaptureStreamState state;
            if (!vrageCageCaptureStreams.TryGetValue(outputId, out state))
            {{
                state = new VrageCageCaptureStreamState();
                vrageCageCaptureStreams.Add(outputId, state);
            }}
            return state;
        }}

{scene_metadata_helper(args)}
        bool VrageCageShouldBuildSceneMetadata(WorkerRasterJob job, int snapshotTick)
        {{
            if (!VrageCageCaptureMatches(job))
                return false;
            lock (vrageCageCaptureLock)
            {{
                VrageCageCaptureStreamState state = VrageCageGetCaptureStream(job.OutputId);
                if (vrageCageCaptureStopped || state.Stopped || state.Count >= VrageCageCaptureMaxFrames)
                    return false;
                if (state.FirstTick < 0)
                    state.FirstTick = snapshotTick;
                if (snapshotTick - state.FirstTick > VrageCageCaptureMaxDurationTicks)
                {{
                    state.Stopped = true;
                    return false;
                }}
                int lastMetadataTick = Math.Max(state.LastTick, state.LastSceneMetadataTick);
                if (lastMetadataTick >= 0 && snapshotTick - lastMetadataTick < VrageCageCaptureIntervalTicks)
                    return false;
                state.LastSceneMetadataTick = snapshotTick;
                return true;
            }}
        }}

        void VrageCageCaptureRetainedFrame(WorkerRasterJob job)
        {{
            if (!VrageCageCaptureMatches(job) || job.FinalPixels == null ||
                string.IsNullOrEmpty(job.VrageCageSceneMetadataJson))
                return;

            int sequence;
            byte[] pixels;
            lock (vrageCageCaptureLock)
            {{
                VrageCageCaptureStreamState state = VrageCageGetCaptureStream(job.OutputId);
                if (vrageCageCaptureStopped || state.Stopped || state.Count >= VrageCageCaptureMaxFrames)
                    return;
                if (state.LastTick >= 0 &&
                    job.SnapshotTick - state.LastTick < VrageCageCaptureIntervalTicks)
                    return;
                if (state.FirstTick < 0)
                    state.FirstTick = job.SnapshotTick;
                if (job.SnapshotTick - state.FirstTick > VrageCageCaptureMaxDurationTicks)
                {{
                    state.Stopped = true;
                    return;
                }}
                // FinalPixels is the native post-processed raster. HoloView may
                // scale that buffer when drawing the larger LCD surface, so
                // validate/sample the buffer dimensions, not OutputWidth/Height.
                if (job.Width <= 0 || job.Height <= 0 ||
                    job.Width > VrageCageCaptureMaxWidth ||
                    job.Height > VrageCageCaptureMaxHeight ||
                    (long)job.Width * job.Height * 4L != job.FinalPixels.Length)
                {{
                    vrageCageCaptureStopped = true;
                    MyLog.Default.WriteLineAndConsole("[VRageCage] HoloView capture stopped: invalid or over-limit raster dimensions.");
                    return;
                }}

                long encodedEstimate = ((job.FinalPixels.Length + 2L) / 3L) * 4L + 1024L;
                if (vrageCageCaptureBytes + encodedEstimate > VrageCageCaptureMaxBytes)
                {{
                    vrageCageCaptureStopped = true;
                    MyLog.Default.WriteLineAndConsole("[VRageCage] HoloView capture stopped: byte budget reached.");
                    return;
                }}

                pixels = (byte[])job.FinalPixels.Clone();
                sequence = ++vrageCageCaptureSequence;
                state.Count++;
                state.LastTick = job.SnapshotTick;
                vrageCageCaptureBytes += encodedEstimate;
            }}

            Vector3D position = job.Cam.Translation;
            Vector3D forward = job.Cam.Forward;
            string header = "HVRAW1|" + job.Width.ToString(CultureInfo.InvariantCulture) + "|" +
                job.Height.ToString(CultureInfo.InvariantCulture) + "|" +
                job.CameraEntityId.ToString(CultureInfo.InvariantCulture) + "|" +
                job.OutputId.ToString(CultureInfo.InvariantCulture) + "|" +
                job.SurfaceIndex.ToString(CultureInfo.InvariantCulture) + "|" +
                job.OutputWidth.ToString(CultureInfo.InvariantCulture) + "|" +
                job.OutputHeight.ToString(CultureInfo.InvariantCulture) + "|" +
                job.SnapshotTick.ToString(CultureInfo.InvariantCulture) + "|" +
                job.FovDegrees.ToString("R", CultureInfo.InvariantCulture) + "|" +
                position.X.ToString("R", CultureInfo.InvariantCulture) + "|" +
                position.Y.ToString("R", CultureInfo.InvariantCulture) + "|" +
                position.Z.ToString("R", CultureInfo.InvariantCulture) + "|" +
                forward.X.ToString("R", CultureInfo.InvariantCulture) + "|" +
                forward.Y.ToString("R", CultureInfo.InvariantCulture) + "|" +
                forward.Z.ToString("R", CultureInfo.InvariantCulture);
            string fileName = "vragecage-hv-{args.capture_id}-" + sequence.ToString("D5", CultureInfo.InvariantCulture) + ".hvraw.txt";
            try
            {{
                string sceneJson = job.VrageCageSceneMetadataJson;
                string sceneFileName = fileName.Substring(0, fileName.Length - ".hvraw.txt".Length) + ".scene.json";
                string sceneDocument = "{{\\\"schema\\\":\\\"vragecage.holoview-scene-metadata.v1\\\",\\\"provenance\\\":\\\"HoloView renderer frustum candidates; mod-side world metadata, not sensor detection or simulator truth\\\",\\\"capture_id\\\":\\\"{args.capture_id}\\\",\\\"frame_sequence\\\":" + sequence.ToString(CultureInfo.InvariantCulture) +
                    ",\\\"associated_raw_frame_file\\\":\\\"" + fileName + "\\\",\\\"camera_entity_id\\\":" + job.CameraEntityId.ToString(CultureInfo.InvariantCulture) +
                    ",\\\"lcd_output_entity_id\\\":" + job.OutputId.ToString(CultureInfo.InvariantCulture) + ",\\\"surface_index\\\":" + job.SurfaceIndex.ToString(CultureInfo.InvariantCulture) +
                    ",\\\"snapshot_tick\\\":" + job.SnapshotTick.ToString(CultureInfo.InvariantCulture) + ",\\\"fov_degrees\\\":" + job.FovDegrees.ToString("R", CultureInfo.InvariantCulture) +
                    ",\\\"camera_position_world\\\":[" + position.X.ToString("R", CultureInfo.InvariantCulture) + "," + position.Y.ToString("R", CultureInfo.InvariantCulture) + "," + position.Z.ToString("R", CultureInfo.InvariantCulture) +
                    "],\\\"camera_forward_world\\\":[" + forward.X.ToString("R", CultureInfo.InvariantCulture) + "," + forward.Y.ToString("R", CultureInfo.InvariantCulture) + "," + forward.Z.ToString("R", CultureInfo.InvariantCulture) +
                    "],\\\"native_raster\\\":[" + job.Width.ToString(CultureInfo.InvariantCulture) + "," + job.Height.ToString(CultureInfo.InvariantCulture) +
                    "],\\\"display_size\\\":[" + job.OutputWidth.ToString(CultureInfo.InvariantCulture) + "," + job.OutputHeight.ToString(CultureInfo.InvariantCulture) + "]," + sceneJson + "}}";
                using (var writer = MyAPIGateway.Utilities.WriteFileInWorldStorage(
                    sceneFileName, typeof(PortalLcdSession)))
                {{
                    writer.WriteLine(sceneDocument);
                }}
                using (var writer = MyAPIGateway.Utilities.WriteFileInWorldStorage(
                    fileName, typeof(PortalLcdSession)))
                {{
                    writer.WriteLine(header);
                    writer.WriteLine(Convert.ToBase64String(pixels));
                }}
                MyLog.Default.WriteLineAndConsole("[VRageCage] HoloView frame exported: " +
                    sequence.ToString(CultureInfo.InvariantCulture) + " tick=" +
                    job.SnapshotTick.ToString(CultureInfo.InvariantCulture));
            }}
            catch (Exception ex)
            {{
                lock (vrageCageCaptureLock) vrageCageCaptureStopped = true;
                MyLog.Default.WriteLineAndConsole("[VRageCage] HoloView capture stopped after export error: " + ex.Message);
            }}
        }}
'''


def instrument(instance: Path, source: Path, args: argparse.Namespace) -> dict[str, object]:
    instance = instance.expanduser().resolve(strict=True)
    source = source.expanduser().resolve(strict=True)
    marker_path = instance / ".vragecage-lab.json"
    if not marker_path.is_file():
        raise SystemExit(f"missing destructive-lab instance receipt: {marker_path}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("authority") != "destructive-lab" or marker.get("disposable") is not True:
        raise SystemExit("refusing capture instrumentation outside a disposable destructive-lab instance")
    if marker.get("engine") != "magnetar":
        raise SystemExit("HoloView frame capture instrumentation currently requires Magnetar")
    unit = f"vragecage-{instance.name}.service"
    try:
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as error:
        raise SystemExit(f"cannot verify that the disposable instance is stopped: {error}")
    if active.returncode == 0:
        raise SystemExit(f"refusing to instrument a running instance: {unit}")
    try:
        source.relative_to(instance)
    except ValueError:
        raise SystemExit("HoloView source must be inside the disposable instance")
    if source.is_symlink():
        raise SystemExit("refusing to instrument a symlinked HoloView source directory")
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for character in args.capture_id):
        raise SystemExit("capture ID may contain only ASCII letters, digits, and hyphens")

    session_path = source / SESSION_FILE
    worker_path = source / WORKER_FILE
    if not session_path.is_file() or not worker_path.is_file():
        raise SystemExit("source does not match the supported HoloView 3D layout")
    before = {str(path.relative_to(instance)): sha256(path) for path in (session_path, worker_path)}
    legacy_receipt = instance / PATCH_MARKER
    if legacy_receipt.is_file():
        previous = json.loads(legacy_receipt.read_text(encoding="utf-8"))
        previous_source = Path(previous.get("source", "")).resolve()
        if previous_source == source and before != previous.get("source_files_before_sha256"):
            raise SystemExit("prior instrumentation is still present or the HoloView source changed")
    receipt_path = instance / f"{PATCH_MARKER_PREFIX}{args.capture_id}.json"
    if receipt_path.exists():
        raise SystemExit(f"capture ID already has a request receipt: {receipt_path}")
    session = session_path.read_text(encoding="utf-8")
    worker = worker_path.read_text(encoding="utf-8")
    worker = replace_once(
        worker,
        "            public long OutputId;\n",
        "            public long OutputId;\n            public long CameraEntityId;\n            public string VrageCageSceneMetadataJson;\n",
        "raster job output identity",
    )
    worker = replace_once(
        worker,
        "            job.OutputId = output.EntityId;\n",
        "            job.OutputId = output.EntityId;\n            job.CameraEntityId = camera.EntityId;\n",
        "raster job camera snapshot",
    )
    worker = replace_once(
        worker,
        "            job.FinalPixels = null;\n",
        "            job.FinalPixels = null;\n            job.VrageCageSceneMetadataJson = null;\n",
        "per-frame metadata reset",
    )
    worker = replace_once(
        worker,
        "            SnapshotGridSceneForWorker(job);\n",
        "            SnapshotGridSceneForWorker(job);\n",
        "renderer scene snapshot hook",
    )
    worker = replace_once(
        worker,
        "            job.SnapshotTick = ticks;\n",
        "            job.SnapshotTick = ticks;\n"
        "            if (VrageCageShouldBuildSceneMetadata(job, ticks))\n"
        "                job.VrageCageSceneMetadataJson = VrageCageBuildSceneMetadata(job);\n",
        "bounded HoloView metadata sampling hook",
    )
    session = replace_once(
        session,
        "using System;\n",
        "using System;\nusing System.Globalization;\n",
        "System using list",
    )
    fields_anchor = (
        "        static readonly object sharedRasterFramesLock = new object();\n\n"
        "        static int sharedRasterFrameSerialSeed;"
    )
    fields = (
        "        static readonly object sharedRasterFramesLock = new object();\n\n"
        + render_helper(args)
        + "\n        static int sharedRasterFrameSerialSeed;"
    )
    session = replace_once(session, fields_anchor, fields, "retained-frame fields")
    method_anchor = (
        "                }\n"
        "            }\n"
        "        }\n\n"
        "        void FanOutRetainedLcdFrame(WorkerRasterJob job)"
    )
    method = (
        "                }\n"
        "            }\n"
        "            VrageCageCaptureRetainedFrame(job);\n"
        "        }\n\n"
        "        void FanOutRetainedLcdFrame(WorkerRasterJob job)"
    )
    session = replace_once(session, method_anchor, method, "retained-frame publish hook")

    atomic_write(session_path, session)
    atomic_write(worker_path, worker)
    receipt = {
        "schema": "vragecage.holoview-software-capture-request.v1",
        "capture_id": args.capture_id,
        "instance": str(instance),
        "source": str(source),
        "camera_entity_id": args.camera_id,
        "lcd_output_entity_id": args.output_id,
        "camera_output_pairs": [
            {"camera_entity_id": camera_id, "lcd_output_entity_id": output_id}
            for camera_id, output_id in args.capture_pairs
        ],
        "interval_ticks": args.interval_ticks,
        "max_frames": args.max_frames,
        "max_bytes": args.max_bytes,
        "max_width": args.max_width,
        "max_height": args.max_height,
        "max_duration_ticks": args.duration_ticks,
        "scene_metadata": True,
        "scene_max_objects": args.scene_max_objects,
        "provenance": "HoloView 3D software raster RGBA; not native framebuffer or simulator ground truth",
        "source_files_before_sha256": before,
        "source_files_after_sha256": {
            str(path.relative_to(instance)): sha256(path) for path in (session_path, worker_path)
        },
    }
    atomic_write(receipt_path, json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return receipt


def atomic_write(path: Path, text: str) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path, help="stopped disposable Magnetar instance directory")
    parser.add_argument("holoview_source", type=Path, help="HoloView source directory inside that instance")
    parser.add_argument("--camera-id", type=int, help="single camera entity ID (legacy single-pair form)")
    parser.add_argument("--output-id", type=int, help="single HoloView LCD output entity ID")
    parser.add_argument("--pair", action="append", metavar="CAMERA:OUTPUT",
                        help="camera/output entity pair; repeat to capture synchronized views")
    parser.add_argument("--interval-ticks", type=int, default=120)
    parser.add_argument("--max-frames", type=int, default=60)
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-width", type=int, default=512)
    parser.add_argument("--max-height", type=int, default=512)
    parser.add_argument("--duration-ticks", type=int, default=7200)
    parser.add_argument("--scene-max-objects", type=int, default=64,
                        help="maximum renderer-candidate entities in each scene metadata sidecar")
    parser.add_argument("--capture-id", default=uuid.uuid4().hex[:16])
    args = parser.parse_args()
    pairs: list[tuple[int, int]] = []
    if args.pair:
        if args.camera_id is not None or args.output_id is not None:
            parser.error("use --pair or --camera-id/--output-id, not both")
        try:
            for value in args.pair:
                camera, output = value.split(":", 1)
                pairs.append((int(camera), int(output)))
        except (ValueError, TypeError):
            parser.error("each --pair must be CAMERA:OUTPUT with integer entity IDs")
    else:
        if args.camera_id is None or args.output_id is None:
            parser.error("provide at least one --pair CAMERA:OUTPUT or both --camera-id and --output-id")
        pairs.append((args.camera_id, args.output_id))
    if not 1 <= len(pairs) <= 4:
        parser.error("capture supports 1..4 camera/output pairs")
    if any(camera <= 0 or output <= 0 for camera, output in pairs):
        parser.error("camera and output entity IDs must be positive")
    if len({camera for camera, _ in pairs}) != len(pairs) or len({output for _, output in pairs}) != len(pairs):
        parser.error("camera IDs and output IDs must each be unique across pairs")
    args.capture_pairs = pairs
    args.camera_id, args.output_id = pairs[0]
    if args.interval_ticks < 1 or not 1 <= args.max_frames <= 600:
        parser.error("interval-ticks must be positive and max-frames must be 1..600")
    if not 4096 <= args.max_bytes <= 1024 * 1024 * 1024:
        parser.error("max-bytes must be between 4 KiB and 1 GiB")
    if not 16 <= args.max_width <= 2048 or not 16 <= args.max_height <= 2048:
        parser.error("max dimensions must each be between 16 and 2048")
    if args.duration_ticks < args.interval_ticks:
        parser.error("duration-ticks must be at least interval-ticks")
    if not 1 <= args.scene_max_objects <= 512:
        parser.error("scene-max-objects must be 1..512")
    receipt = instrument(args.instance, args.holoview_source, args)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
