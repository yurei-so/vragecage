#!/usr/bin/env python3
"""Bounded read-only capture of run-bound Voidwright motion log samples."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time


RUN_ID = re.compile(r"^[0-9a-f]{32}$")
FIELD = re.compile(r"(?:^|\s)([A-Za-z][A-Za-z0-9]*)=([^\s]+)")
SOURCE_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3,9})")
MARKER = "Voidwright evidence:"
MAX_LINE_BYTES = 32 * 1024
MAX_READ_BYTES_PER_POLL = 1024 * 1024
MAX_SOURCE_BYTES_PER_CAPTURE = 256 * 1024 * 1024
MAX_DURATION_SECONDS = 300
MAX_POLL_HZ = 60
MAX_CAPACITY = 8192
MAX_CAPTURE_BYTES = 64 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_sample(line: str, *, run_id: str, operation_id: str,
                 controller_id: int, grid_id: int, observed_at_utc: str) -> tuple[dict[str, object] | None, str]:
    marker_index = line.find(MARKER)
    if marker_index < 0:
        return None, "filtered"
    evidence = line[marker_index + len(MARKER):].strip()
    fields = dict(FIELD.findall(evidence))
    if fields.get("type") != "server-control" or fields.get("action") != "motion-telemetry":
        return None, "filtered"
    required = ("operation", "controllerId", "gridId", "runId", "sampleSequence", "capturedAtUtc")
    if any(name not in fields for name in required):
        return None, "malformed"
    try:
        sample_controller = int(fields["controllerId"], 10)
        sample_grid = int(fields["gridId"], 10)
        sample_sequence = int(fields["sampleSequence"], 10)
        captured = datetime.fromisoformat(fields["capturedAtUtc"].replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None, "malformed"
    if (sample_controller <= 0 or sample_grid <= 0 or sample_sequence < 0 or
            captured.tzinfo is None or captured.utcoffset() is None):
        return None, "malformed"
    if (fields["runId"] != run_id or fields["operation"] != operation_id or
            sample_controller != controller_id or sample_grid != grid_id):
        return None, "identity_mismatch"
    source_time = SOURCE_TIME.match(line)
    return {
        "type": "motion-sample",
        "runId": run_id,
        "operationId": operation_id,
        "controllerId": sample_controller,
        "gridId": sample_grid,
        "sampleSequence": sample_sequence,
        "capturedAtUtc": captured.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "observedAtUtc": observed_at_utc,
        "sourceTimestampUnqualified": source_time.group(1) if source_time else None,
        "rawEvidence": evidence,
    }, "matched"


class BoundedCapture:
    """Drop oldest records under bounded memory pressure and report exact loss."""

    def __init__(self, capacity: int, maximum_bytes: int) -> None:
        self.capacity = capacity
        self.maximum_bytes = maximum_bytes
        self.records: deque[tuple[dict[str, object], int]] = deque()
        self.retained_bytes = 0
        self.dropped_samples = 0
        self.dropped_bytes = 0

    def append(self, record: dict[str, object]) -> bool:
        size = len((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        if size > self.maximum_bytes:
            self.dropped_samples += 1
            self.dropped_bytes += size
            return False
        while self.records and (len(self.records) >= self.capacity or self.retained_bytes + size > self.maximum_bytes):
            _, removed_size = self.records.popleft()
            self.retained_bytes -= removed_size
            self.dropped_samples += 1
            self.dropped_bytes += removed_size
        self.records.append((record, size))
        self.retained_bytes += size
        return True


def capture(source: Path, output: Path, *, run_id: str, operation_id: str,
            controller_id: int, grid_id: int, duration_seconds: float,
            poll_hz: float, capacity: int, maximum_bytes: int,
            from_start: bool = False) -> dict[str, object]:
    source = source.expanduser()
    if source.is_symlink():
        raise ValueError("source log must be a regular, non-symlink file")
    source = source.resolve(strict=True)
    output = output.expanduser().absolute()
    if source == output or output.exists() or output.is_symlink():
        raise ValueError("output must be a new path distinct from the read-only source log")
    if not source.is_file() or source.is_symlink():
        raise ValueError("source log must be a regular, non-symlink file")
    source_stat = source.stat()
    if source_stat.st_size > 16 * 1024 * 1024 * 1024:
        raise ValueError("source log exceeds the 16 GiB safety bound")
    poll_period = 1.0 / poll_hz
    queue = BoundedCapture(capacity, maximum_bytes)
    counts = {"linesRead": 0, "matchedSamples": 0, "filteredLines": 0,
              "identityMismatches": 0, "malformedLines": 0, "oversizedLines": 0}
    started_at = utc_now()
    start_monotonic = time.monotonic()
    deadline = start_monotonic + duration_seconds
    stop_reason = "duration_elapsed"
    pending = bytearray()
    discarding_oversized = False
    bytes_read = 0

    def consume(raw_line: bytes) -> None:
        counts["linesRead"] += 1
        line = raw_line.decode("utf-8", errors="replace")
        record, result = parse_sample(line, run_id=run_id, operation_id=operation_id,
                                     controller_id=controller_id, grid_id=grid_id,
                                     observed_at_utc=utc_now())
        if result == "matched" and record is not None:
            counts["matchedSamples"] += 1
            queue.append(record)
        elif result == "identity_mismatch":
            counts["identityMismatches"] += 1
        elif result == "malformed":
            counts["malformedLines"] += 1
        else:
            counts["filteredLines"] += 1

    with source.open("rb") as stream:
        start_offset = 0 if from_start else source_stat.st_size
        stream.seek(start_offset)
        while True:
            loop_started = time.monotonic()
            if loop_started >= deadline:
                break
            current_stat = source.stat()
            if (current_stat.st_dev, current_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino):
                stop_reason = "source_rotated"
                break
            if current_stat.st_size < stream.tell():
                stop_reason = "source_truncated"
                break
            read_budget = min(MAX_READ_BYTES_PER_POLL, MAX_SOURCE_BYTES_PER_CAPTURE - bytes_read)
            if read_budget <= 0:
                stop_reason = "source_read_limit"
                break
            chunk = stream.read(read_budget)
            bytes_read += len(chunk)
            if chunk:
                data = bytes(pending) + chunk
                pending.clear()
                while data:
                    if discarding_oversized:
                        newline = data.find(b"\n")
                        if newline < 0:
                            data = b""
                            continue
                        data = data[newline + 1:]
                        discarding_oversized = False
                    newline = data.find(b"\n")
                    if newline < 0:
                        if len(data) > MAX_LINE_BYTES:
                            counts["oversizedLines"] += 1
                            discarding_oversized = True
                        else:
                            pending.extend(data)
                        break
                    line_bytes = data[:newline]
                    data = data[newline + 1:]
                    if len(line_bytes) > MAX_LINE_BYTES:
                        counts["oversizedLines"] += 1
                    else:
                        consume(line_bytes)
            remaining = poll_period - (time.monotonic() - loop_started)
            if remaining > 0:
                time.sleep(min(remaining, max(0.0, deadline - time.monotonic())))

    finished_at = utc_now()
    header = {
        "type": "capture-header",
        "schema": "vragecage.motion-telemetry-capture.v1",
        "sourceLog": str(source),
        "startedAtOffset": start_offset,
        "fromStart": from_start,
        "runId": run_id,
        "operationId": operation_id,
        "controllerId": controller_id,
        "gridId": grid_id,
        "startedAtUtc": started_at,
        "requestedDurationSeconds": duration_seconds,
        "requestedPollHz": poll_hz,
        "bufferCapacity": capacity,
        "maximumSourceBytes": MAX_SOURCE_BYTES_PER_CAPTURE,
        "maximumBufferedBytes": maximum_bytes,
        "overflowPolicy": "drop-oldest",
    }
    footer = {
        "type": "capture-footer",
        "finishedAtUtc": finished_at,
        "stopReason": stop_reason,
        "sourceBytesRead": bytes_read,
        **counts,
        "retainedSamples": len(queue.records),
        "droppedSamples": queue.dropped_samples,
        "droppedBytes": queue.dropped_bytes,
        "retainedBytes": queue.retained_bytes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(output, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as writer:
            writer.write(json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n")
            for record, _ in queue.records:
                writer.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            writer.write(json.dumps(footer, sort_keys=True, separators=(",", ":")) + "\n")
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {"output": str(output), **footer}


def positive_id(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("entity ID must be a positive decimal integer") from error
    if parsed <= 0 or parsed > 9_223_372_036_854_775_807:
        raise argparse.ArgumentTypeError("entity ID must fit a positive signed 64-bit integer")
    return parsed


def bounded_number(minimum: float, maximum: float, description: str):
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{description} must be numeric") from error
        if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
            raise argparse.ArgumentTypeError(f"{description} must be {minimum:g}-{maximum:g}")
        return parsed
    return parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture only matching run-bound Voidwright motion telemetry from an existing game log.")
    parser.add_argument("--source-log", required=True, type=Path, help="existing append-only dedicated-server log; capture starts at its current end")
    parser.add_argument("--output", required=True, type=Path, help="new NDJSON output path; existing files are never overwritten")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--controller-id", required=True, type=positive_id)
    parser.add_argument("--grid-id", required=True, type=positive_id)
    parser.add_argument("--from-start", action="store_true",
                        help="include matching samples already present in this exact run log")
    parser.add_argument("--duration-seconds", type=bounded_number(1, MAX_DURATION_SECONDS, "duration"), default=60)
    parser.add_argument("--poll-hz", type=bounded_number(1, MAX_POLL_HZ, "poll rate"), default=20)
    parser.add_argument("--capacity", type=int, default=2048, help="bounded in-memory sample capacity (1-8192)")
    parser.add_argument("--max-buffer-bytes", type=int, default=16 * 1024 * 1024,
                        help="bounded retained NDJSON sample bytes (1-67108864)")
    args = parser.parse_args(argv)
    if not RUN_ID.fullmatch(args.run_id) or not RUN_ID.fullmatch(args.operation_id):
        parser.error("run ID and operation ID must be lowercase 32-hex identities")
    if not 1 <= args.capacity <= MAX_CAPACITY:
        parser.error("capacity must be 1-8192")
    if not 1 <= args.max_buffer_bytes <= MAX_CAPTURE_BYTES:
        parser.error("max buffer bytes must be 1-67108864")
    try:
        summary = capture(args.source_log, args.output, run_id=args.run_id,
                          operation_id=args.operation_id, controller_id=args.controller_id,
                          grid_id=args.grid_id, duration_seconds=args.duration_seconds,
                          poll_hz=args.poll_hz, capacity=args.capacity,
                          maximum_bytes=args.max_buffer_bytes, from_start=args.from_start)
    except (OSError, ValueError) as error:
        print(f"capture failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
