from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
import rookframe_authoring as author

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "addons/rookframe_development/rookframe_authoring.py"


class PackageAuthoringTests(unittest.TestCase):
    def test_missing_selected_application_does_not_fall_back_to_another_install(self):
        from rookframe_authoring_checks import configure_application
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            fallback = project / "Rookframe.exe"
            fallback.touch()
            with patch("shutil.which", return_value=str(fallback)):
                with self.assertRaisesRegex(RuntimeError, "Selected Rookframe application does not exist"):
                    configure_application(project, project / "missing.exe")

    def test_checking_accepts_the_app_bundle_saved_by_development(self):
        import rookframe_authoring_checks as checks
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            bundle = project / "Rookframe.app"
            executable = bundle / "Contents/MacOS/Rookframe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            (project / ".rookframe").mkdir()
            (project / ".rookframe/development.json").write_text(json.dumps({"rookframe": str(bundle)}))
            with patch.dict("os.environ", {}, clear=True):
                checks.configure_application(project)
            self.assertEqual(checks._application, executable)

    def test_new_package_references_installed_source_without_generating_sdk_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            author.initialize(project, "Author", "optional", ui=True)
            manifest = json.loads((project / "rookframe.json").read_text())
            root = project / "rookframe/packages" / manifest["id"]
            self.assertFalse((root / "sdk").exists())
            self.assertTrue((root / "logic/implementation.gd").read_text().startswith('extends Node\n'))
            self.assertIn("rookframe/sdk/**", (project / "export_presets.cfg").read_text())
            self.assertEqual(author.check_manifest(project), manifest)

    def test_register_native_theme_without_implementation(self):
        from rookframe_authoring_entries import register
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            author.initialize(project, "Theme pack", "optional", content_only=True)
            manifest = json.loads((project / "rookframe.json").read_text())
            root = project / "rookframe/packages" / manifest["id"]
            root.mkdir(parents=True, exist_ok=True)
            resource = root / "jade.tres"
            resource.write_text('[gd_resource type="Theme" format=3]\n[resource]\n')
            register(project, str(resource), "jade", "Jade", "theme")
            saved = json.loads((project / "rookframe.json").read_text())
            self.assertEqual(saved["content"][0]["entries"][0], {
                "id": "jade", "displayName": "Jade", "type": "theme", "payload": "jade.tres"})
            self.assertNotIn("implementation", saved)
            self.assertNotIn("presentations", saved)

    def test_new_project_installs_and_enables_the_separate_development_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            author.initialize(project, "Author", "optional", content_only=True)
            self.assertFalse((project / "plug.gd").exists())
            self.assertIn('"res://addons/rookframe_development/plugin.cfg"',
                          (project / "project.godot").read_text())
            self.assertNotIn('"res://addons/rookframe_sdk/plugin.cfg"',
                             (project / "project.godot").read_text())
            self.assertIn("addons/rookframe_development/", (project / ".gitignore").read_text())

    def test_editor_registration_preserves_author_files_and_survives_content_to_ui_addition(self):
        from rookframe_authoring_entries import register
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            author.initialize(project, "Author", "optional", content_only=True)
            manifest = json.loads((project / "rookframe.json").read_text())
            self.assertNotIn("implementation", manifest)
            root = project / "rookframe/packages" / manifest["id"]
            root.mkdir(parents=True, exist_ok=True)
            scene = root / "window.tscn"
            scene.write_text('[gd_scene format=3]\n[node name="Window" type="Control"]\n')
            register(project, scene.as_posix(), "window", "Window", "left")
            first = (project / "rookframe.json").read_bytes()
            generated = root / json.loads(first)["presentations"][0]["entryPoint"]
            self.assertIn('host.RegisterWindowButton("left", BUTTON_0, ENTRY_0)', generated.read_text())
            generated.write_text(generated.read_text() + "# Author customization\n")
            preserved = generated.read_bytes()
            with self.assertRaisesRegex(ValueError, "was edited"):
                register(project, scene.as_posix(), "second", "Second", "right")
            self.assertEqual((project / "rookframe.json").read_bytes(), first)
            self.assertEqual(generated.read_bytes(), preserved)
            self.assertFalse((root / "ui/entries/second.tres").exists())
            with self.assertRaisesRegex(ValueError, "inside this Package"):
                register(project, str(project / "project.godot"), "escape", "Escape", "prop")






    def test_shared_export_retains_all_presentations_and_texture_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            author.initialize(project, "Calendar", "optional", True)
            self.assertEqual(author.configured_profiles(project), ["package"])
            config = project / "export_presets.cfg"
            original = config.read_text()
            for old, new, diagnostic in [
                ('dedicated_server=false', 'dedicated_server=true', 'EXPORT.CONTENTS'),
                ('s3tc,bptc,etc2,astc', 's3tc,bptc', 'EXPORT.TEXTURES'),
            ]:
                config.write_text(original.replace(old, new))
                with self.assertRaisesRegex(RuntimeError, diagnostic):
                    author.configured_profiles(project)













    def test_init_rejects_a_preset_collision_before_creating_any_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            original = '[preset.0]\nname="package"\nplatform="macOS"\nscript_export_mode=1\n'
            (project / "export_presets.cfg").write_text(original)
            result = subprocess.run([sys.executable, str(CLI), "init", "--project", directory],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("INIT.CONFLICT", result.stderr)
            self.assertEqual(list(project.iterdir()), [project / "export_presets.cfg"])
            self.assertEqual((project / "export_presets.cfg").read_text(), original)

    def test_init_rejects_presets_directory_before_writing_scaffolding(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "export_presets.cfg").mkdir()
            with self.assertRaisesRegex(RuntimeError, "INIT.CONFLICT"):
                author.initialize(project, "Calendar", "optional")
            self.assertEqual(list(project.iterdir()), [project / "export_presets.cfg"])

    def test_init_preserves_publisher_inputs_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            settings = b'config_version=5\n[application]\nconfig/name="My project"\n'
            (project / "project.godot").write_bytes(settings)
            command = [sys.executable, str(CLI), "init", "--project", directory,
                       "--name", "Calendar"]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            manifest = json.loads((project / "rookframe.json").read_text())
            self.assertEqual(manifest["kind"], "optional")
            self.assertEqual(manifest["name"], "Calendar")
            self.assertEqual((project / "project.godot").read_bytes(), settings)
            before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(before, {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()})

    def test_failed_export_publishes_no_partial_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            output = project / "output.rookpackage"
            manifest = {"id": "aae579da-5392-4507-8eaa-0d918af58076"}
            with patch.object(author, "source_identity", return_value="source"), \
                 patch.object(author.subprocess, "check_output", return_value="4.7.2"), \
                 patch.object(author, "prepare_profile", return_value=(project, "res://build/")), \
                 patch.object(author, "export_prepared_profile", side_effect=RuntimeError("resource export failed")):
                with self.assertRaisesRegex(RuntimeError, "resource export"):
                    author.build_project(project, project, project / "work", manifest,
                                         ["package"], output, headless=True)
            self.assertFalse(output.exists())
            self.assertEqual(list(project.iterdir()), [])

    def test_source_check_keeps_publisher_tree_unchanged_for_shared_export(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "author"
            work = Path(directory) / "work"
            work.mkdir()
            author.initialize(project, "Calendar", "optional", True)
            before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            checked_profiles = []
            def verify(mode, path, *args):
                if mode == "source":
                    checked_profiles.append(args[0])
                return {"status": mode + "_checked", "minimumSdkRevision": 1}
            def import_project(godot, snapshot, *args):
                (snapshot / "project.godot").write_text("tool modified the disposable copy")
            with patch.object(author, "normalize_binary_resources"), \
                 patch.object(author, "run_godot", side_effect=import_project), \
                 patch.object(author, "verify", side_effect=verify):
                author.check_project(project, Path("godot"), work)
            self.assertEqual(checked_profiles, ["package"])
            self.assertEqual(before, {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
