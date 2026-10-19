import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
import rookframe_development as development


class InstalledApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "author"
        (self.project / ".rookframe").mkdir(parents=True)
        self.application = self.root / "Installed Rookframe"
        self.managed = self.application / "data_Rookframe_windows_x86_64"
        self.managed.mkdir(parents=True)
        self.executable = self.application / "Rookframe.exe"
        self.executable.write_bytes(b"ordinary exported executable")
        (self.application / "Rookframe.pck").write_bytes(b"ordinary exported pack")
        (self.managed / "Rookframe.dll").write_bytes(b"ordinary published application")
        self.native = "libwebrtc_native.windows.template_release.x86_64.dll"
        (self.application / self.native).write_bytes(b"ordinary native extension")
        self.engine = "4.7.2.stable.mono.official.ed1daf0bf"
        for patcher in [patch.object(development, "host_platform", return_value="windows"),
                        patch.object(development.subprocess, "check_output", return_value=self.engine)]:
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.project / "rookframe.json").write_text(json.dumps({"id": "author", "kind": "system-extension"}))
        (self.project / ".rookframe/development.json").write_text(json.dumps({
            "schema": development.CONFIG_SCHEMA, "worldName": "My World", "developPackage": "author"}))
        (self.project / "my_scene.tscn").write_text("Author scene")

    def prepare(self):
        return development.prepare(self.project, self.executable, self.engine, "x86_64")

    def test_uses_installed_pack_directly_and_recovers_deleted_library_cache(self):
        prepared = self.prepare()
        self.assertIn(json.dumps((self.application / "Rookframe.pck").as_posix()), prepared["runArguments"])
        self.assertEqual(list(self.project.glob("*.pck")), [])
        target = self.project / development.MANAGED_ROOT / "Rookframe.dll"
        self.assertEqual(target.read_bytes(), (self.managed / "Rookframe.dll").read_bytes())
        target.unlink()
        self.prepare()
        self.assertEqual(target.read_bytes(), b"ordinary published application")
        self.assertEqual((self.project / "my_scene.tscn").read_text(), "Author scene")

    def test_incomplete_installation_does_not_modify_prepared_libraries(self):
        self.prepare()
        (self.application / "Rookframe.pck").unlink()
        (self.managed / "Rookframe.dll").write_bytes(b"updated")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.prepare()
        self.assertEqual((self.project / development.MANAGED_ROOT / "Rookframe.dll").read_bytes(), b"ordinary published application")

    def test_requires_matching_stock_mono_editor_and_architecture(self):
        with self.assertRaisesRegex(ValueError, "ENGINE"):
            development.prepare(self.project, self.executable, "another-engine", "x86_64")
        with self.assertRaisesRegex(ValueError, "architecture"):
            development.prepare(self.project, self.executable, self.engine, "arm64")
        self.assertFalse((self.project / ".godot").exists())

    def test_upgrade_removes_only_obsolete_owned_libraries(self):
        (self.managed / "old.dll").write_bytes(b"old library")
        self.prepare()
        (self.managed / "old.dll").unlink()
        self.prepare()
        self.assertFalse((self.project / development.MANAGED_ROOT / "old.dll").exists())
        self.assertEqual((self.project / "my_scene.tscn").read_text(), "Author scene")

    def test_interrupted_preparation_can_be_retried(self):
        with patch.object(development, "atomic_copy", side_effect=OSError("Interrupted")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.prepare()["status"], "ready")

    def test_native_collision_preserves_author_files(self):
        target = self.project / development.NATIVE_ROOT / self.native
        target.parent.mkdir(parents=True)
        target.write_bytes(b"author-owned native file")
        with self.assertRaisesRegex(ValueError, "COLLISION"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"author-owned native file")

    def test_tampered_ownership_cannot_delete_files_outside_the_project(self):
        self.prepare()
        outside = self.root / "keep.dll"
        outside.write_bytes(b"keep")
        record = self.project / ".rookframe/installed-application.json"
        previous = json.loads(record.read_text())
        previous["files"].append(development.MANAGED_ROOT + "../../../../../../keep.dll")
        record.write_text(json.dumps(previous))
        with self.assertRaisesRegex(ValueError, "PATH"):
            self.prepare()
        self.assertEqual(outside.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
