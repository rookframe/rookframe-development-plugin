from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "package_build", ROOT / "addons/rookframe_development/rookframe_package_build.py"
)
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)
GODOT = Path(os.environ.get("ROOKFRAME_GODOT", "/Applications/Godot_mono.app/Contents/MacOS/Godot"))
PACKAGE_ID = "22222222-2222-4222-8222-222222222222"
BUILD_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


class PackageBuildPreparationTests(unittest.TestCase):
    def test_prepared_scripts_preserve_canonical_lf_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            root = source / "rookframe/packages" / PACKAGE_ID
            root.mkdir(parents=True)
            (source / "project.godot").write_text("config_version=5\n")
            (source / "rookframe_ui_kit_provenance.json").write_text('{"installation": "local"}')
            script = b"extends RefCounted\n\nfunc value() -> int:\n\treturn 1\n"
            (root / "example.gd").write_bytes(script)
            parameters = "\ncompress/mode=0\nsvg/scale=2.0\n"
            (root / "icon.svg.import").write_text(
                '[remap]\nimporter="texture"\ntype="CompressedTexture2D"\n'
                '[deps]\nsource_file="res://rookframe/packages/' + PACKAGE_ID
                + '/icon.svg"\n[params]' + parameters
            )
            before = BUILD.source_identity(source)
            with patch.object(BUILD, "allocate_uids", return_value={}):
                project, runtime = BUILD.prepare_profile(
                    GODOT, source, Path(temporary) / "prepared", PACKAGE_ID, BUILD_ID, "package"
                )
            self.assertEqual(script, (project / runtime[6:] / "example.gd").read_bytes())
            self.assertFalse((project / "rookframe_ui_kit_provenance.json").exists())
            self.assertTrue((source / "rookframe_ui_kit_provenance.json").exists())
            prepared_import = (project / runtime[6:] / "icon.svg.import").read_text()
            self.assertEqual(parameters, prepared_import.split("[params]", 1)[1])
            self.assertNotIn("[deps]", prepared_import)
            self.assertNotIn("source_file", prepared_import)
            self.assertEqual(before, BUILD.source_identity(source))

    def test_build_diagnoses_global_cache_dependencies_without_rejecting_path_attached_named_scripts(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            root = source / "rookframe/packages" / PACKAGE_ID
            root.mkdir(parents=True)
            (source / "project.godot").write_text("config_version=5\n")
            (root / "helper.gd").write_text(
                "extends RefCounted\nclass_name ExampleHelper\n"
            )
            with patch.object(BUILD, "allocate_uids", return_value={}):
                BUILD.prepare_profile(
                    GODOT,
                    source,
                    Path(temporary) / "supported",
                    PACKAGE_ID,
                    BUILD_ID,
                    "desktop",
                )
            (root / "entry.gd").write_text("extends Node\nvar helper: ExampleHelper\n")
            with patch.object(BUILD, "allocate_uids", return_value={}):
                with self.assertRaisesRegex(
                    RuntimeError, "global class.*ExampleHelper"
                ):
                    BUILD.prepare_profile(
                        GODOT,
                        source,
                        Path(temporary) / "unsupported",
                        PACKAGE_ID,
                        BUILD_ID,
                        "desktop",
                    )

    @unittest.skipUnless(
        GODOT.is_file(),
        "Native author-resource preparation requires the pinned Godot executable",
    )
    def test_native_binary_author_resources_keep_references_coherent_when_prepared(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "author"
            source.mkdir()
            (source / "project.godot").write_text("config_version=5\n")
            root = source / "rookframe/packages" / PACKAGE_ID
            root.mkdir(parents=True)
            author_root = f"res://rookframe/packages/{PACKAGE_ID}/"
            (source / "seed.gd").write_text(
                """extends SceneTree
func _init():
    var resource = Resource.new()
    resource.set_meta("marker", "Binary author data")
    ResourceSaver.save(resource, "%sdata.res")
    ResourceSaver.set_uid("%sdata.res", ResourceUID.text_to_id("uid://babcdefghijk"))
    quit()
"""
                % (author_root, author_root)
            )
            BUILD.run_godot(GODOT, source, "--script", "res://seed.gd")
            (root / "piece.tscn").write_text(
                "[gd_scene load_steps=2 format=3]\n"
                f'[ext_resource type="Resource" uid="uid://babcdefghijk" path="{author_root}data.res" id="1"]\n'
                '[node name="Piece" type="Node"]\nmetadata/data = ExtResource("1")\n'
            )
            before = BUILD.source_identity(source)
            project, runtime = BUILD.prepare_profile(
                GODOT,
                source,
                Path(temporary) / "stage",
                PACKAGE_ID,
                BUILD_ID,
                "desktop",
            )
            prepared = project / runtime[6:]
            self.assertNotIn(
                "uid://babcdefghijk", (prepared / "piece.tscn").read_text()
            )
            self.assertIn(
                runtime + "data.tres", (prepared / "data.res.remap").read_text()
            )
            self.assertIn("Binary author data", (prepared / "data.tres").read_text())
            self.assertEqual(before, BUILD.source_identity(source))

    def test_reference_preparation_preserves_nonreference_text_and_escaped_literals(
        self,
    ):
        root = "res://rookframe/packages/id/"
        runtime = "res://rookframe/package-artifacts/build/desktop/"
        text = (
            '# "'
            + root
            + 'ignored.gd"\nvar message = "example '
            + root
            + 'icon.png"\nvar path = "'
            + root
            + 'icon.png"\n'
        )
        actual = BUILD.relocate_strings(text, root, runtime, {})
        self.assertIn('# "' + root + 'ignored.gd"', actual)
        self.assertIn('"example ' + root + 'icon.png"', actual)
        self.assertIn('var path = "' + runtime + 'icon.png"', actual)


if __name__ == "__main__":
    unittest.main()
