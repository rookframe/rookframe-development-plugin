from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
import rookframe_compiled_scenery as scenery
import rookframe_editable_preparation as editable


PACKAGE = "0404eb56-ef27-4b1a-8385-27e4e3ec907e"


class EditableSceneryPublicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name)
        self.source = self.project / f"rookframe/packages/{PACKAGE}"
        self.source.mkdir(parents=True)
        (self.project / "project.godot").write_text("config_version=5\n")
        self.manifest = self.project / "rookframe.json"
        self.manifest.write_text(json.dumps({"id": PACKAGE}))
        self.config = self.project / editable.CONFIG
        self.config.parent.mkdir()
        self.config.write_text(json.dumps({"developPackage": PACKAGE}))
        self.snapshot = self.project / editable.DECLARATIONS
        self.destination = self.project / f"{scenery.EDITABLE}/{PACKAGE}"
        patcher = patch.object(editable, "prepare_development_data", return_value={
            "packageData": {"packageId": PACKAGE, "indexSha256": "d" * 64}, "changedPaths": []})
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self):
        return editable.prepare_editable_package(self.project, Path("godot"), "engine", PACKAGE)

    def stage(self, *args, **_options):
        destination = args[4]
        body = self.manifest.read_bytes()
        digest = sha256(body).hexdigest()
        payload = f"{scenery.ASSETS}/{digest}.rfscenery"
        scenery.replace_bytes(destination / payload, body)
        scenery.replace_bytes(destination / scenery.INDEX, body)
        return scenery.Compilation({
            "format": scenery.FORMAT, "indexFormat": scenery.INDEX_FORMAT,
            "compilerVersion": 1, "indexPath": scenery.INDEX, "indexSha256": digest,
        }, {payload: digest, scenery.INDEX: digest}, {}, [])

    def test_publishes_members_first_and_reuses_only_a_complete_generation(self):
        with patch.object(editable, "compile_scenery", side_effect=self.stage) as compile_native:
            self.assertTrue(self.prepare()["updated"])
            previous = json.loads(self.snapshot.read_bytes())
            declaration = previous["compiledScenery"]
            self.assertEqual(declaration["packageId"], PACKAGE)
            self.assertEqual(declaration["indexSha256"],
                             sha256((self.destination / scenery.INDEX).read_bytes()).hexdigest())
            self.assertFalse(self.prepare()["updated"])
            compile_native.assert_called_once()
            (self.destination / scenery.INDEX).unlink()
            self.assertFalse(self.prepare()["updated"])
            self.assertTrue((self.destination / scenery.INDEX).is_file())
            self.assertEqual(compile_native.call_count, 2)

    def test_stale_compile_keeps_last_index_and_declaration(self):
        with patch.object(editable, "compile_scenery", side_effect=self.stage):
            self.prepare()
        previous = self.snapshot.read_bytes()
        index = (self.destination / scenery.INDEX).read_bytes()

        def stale(*args, **options):
            prepared = self.stage(*args, **options)
            (self.source / "changed.tres").write_text("edit during compilation")
            return prepared

        self.manifest.write_text(json.dumps({"id": PACKAGE, "version": "1.0.1"}))
        with patch.object(editable, "compile_scenery", side_effect=stale):
            with self.assertRaisesRegex(ValueError, "SCENERY.STALE"):
                self.prepare()
        self.assertEqual(self.snapshot.read_bytes(), previous)
        self.assertEqual((self.destination / scenery.INDEX).read_bytes(), index)

    def test_no_compiled_output_clears_pointer_after_complete_refresh(self):
        with patch.object(editable, "compile_scenery", side_effect=self.stage):
            self.prepare()
        self.manifest.write_text(json.dumps({"id": PACKAGE, "version": "1.0.1"}))
        with patch.object(editable, "compile_scenery", return_value=scenery.Compilation({}, {}, {}, [])):
            self.prepare()
        snapshot = json.loads(self.snapshot.read_bytes())
        self.assertNotIn("compiledScenery", snapshot)
        self.assertIn("packageData", snapshot)
        self.assertFalse((self.destination / scenery.INDEX).exists())

    def test_invalid_gameplay_data_retains_its_index_and_refreshes_native_paths(self):
        native = f"res://rookframe/packages/{PACKAGE}/scene.tscn"
        gameplay = self.project / f"{editable.DEV_DATA}/{PACKAGE}/{editable.INDEX_PATH}"
        contents = b"previously accepted gameplay index"
        scenery.replace_bytes(gameplay, contents)
        data = {"packageData": {"packageId": PACKAGE, "indexSha256": sha256(contents).hexdigest()},
                "changedPaths": []}
        with patch.object(editable, "compile_scenery", side_effect=self.stage):
            with patch.object(editable, "prepare_development_data", return_value=data):
                self.prepare()
            self.manifest.write_text(json.dumps({"id": PACKAGE, "version": "1.0.1"}))
            with patch.object(editable, "prepare_development_data", side_effect=ValueError("DATA.VALUE")):
                result = editable.prepare_editable_package(self.project, Path("godot"), "engine", PACKAGE,
                                                           paths=(native,))
        snapshot = json.loads(self.snapshot.read_bytes())
        self.assertEqual(result, {"dataError": "DATA.VALUE", "updated": True})
        self.assertEqual(snapshot["packageData"], data["packageData"])
        self.assertEqual(snapshot["changedPaths"], [native])
        self.assertNotIn("compiledScenery", snapshot)
        self.assertEqual(gameplay.read_bytes(), contents)

    def test_portable_inventory_rejects_unicode_alias_and_reserved_extension(self):
        (self.source / "Straße.tres").touch()
        (self.source / "STRASSE.tres").touch()
        with self.assertRaisesRegex(ValueError, "SCENERY.REFERENCE.*aliases"):
            scenery.inputs(self.source, "source")
        (self.source / "STRASSE.tres").unlink()
        (self.source / "private.RFSCENERY").touch()
        with self.assertRaisesRegex(ValueError, "SCENERY.REFERENCE.*reserved"):
            scenery.inputs(self.source, "source")

    def test_export_requires_exact_framed_member_inventory(self):
        root = "res://rookframe/package-artifacts/example/package/"
        path = root.removeprefix("res://") + scenery.INDEX
        record = b"native framed index from the staged profile"
        compiled = scenery.Compilation({}, {scenery.INDEX: sha256(record).hexdigest()}, {}, [])
        scenery.verify_export({path: record}, root, compiled)
        with self.assertRaisesRegex(RuntimeError, "SCENERY.DIGEST"):
            scenery.verify_export({path: record + b"x"}, root, compiled)
        with self.assertRaisesRegex(RuntimeError, "SCENERY.DIGEST"):
            scenery.verify_export({path: record, "orphan.rfscenery": record}, root, compiled)

    def test_export_keeps_native_vegetation_input_even_when_no_lods_are_authored(self):
        root = "res://rookframe/package-artifacts/example/package/"
        compiled = scenery.Compilation({}, {}, {}, [], [
            {"artwork": "content/source.res", "authoredLods": False}])
        with self.assertRaisesRegex(RuntimeError, "SCENERY.REFERENCE.*content/source.res"):
            scenery.verify_export({}, root, compiled)
        scenery.verify_export({root[6:] + "content/source.res.remap": b"native source remap"}, root, compiled)

    def use_vegetation(self):
        (self.source / "grass.tres").write_text("authored vegetation source")
        self.manifest.write_text(json.dumps({"id": PACKAGE, "content": [{"entries": [
            {"id": "grass", "type": "grass", "payload": "grass.tres"}]}]}))

    def test_client_receipt_follows_publication_and_is_scoped_to_renderer(self):
        self.use_vegetation()

        def app(_godot, _project, _pack, renderer, _report):
            declaration = json.loads(self.snapshot.read_bytes())["compiledScenery"]
            index = self.destination / scenery.INDEX
            self.assertEqual(sha256(index.read_bytes()).hexdigest(), declaration["indexSha256"])
            return {"status": "ready", "packageId": PACKAGE, "renderer": renderer}

        with patch.object(editable, "compile_scenery", side_effect=self.stage):
            with patch.object(editable, "prepare_client_lods", side_effect=app) as application:
                self.assertEqual(self.prepare()["lodPreparation"]["status"], "ready")
                self.prepare()
                application.assert_called_once()
                editable.prepare_editable_package(self.project, Path("godot"), "engine", PACKAGE,
                                                   renderer="mobile")
                self.assertEqual(application.call_count, 2)

    def test_edit_during_client_preparation_does_not_accept_a_receipt(self):
        self.use_vegetation()

        def outdated(*_args):
            (self.source / "grass.tres").write_text("new author edit")
            return {"status": "ready"}

        with patch.object(editable, "compile_scenery", side_effect=self.stage):
            with patch.object(editable, "prepare_client_lods", side_effect=outdated):
                with self.assertRaisesRegex(ValueError, "SCENERY.STALE"):
                    self.prepare()
        self.assertFalse((self.destination / "lod-preparation.json").exists())

    def test_calls_ordinary_application_with_graphics_and_preserves_its_fallback_status(self):
        report = self.project / "receipt.json"
        body = {"status": "source_fallback", "diagnostic": "The source artwork remains available."}

        def run(command, **options):
            self.assertEqual(command[:7], [
                "godot", "--path", str(self.project), "--main-pack", "Rookframe.pck",
                "--scene", editable.APPLICATION_SCENE])
            self.assertEqual(command[7:9], ["--rendering-method", "mobile"])
            self.assertEqual(command[-4:], [
                "--", "--prepare-editable-lods", editable.DEVELOPMENT_ARGUMENT, f"--report={report}"])
            self.assertNotIn("ROOKFRAME_VILLAGE_TRIAL", options["env"])
            report.write_text(json.dumps(body))
            return CompletedProcess(command, 0, "", "")

        with patch.object(editable.subprocess, "run", side_effect=run):
            self.assertEqual(editable.prepare_client_lods(Path("godot"), self.project,
                                                          Path("Rookframe.pck"), "mobile", report), body)


if __name__ == "__main__":
    unittest.main()
