"""Package operations and independent optional-library updates at file boundaries."""
import io
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
import rookframe_authoring as authoring
import rookframe_authoring_entries as entries
import optional_libraries as libraries
from rookframe_package_build import copy_author_project


class StandaloneAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()

    def initialize(self, **options):
        authoring.initialize(self.project, "My Miniatures", "optional", **options)
        manifest = authoring.check_manifest(self.project)
        return self.project / "rookframe/packages" / manifest["id"]

    def test_every_package_kind_initializes_without_optional_libraries(self):
        for kind, ui, content_only in [("optional", False, True), ("optional", True, False),
                                       ("system-extension", True, False)]:
            with self.subTest(kind=kind, content=content_only), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                authoring.initialize(project, "Package", kind, ui=ui, content_only=content_only)
                manifest = authoring.check_manifest(project)
                self.assertEqual(manifest["kind"], kind)
                for suffix in (".gd", ".tscn", ".tres"):
                    for source in project.rglob("*" + suffix):
                        text = source.read_text(encoding="utf-8")
                        self.assertNotIn("res://rookframe/sdk/", text)
                        self.assertNotIn("res://rookframe/ui/", text)
                self.assertFalse((project / "plug.gd").exists())
                self.assertFalse((project / ".rookframe/authoring.lock.json").exists())
                self.assertFalse((project / "addons/rookframe_sdk").exists())

    def test_registering_model_changes_only_the_manifest(self):
        root = self.initialize(content_only=True)
        model = root / "assets/mini.glb"
        model.write_bytes(b"model owned by the author")
        result = entries.register(self.project, "res://" + model.relative_to(self.project).as_posix(),
                                  "my-mini", "My Mini", "miniature")
        declaration = authoring.check_manifest(self.project)["content"][0]["entries"][0]
        self.assertEqual(declaration["payload"], "assets/mini.glb")
        self.assertEqual(result["changedPaths"], ["res://rookframe.json"])
        self.assertEqual(model.read_bytes(), b"model owned by the author")
        before = (self.project / "rookframe.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            entries.register(self.project, "res://" + model.relative_to(self.project).as_posix(),
                             "my-mini", "Duplicate", "miniature")
        self.assertEqual((self.project / "rookframe.json").read_bytes(), before)

    def test_ui_registration_uses_application_operations_without_sdk_resources(self):
        root = self.initialize(ui=True)
        scene = "res://" + (root / "ui/window.tscn").relative_to(self.project).as_posix()
        for slot in ("right", "ui_root"):
            entries.register(self.project, scene, "entry-" + slot, "My Window", slot)
        manifest = authoring.check_manifest(self.project)
        wrapper = root / manifest["presentations"][0]["entryPoint"]
        text = wrapper.read_text(encoding="utf-8")
        self.assertIn("host.RegisterWindowButton", text)
        self.assertIn("host.RegisterContribution", text)
        self.assertNotIn("res://rookframe/sdk/", text)
        wrapper.write_text(text + "# author customization\n", encoding="utf-8")
        before = (self.project / "rookframe.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Presentation was edited"):
            entries.register(self.project, scene, "third-entry", "Third", "left")
        self.assertEqual((self.project / "rookframe.json").read_bytes(), before)

    def test_check_copy_has_no_dependency_on_a_legacy_authoring_lock(self):
        self.initialize(content_only=True)
        lock = self.project / ".rookframe/authoring.lock.json"
        lock.parent.mkdir()
        lock.write_text('{"uiKit":{"commit":"no-local-checkout"}}')
        copy_author_project(self.project, Path(self.temp.name) / "copy")
        self.assertFalse((self.project / ".plugged").exists())


class OptionalLibraryTests(unittest.TestCase):
    def test_latest_uses_published_time_and_includes_candidates(self):
        releases = [{"tag_name": "stable", "published_at": "2026-10-01"},
                    {"tag_name": "candidate", "published_at": "2026-10-19", "prerelease": True},
                    {"tag_name": "draft", "published_at": "2026-10-20", "draft": True}]
        with patch.object(libraries, "fetch", return_value=json.dumps(releases).encode()):
            self.assertEqual(libraries.latest_release("example/repo")["tag_name"], "candidate")

    def test_archive_selects_only_one_library_and_rejects_escaping_paths(self):
        for path, rejected in [("root/rookframe/sdk/value.gd", False),
                               ("root/rookframe/sdk/../../project.godot", True)]:
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr(path, b"extends RefCounted\n")
                zipped.writestr("root/rookframe/ui/theme.tres", b"other library")
            if rejected:
                with self.assertRaisesRegex(ValueError, "Unsafe path"):
                    libraries.library_files("sdk", {"tag_name": "v1"}, "a" * 40, archive.getvalue())
            else:
                result = libraries.library_files("sdk", {"tag_name": "v1"}, "a" * 40, archive.getvalue())
                self.assertEqual(set(result), {"rookframe/sdk/value.gd"})

    def test_inventory_cannot_remove_files_owned_by_the_other_library(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            other = project / "rookframe/ui/theme.tres"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"author theme")
            inventory = project / ".rookframe/optional-libraries.json"
            inventory.parent.mkdir()
            inventory.write_text(json.dumps({"sdk": {"files": {
                "rookframe/ui/theme.tres": hashlib.sha256(b"author theme").hexdigest()}}}))
            with self.assertRaisesRegex(ValueError, "outside this library"):
                libraries.install_files(project, "sdk", {"tag_name": "v2"}, "a" * 40,
                                        {"rookframe/sdk/value.gd": b"extends Resource"})
            self.assertEqual(other.read_bytes(), b"author theme")
            self.assertFalse((project / "rookframe/sdk").exists())

    def test_python310_windows_reparse_points_are_rejected(self):
        import authoring_paths
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            path = project / "rookframe/sdk/value.gd"
            native_lstat = Path.lstat
            def reparse(candidate, *args, **kwargs):
                if candidate == path.parent:
                    return SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)
                return native_lstat(candidate, *args, **kwargs)
            with patch.object(Path, "lstat", reparse):
                with self.assertRaisesRegex(ValueError, "links or junctions"):
                    authoring_paths.project_member(project, "rookframe/sdk/value.gd")

    def test_library_updates_are_independent_and_preserve_author_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            world = project / ".rookframe/development.json"
            world.parent.mkdir()
            world.write_bytes(b"existing World configuration")
            for library, path in (("sdk", "rookframe/sdk/a.gd"), ("ui-kit", "rookframe/ui/a.tres")):
                libraries.install_files(project, library, {"tag_name": "v1"}, "a" * 40, {path: b"v1"})
            libraries.install_files(project, "sdk", {"tag_name": "v2"}, "b" * 40, {"rookframe/sdk/a.gd": b"v2"})
            self.assertEqual((project / "rookframe/ui/a.tres").read_bytes(), b"v1")
            (project / "rookframe/sdk/a.gd").write_bytes(b"author edit")
            with self.assertRaisesRegex(ValueError, "edited file"):
                libraries.install_files(project, "sdk", {"tag_name": "v3"}, "c" * 40, {"rookframe/sdk/a.gd": b"v3"})
            self.assertEqual((project / "rookframe/sdk/a.gd").read_bytes(), b"author edit")
            self.assertEqual(world.read_bytes(), b"existing World configuration")


if __name__ == "__main__":
    unittest.main()
