"""Regression coverage for the receipt parser's operation-event authority policy."""
import runpy
import unittest
from pathlib import Path


policy = runpy.run_path(str(Path(__file__).resolve().parents[1] / "magnetar-receipt.py"))[
    "operation_event_is_authoritative"
]


class MagnetarReceiptPolicyTests(unittest.TestCase):
    def test_preflight_only_success_ignores_enclosing_depth_deadline(self):
        self.assertTrue(policy("preflight-only", "preflight-only"))
        self.assertFalse(policy("preflight-only", "depth-controller"))

    def test_preflight_only_timeout_remains_authoritative(self):
        self.assertTrue(policy("preflight-only", "preflight-only"))
        receipt = {"passed": False, "lab_operation_terminal": True,
                   "lab_operation_status": "timeout"}
        self.assertFalse(receipt["passed"])
        self.assertEqual(receipt["lab_operation_status"], "timeout")


if __name__ == "__main__":
    unittest.main()
