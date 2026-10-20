"""Severe risks: lost author edits, changed identity and overwritten source."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
from editor_project import execute
from rookframe_authoring import initialize


class EditorProjectTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name)
        initialize(self.project, "Author package", "optional", content_only=True)
        self.path = self.project / "rookframe.json"
        self.before = self.path.read_bytes()
        self.manifest = json.loads(self.before)

    def request(self, **extra):
        return {"expected": hashlib.sha256(self.before).hexdigest(),
                "manifest": self.manifest, **extra}

    def test_conflict_and_identity_changes_preserve_disk(self):
        self.path.write_bytes(self.before + b"\n")
        with self.assertRaisesRegex(ValueError, "outside"):
            execute(self.project, self.request())
        self.assertEqual(self.path.read_bytes(), self.before + b"\n")
        self.path.write_bytes(self.before)
        self.manifest["id"] = "different"
        with self.assertRaisesRegex(ValueError, "identity"):
            execute(self.project, self.request())
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_failed_transaction_keeps_manifest_and_rolls_back_only_new_source(self):
        with patch("editor_project.replace_file", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                execute(self.project, self.request(operation="behavior"))
        self.assertEqual(self.path.read_bytes(), self.before)
        source = self.project / "rookframe/packages" / self.manifest["id"] / "logic/implementation.gd"
        self.assertFalse(source.exists())
        source.write_text("# author source\n")
        self.manifest.pop("implementation", None)
        with self.assertRaisesRegex(ValueError, "existing file"):
            execute(self.project, self.request(operation="behavior"))
        self.assertEqual(source.read_text(), "# author source\n")
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_presentation_source_is_optional_library_free_and_metadata_is_preserved(self):
        self.manifest["publisherMetadata"] = {"keep": "this"}
        self.manifest["presentations"] = [{"id": "phone", "entryPoint": "ui/phone.gd",
                                            "experiences": ["phone"]}]
        execute(self.project, self.request(operation="presentation", entryPoint="ui/phone.gd"))
        root = self.project / "rookframe/packages" / self.manifest["id"]
        script = (root / "ui/phone.gd").read_text()
        self.assertIn("extends Control", script)
        self.assertNotIn("rookframe/sdk", script)
        self.assertTrue((root / "ui/phone.tscn").exists())
        self.assertEqual(json.loads(self.path.read_bytes())["publisherMetadata"], {"keep": "this"})
        self.assertFalse((self.project / "rookframe/sdk").exists())

    def test_godot_float_json_keeps_integer_manifest_contracts(self):
        original = json.loads(self.before)
        original["publisherMetadata"] = {"integer": 9007199254740993}
        self.before = json.dumps(original).encode()
        self.path.write_bytes(self.before)
        self.manifest["publisherMetadata"] = {"integer": float(9007199254740993)}
        self.manifest["manifestVersion"] = 2.0
        self.manifest["sdk"]["minimumRevision"] = 34.0
        execute(self.project, self.request())
        saved = json.loads(self.path.read_bytes())
        self.assertIs(type(saved["manifestVersion"]), int)
        self.assertIs(type(saved["sdk"]["minimumRevision"]), int)
        self.assertEqual(saved["publisherMetadata"]["integer"], 9007199254740993)

    def test_removing_records_preserves_large_metadata_on_retained_identities(self):
        large = 9007199254740993
        original = json.loads(self.before)
        original["content"] = [
            {"kind": "visual", "entries": [
                {"id": "a", "publisherMetadata": {"integer": large}},
                {"id": "b", "publisherMetadata": {"integer": large}}]},
            {"kind": "system", "targetContractId": "first", "entries": [{"id": "c"}]},
            {"kind": "system", "targetContractId": "second", "entries": [
                {"id": "d", "publisherMetadata": {"integer": large}}]}]
        original["presentations"] = [
            {"id": "desktop"}, {"id": "phone", "publisherMetadata": {"integer": large}}]
        self.before = json.dumps(original).encode()
        self.path.write_bytes(self.before)
        self.manifest = json.loads(self.before, parse_int=float)
        self.manifest["content"][0]["entries"].pop(0)
        self.manifest["content"].pop(1)
        self.manifest["presentations"].pop(0)
        execute(self.project, self.request())
        saved = json.loads(self.path.read_bytes())
        for record in (saved["content"][0]["entries"][0], saved["content"][1]["entries"][0],
                       saved["presentations"][0]):
            self.assertEqual(record["publisherMetadata"]["integer"], large)


if __name__ == "__main__":
    unittest.main()
