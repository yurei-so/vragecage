#!/usr/bin/env python3
"""Regression test for forwarding a local world source to the remote importer."""

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


CLI_PATH = Path(__file__).resolve().parents[2] / "bin/vragecage"
SPEC = importlib.util.spec_from_loader(
    "vragecage_cli", importlib.machinery.SourceFileLoader("vragecage_cli", str(CLI_PATH)))
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class ImportWorldClientDispatchTests(unittest.TestCase):
    def test_remote_import_upload_forwards_source_and_import_flags(self):
        with tempfile.TemporaryDirectory(prefix="vragecage-import-dispatch-") as temporary:
            source = Path(temporary)
            for name in ("Sandbox.sbc", "Sandbox_config.sbc", "SANDBOX_0_0_0_.sbs"):
                (source / name).write_text("fixture")
            args = CLI.parser().parse_args([
                "--host", "worker.example", "import-world", "fresh-clone", str(source),
                "--engine", "magnetar", "--engine-ready", "--destructive-lab",
            ])
            calls = []

            class Producer:
                class Stream:
                    def close(self):
                        pass

                stdout = Stream()

                def wait(self):
                    return 0

            def fake_run(argv, **kwargs):
                calls.append(argv)
                return type("Result", (), {"returncode": 0})()

            with patch.object(CLI.subprocess, "Popen", return_value=Producer()), \
                 patch.object(CLI.subprocess, "run", side_effect=fake_run), \
                 patch.object(CLI.uuid, "uuid4", return_value=type("UUID", (), {"hex": "a" * 32})()):
                self.assertEqual(CLI.client_main(args), 0)

            remote = next(call[-1] for call in calls if call[0] == "ssh" and
                          "python3 ~/" in call[-1])
            self.assertIn("_worker import-world fresh-clone", remote)
            self.assertIn("--engine magnetar --engine-ready --destructive-lab", remote)
            self.assertNotIn("source is required", remote)
            self.assertEqual(sum(call[0] == "ssh" for call in calls), 4)  # mkdir, extract, import, cleanup


if __name__ == "__main__":
    unittest.main()
