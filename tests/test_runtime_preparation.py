import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
import rookframe_development as development


class DevelopmentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "author"
        (self.project / ".rookframe").mkdir(parents=True)
        self.bundle = self.root / "runtime"
        (self.bundle / "managed").mkdir(parents=True)
        (self.bundle / "Rookframe.pck").write_bytes(b"compiled pack")
        (self.bundle / "managed/Rookframe.dll").write_bytes(b"compiled application")
        native_name = "libwebrtc_native.windows.template_debug.x86_64.dll" if development.host_platform() == "windows" else (
            "libwebrtc_native.linux.template_debug.x86_64.so" if development.host_platform() == "linux" else
            "libwebrtc_native.macos.template_debug.universal.framework/libwebrtc_native.macos.template_debug.universal.dylib")
        self.native = "native/" + native_name
        (self.bundle / self.native).parent.mkdir(parents=True)
        (self.bundle / self.native).write_bytes(b"compiled native extension")
        self.engine = "4.7.2.stable.mono.official.ed1daf0bf"
        self.bundle_manifest = {"schema": development.BUNDLE_SCHEMA, "engine": self.engine,
            "features": [development.REQUIRED_FEATURE],
            "platform": development.host_platform(), "architecture": "x86_64", "version": "1.0.0", "files": {
                name: development.digest(self.bundle / name)
                for name in ["Rookframe.pck", "managed/Rookframe.dll", self.native]}}
        self.save_bundle()
        (self.project / "rookframe.json").write_text(json.dumps({"id": "author", "kind": "system-extension"}))
        (self.project / ".rookframe/development.json").write_text(json.dumps({
            "schema": development.CONFIG_SCHEMA, "worldName": "My World", "developPackage": "author"}))
        (self.project / "my_scene.tscn").write_text("Author scene")

    def save_bundle(self):
        (self.bundle / "rookframe-development-runtime.json").write_text(json.dumps(self.bundle_manifest))

    def prepare(self):
        return development.prepare(self.project, self.bundle / "rookframe-development-runtime.json", self.engine, "x86_64")

    def test_prepares_actual_project_and_recovers_removed_godot_cache(self):
        self.assertEqual(self.prepare()["status"], "ready")
        target = self.project / ".godot/mono/temp/bin/Debug/Rookframe.dll"
        target.unlink()
        self.prepare()
        self.assertEqual(target.read_bytes(), b"compiled application")
        self.assertEqual((self.project / "my_scene.tscn").read_text(), "Author scene")

    def test_rejects_damaged_bundle_before_changing_a_prepared_runtime(self):
        self.prepare()
        (self.bundle / "Rookframe.pck").write_bytes(b"partial update")
        with self.assertRaisesRegex(ValueError, "Damaged"):
            self.prepare()
        self.assertEqual((self.project / development.PACK).read_bytes(), b"compiled pack")

    def test_requires_exact_stock_mono_engine_host_platform_and_live_declarations(self):
        for field, wrong in [("engine", "4.7.stable.official"), ("platform", "other-os"), ("architecture", "arm64"), ("features", [])]:
            old = self.bundle_manifest[field]
            self.bundle_manifest[field] = wrong
            self.save_bundle()
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse((self.project / development.PACK).exists())
            self.bundle_manifest[field] = old

    def test_rejects_path_escape_and_does_not_overwrite_an_author_pack(self):
        self.bundle_manifest["files"]["managed/../../escape"] = "any"
        self.save_bundle()
        with self.assertRaisesRegex(ValueError, "PATH"):
            self.prepare()
        del self.bundle_manifest["files"]["managed/../../escape"]
        self.save_bundle()
        (self.project / development.PACK).write_bytes(b"author file")
        with self.assertRaisesRegex(ValueError, "COLLISION"):
            self.prepare()
        self.assertEqual((self.project / development.PACK).read_bytes(), b"author file")

    def test_upgrade_removes_only_obsolete_owned_managed_files(self):
        (self.bundle / "managed/old.dll").write_bytes(b"old library")
        self.bundle_manifest["files"]["managed/old.dll"] = development.digest(self.bundle / "managed/old.dll")
        self.save_bundle()
        self.prepare()
        del self.bundle_manifest["files"]["managed/old.dll"]
        self.save_bundle()
        self.prepare()
        self.assertFalse((self.project / ".godot/mono/temp/bin/Debug/old.dll").exists())
        self.assertEqual((self.project / "my_scene.tscn").read_text(), "Author scene")

    def test_interrupted_preparation_can_be_retried(self):
        copy = development.atomic_copy
        def interrupted(source, target):
            if target.suffix == ".dll":
                raise OSError("Interrupted managed file copy")
            copy(source, target)
        with patch.object(development, "atomic_copy", interrupted):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.prepare()["status"], "ready")
        self.assertEqual((self.project / ".godot/mono/temp/bin/Debug/Rookframe.dll").read_bytes(), b"compiled application")

    def test_native_collision_preserves_author_files(self):
        target = self.project / "addons/webrtc_native/lib" / self.native.removeprefix("native/")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"author-owned native file")
        with self.assertRaisesRegex(ValueError, "COLLISION"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"author-owned native file")
        self.assertFalse((self.project / development.PACK).exists())
        target.unlink()
        self.prepare()
        self.assertEqual(target.read_bytes(), b"compiled native extension")


if __name__ == "__main__":
    unittest.main()
