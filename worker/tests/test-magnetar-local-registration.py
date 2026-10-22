#!/usr/bin/env python3
"""Exercise Magnetar local registration against the configured save, offline."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRAR = load("register_magnetar_mod", ROOT / "worker/register-magnetar-mod.py")
CLI = load("vragecage_cli_registration_test", ROOT / "bin/vragecage")


class MagnetarLocalRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="magnetar-local-registration-")
        self.worker = Path(self.temp.name) / "worker"
        self.instance = self.worker / "instances" / "fixture"
        self.instance.mkdir(parents=True)
        (self.instance / ".vragecage-engine").write_text("magnetar\n")
        (self.instance / ".vragecage-stage.lock").touch()
        self.world = self.instance / "Saves" / "selected-save"
        self.world.mkdir(parents=True)
        (self.instance / "World").mkdir()
        (self.instance / "SpaceEngineers-Dedicated.cfg").write_text(
            f"<DedicatedServer><LoadWorld>{self.world}</LoadWorld></DedicatedServer>"
        )
        self.name = "LifecycleCandidate"
        self.mod_id = REGISTRAR.synthetic_id(self.name)
        self.cache = self.worker / "magnetar-steam/steamapps/workshop/content/244850" / str(self.mod_id)
        (self.cache / "Data").mkdir(parents=True)
        (self.cache / "Data" / "Candidate.sbc").write_text("<Definitions />\n")
        qname = '<ScriptManager xsi:type="xsd:string">opaque</ScriptManager>'
        sandbox = (
            '<Checkpoint xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<Mods><ModItem><Name>Published</Name><PublishedFileId>123</PublishedFileId></ModItem>'
            f'<ModItem><Name>{self.mod_id}.sbm</Name><PublishedFileId>{self.mod_id}</PublishedFileId>'
            '</ModItem></Mods>' + qname + '</Checkpoint>'
        )
        config = (
            '<WorldConfiguration xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<Mods><ModItem><Name>Published</Name><PublishedFileId>123</PublishedFileId></ModItem>'
            f'<ModItem><Name>{self.mod_id}.sbm</Name><PublishedFileId>{self.mod_id}</PublishedFileId>'
            '</ModItem></Mods></WorldConfiguration>'
        )
        (self.world / "Sandbox.sbc").write_text(sandbox)
        (self.world / "Sandbox_config.sbc").write_text(config)
        (self.instance / "World" / "Sandbox.sbc").write_text("<Decoy />")
        (self.instance / "World" / "Sandbox_config.sbc").write_text("<Decoy />")
        self.cache_before = REGISTRAR.tree_hash(self.cache)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def q_instance(self) -> tuple[Path, Path, Path]:
        instance = self.worker / "instances" / REGISTRAR.Q_LOCAL_VEHICLE_TEST_INSTANCE
        instance.mkdir(parents=True)
        (instance / ".vragecage-engine").write_text("magnetar\n")
        (instance / ".vragecage-stage.lock").touch()
        world = instance / "World"
        world.mkdir()
        (world / "Sandbox.sbc").write_text("<Checkpoint><Mods /></Checkpoint>")
        (world / "Sandbox_config.sbc").write_text("<WorldConfiguration><Mods /></WorldConfiguration>")
        (instance / "SpaceEngineers-Dedicated.cfg").write_text(
            f"<DedicatedServer><LoadWorld>{world}</LoadWorld></DedicatedServer>"
        )
        source = self.worker / "incoming" / "q-voidwright-source"
        (source / "Data").mkdir(parents=True)
        (source / "Data" / "Voidwright.sbc").write_text("<Definitions />\n")
        return instance, world, source

    def test_default_synthetic_id_is_unchanged_and_override_is_q_scoped(self) -> None:
        self.assertEqual(REGISTRAR.resolve_synthetic_id("fixture", "Voidwright"),
                         REGISTRAR.synthetic_id("Voidwright"))
        self.assertEqual(
            REGISTRAR.resolve_synthetic_id(
                REGISTRAR.Q_LOCAL_VEHICLE_TEST_INSTANCE, "Voidwright",
                REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID),
            REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID,
        )
        for instance_name, mod_name, requested_id in (
            ("local-vehicle-steering-v4-20261021-p", "Voidwright",
             REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID),
            (REGISTRAR.Q_LOCAL_VEHICLE_TEST_INSTANCE, "OtherMod",
             REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID),
            (REGISTRAR.Q_LOCAL_VEHICLE_TEST_INSTANCE, "Voidwright", 90027687454063603),
        ):
            with self.assertRaises(ValueError):
                REGISTRAR.resolve_synthetic_id(instance_name, mod_name, requested_id)

    def test_q_override_refuses_existing_cache_target(self) -> None:
        instance, _, source = self.q_instance()
        target = self.worker / "magnetar-steam/steamapps/workshop/content/244850" / str(
            REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID)
        (target / "Data").mkdir(parents=True)
        (target / "Data" / "existing.sbc").write_text("keep")
        before = REGISTRAR.tree_hash(target)
        with patch.object(REGISTRAR.sys, "argv", [
            str(ROOT / "worker/register-magnetar-mod.py"), str(instance), "Voidwright",
            str(source), "--synthetic-id", str(REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID),
        ]), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            REGISTRAR.main()
        self.assertEqual(REGISTRAR.tree_hash(target), before)
        self.assertFalse((instance / ".vragecage-mod-stage.json").exists())
        self.assertFalse((instance / "Mods/Voidwright").exists())

    def test_q_stage_receipt_sources_profile_and_mirror_bind_same_id_and_hash(self) -> None:
        instance, _, source = self.q_instance()
        with patch.object(REGISTRAR.sys, "argv", [
            str(ROOT / "worker/register-magnetar-mod.py"), str(instance), "Voidwright",
            str(source), "--synthetic-id", str(REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID),
        ]), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(REGISTRAR.main(), 0)
        receipt = json.loads(output.getvalue())
        target = self.worker / "magnetar-steam/steamapps/workshop/content/244850" / str(
            REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID)
        self.assertEqual(receipt["synthetic_workshop_id"], REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID)
        self.assertEqual(receipt["name"], "Voidwright")
        self.assertEqual(receipt["path"], str(target))
        self.assertEqual(receipt["tree_sha256"], REGISTRAR.tree_hash(target))
        self.assertEqual(REGISTRAR.tree_hash(instance / "Mods/Voidwright"), receipt["tree_sha256"])
        sources = ET.parse(instance / "MagnetarConfig/Sources/sources.xml").getroot()
        self.assertEqual([(node.findtext("Name"), node.findtext("ID"))
                          for node in sources.findall("ModSources/Mod")],
                         [("Voidwright", str(REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID))])
        profile = ET.parse(instance / "MagnetarConfig/Profiles/Current.xml").getroot()
        self.assertEqual([node.text for node in profile.findall("Mods/unsignedLong")],
                         [str(REGISTRAR.Q_LOCAL_VEHICLE_TEST_MOD_ID)])

    def test_reuses_cache_and_removes_only_candidate_from_configured_save(self) -> None:
        with patch.object(REGISTRAR.sys, "argv", [
            str(ROOT / "worker/register-magnetar-mod.py"), str(self.instance), self.name,
            str(self.cache), "--reuse-existing-cache",
        ]), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(REGISTRAR.main(), 0)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt["load_world"], str(self.world))
        self.assertEqual(receipt["removed_unpublished_world_entries"], 2)
        self.assertTrue(receipt["cache_reused_without_writes"])
        self.assertEqual(REGISTRAR.tree_hash(self.cache), self.cache_before)
        self.assertEqual((self.instance / "World/Sandbox.sbc").read_text(), "<Decoy />")

        for filename in ("Sandbox.sbc", "Sandbox_config.sbc"):
            path = self.world / filename
            raw = path.read_text(encoding="utf-8")
            self.assertIn('xmlns:xsd="http://www.w3.org/2001/XMLSchema"', raw)
            self.assertIn('xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"', raw)
            root = ET.parse(path).getroot()
            self.assertEqual(
                [node.findtext("PublishedFileId") for node in root.findall("Mods/ModItem")], ["123"]
            )
        self.assertIn('xsi:type="xsd:string"', (self.world / "Sandbox.sbc").read_text())

        stage = json.loads((self.instance / ".vragecage-mod-stage.json").read_text())
        CLI.worker_root = lambda: self.worker
        self.assertEqual(CLI._verify_magnetar_local_registration(self.instance, self.world, stage), self.mod_id)

    def test_conflicting_synthetic_entry_is_rejected_without_cache_change(self) -> None:
        path = self.world / "Sandbox_config.sbc"
        path.write_text(
            '<WorldConfiguration><Mods><ModItem><Name>WrongName.sbm</Name>'
            f'<PublishedFileId>{self.mod_id}</PublishedFileId></ModItem></Mods></WorldConfiguration>'
        )
        before = path.read_bytes()
        with patch.object(REGISTRAR.sys, "argv", [
            str(ROOT / "worker/register-magnetar-mod.py"), str(self.instance), self.name,
            str(self.cache), "--reuse-existing-cache",
        ]), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            REGISTRAR.main()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(REGISTRAR.tree_hash(self.cache), self.cache_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
