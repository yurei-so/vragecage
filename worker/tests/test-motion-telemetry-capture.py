from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_motion_telemetry as telemetry


RUN = "0123456789abcdef0123456789abcdef"
OPERATION = "fedcba9876543210fedcba9876543210"
CONTROLLER = 123456789012345678
GRID = 223456789012345678


def sample_line(sequence: int, *, run_id: str = RUN, operation_id: str = OPERATION,
                controller_id: int = CONTROLLER, grid_id: int = GRID) -> bytes:
    return (
        "2026-10-19 10:30:40.123 - Thread: 1 -> Voidwright evidence: "
        "type=server-control operation=" + operation_id +
        " action=motion-telemetry sample=running controllerId=" + str(controller_id) +
        " gridId=" + str(grid_id) + " runId=" + run_id +
        " sampleSequence=" + str(sequence) +
        " capturedAtUtc=2026-10-19T15:30:40.123456Z elapsedTicks=" + str(sequence) + "\n"
    ).encode()


class MotionTelemetryCaptureTests(unittest.TestCase):
    def test_parser_keeps_exact_identity_timestamps_and_source_line(self):
        record, result = telemetry.parse_sample(
            sample_line(42).decode().rstrip(), run_id=RUN, operation_id=OPERATION,
            controller_id=CONTROLLER, grid_id=GRID,
            observed_at_utc="2026-10-19T15:30:41.000000Z",
        )
        self.assertEqual(result, "matched")
        self.assertEqual(record["runId"], RUN)
        self.assertEqual(record["operationId"], OPERATION)
        self.assertEqual(record["controllerId"], CONTROLLER)
        self.assertEqual(record["gridId"], GRID)
        self.assertEqual(record["sampleSequence"], 42)
        self.assertEqual(record["capturedAtUtc"], "2026-10-19T15:30:40.123456Z")
        self.assertEqual(record["sourceTimestampUnqualified"], "2026-10-19 10:30:40.123")

    def test_parser_rejects_identity_mismatch_and_malformed_samples(self):
        for overrides in (
            {"run_id": "f" * 32}, {"operation_id": "e" * 32},
            {"controller_id": CONTROLLER + 1}, {"grid_id": GRID + 1},
        ):
            record, result = telemetry.parse_sample(
                sample_line(1, **overrides).decode().rstrip(), run_id=RUN,
                operation_id=OPERATION, controller_id=CONTROLLER, grid_id=GRID,
                observed_at_utc="2026-10-19T15:30:41Z",
            )
            self.assertIsNone(record)
            self.assertEqual(result, "identity_mismatch")
        malformed = sample_line(1).decode().replace("capturedAtUtc=2026-10-19T15:30:40.123456Z", "capturedAtUtc=invalid")
        _, result = telemetry.parse_sample(
            malformed.rstrip(), run_id=RUN, operation_id=OPERATION,
            controller_id=CONTROLLER, grid_id=GRID, observed_at_utc="2026-10-19T15:30:41Z",
        )
        self.assertEqual(result, "malformed")

    def test_bounded_buffer_drops_oldest_and_accounts_bytes(self):
        buffer = telemetry.BoundedCapture(capacity=1, maximum_bytes=4096)
        first = {"type": "motion-sample", "sampleSequence": 1, "rawEvidence": "a"}
        second = {"type": "motion-sample", "sampleSequence": 2, "rawEvidence": "b"}
        self.assertTrue(buffer.append(first))
        first_size = len((json.dumps(first, sort_keys=True, separators=(",", ":")) + "\n").encode())
        self.assertTrue(buffer.append(second))
        self.assertEqual([record["sampleSequence"] for record, _ in buffer.records], [2])
        self.assertEqual(buffer.dropped_samples, 1)
        self.assertEqual(buffer.dropped_bytes, first_size)

    def test_capture_reads_only_new_matching_lines_and_reports_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "server.log"
            output = Path(directory) / "capture.ndjson"
            source.write_bytes(b"historical line must not be included\n")
            writer_errors: list[Exception] = []

            def append_samples() -> None:
                try:
                    time.sleep(0.03)
                    with source.open("ab") as stream:
                        stream.write(sample_line(1))
                        stream.write(sample_line(2))
                        stream.flush()
                except Exception as error:
                    writer_errors.append(error)

            writer = threading.Thread(target=append_samples)
            writer.start()
            summary = telemetry.capture(
                source, output, run_id=RUN, operation_id=OPERATION,
                controller_id=CONTROLLER, grid_id=GRID, duration_seconds=0.2,
                poll_hz=60, capacity=1, maximum_bytes=4096,
            )
            writer.join(timeout=1)
            self.assertFalse(writer.is_alive())
            self.assertEqual(writer_errors, [])
            self.assertEqual(summary["matchedSamples"], 2)
            self.assertEqual(summary["retainedSamples"], 1)
            self.assertEqual(summary["droppedSamples"], 1)
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(records[0]["type"], "capture-header")
            self.assertEqual(records[0]["runId"], RUN)
            self.assertEqual(records[1]["type"], "motion-sample")
            self.assertEqual(records[1]["sampleSequence"], 2)
            self.assertEqual(records[2]["type"], "capture-footer")
            self.assertEqual(records[2]["droppedSamples"], 1)

    def test_from_start_can_recover_samples_written_before_collector_started(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "server.log"
            output = Path(directory) / "capture.ndjson"
            source.write_bytes(sample_line(0))
            summary = telemetry.capture(
                source, output, run_id=RUN, operation_id=OPERATION,
                controller_id=CONTROLLER, grid_id=GRID, duration_seconds=0.05,
                poll_hz=60, capacity=2, maximum_bytes=4096, from_start=True,
            )
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(summary["matchedSamples"], 1)
            self.assertEqual(records[0]["startedAtOffset"], 0)
            self.assertTrue(records[0]["fromStart"])
            self.assertEqual(records[1]["sampleSequence"], 0)


if __name__ == "__main__":
    unittest.main()
