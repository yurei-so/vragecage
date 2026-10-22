"""Exercise wait lifecycle decisions without starting a game or service."""
import argparse
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


command_wait = runpy.run_path(str(Path(__file__).resolve().parents[2] / "bin/vragecage"))["command_wait"]


class WaitLifecycleTests(unittest.TestCase):
    def run_wait(self, receipts, *, active=True, observations=0, requirements=None):
        emitted = Mock()
        read = Mock(side_effect=[(receipt, 0 if receipt.get("passed") else 1) for receipt in receipts])
        args = argparse.Namespace(instance="fixture", timeout=30,
                                  observations=observations, require_observation=requirements or [])
        with patch.dict(command_wait.__globals__, read_receipt=read, emit=emitted), \
                patch("subprocess.run", return_value=SimpleNamespace(returncode=0 if active else 1)) as service, \
                patch("time.sleep") as sleep:
            result = command_wait(args)
        return result, emitted.call_args.args[0], sleep.call_count, service.call_count

    def test_terminal_failure_returns_receipt_without_polling(self):
        for status in ("timeout", "stalled", "attitude-limit", "capture-failed",
                       "depth-overspeed", "depth-startup-motion", "depth-actuation-stalled",
                       "depth-stalled", "human-connected", "players-api-unavailable"):
            with self.subTest(status=status):
                receipt = {"passed": False, "lab_operation_terminal": True,
                           "lab_operation_status": status}
                result, output, sleeps, service_checks = self.run_wait([receipt], observations=1)
                self.assertEqual(result, 1)
                self.assertEqual(output["status"], "lab_operation_failed")
                self.assertIs(output["receipt"], receipt)
                self.assertEqual((sleeps, service_checks), (0, 0))

    def test_pending_operation_is_polled_until_terminal_failure(self):
        pending = {"passed": False, "lab_operation_terminal": False, "lab_operation_status": "running"}
        terminal = {**pending, "lab_operation_terminal": True, "lab_operation_status": "timeout"}
        result, output, sleeps, _ = self.run_wait([pending, terminal])
        self.assertEqual(result, 1)
        self.assertEqual(output["receipt"]["lab_operation_status"], "timeout")
        self.assertEqual(sleeps, 1)

    def test_success_still_requires_requested_evidence(self):
        ready = {"passed": True, "observations": [], "lab_operation_events": ["status=complete"]}
        observed = {**ready, "observations": ["vehicle=rover"]}
        result, output, sleeps, _ = self.run_wait([ready, observed], observations=1,
                                                  requirements=["status=complete", "vehicle=rover"])
        self.assertEqual(result, 0)
        self.assertIs(output, observed)
        self.assertEqual(sleeps, 1)

    def test_stopped_nonterminal_operation_is_not_reported_complete(self):
        receipt = {"passed": False, "lab_operation_terminal": False}
        result, output, sleeps, _ = self.run_wait([receipt], active=False)
        self.assertEqual(result, 1)
        self.assertEqual(output["status"], "stopped_before_ready")
        self.assertEqual(sleeps, 0)


if __name__ == "__main__":
    unittest.main()
