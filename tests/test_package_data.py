from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/rookframe_development"))
import rookframe_authoring as authoring
import rookframe_package_data as data
from rookframe_package_build import BUILD_SCHEMA, pck_members, run_godot


GODOT = Path(os.environ.get("ROOKFRAME_GODOT", "/Applications/Godot_mono.app/Contents/MacOS/Godot"))
ENGINE = "4.7.2.stable.mono.official.ed1daf0bf"
PACKAGE = "22222222-2222-4222-8222-222222222222"


class DataSources(unittest.TestCase):
    def test_selects_namespace_data_and_keeps_the_exact_control_set(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            root = data.package_root(project, PACKAGE)
            controls = {"rookframe.json", "authentication.json", "services.json", "artifacts/build.json"}
            self.assertEqual(controls, data.CONTROL_DOCUMENTS)
            for relative in controls | {"data/a.json", "data/services.json", "tools/config.json",
                                         "licenses/foo.json", "credit.provenance.json",
                                         "ui/attack_assets/icons/manifest.json", ".rookframe/work.json"}:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            (project / "project-config.json").write_text("{}")
            self.assertEqual(["data/a.json", "data/services.json"], list(data.runtime_sources(root)))
            self.assertTrue(all((root / path).read_bytes() == b"{}" for path in controls))

    def test_rejects_reserved_and_ambiguous_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            root = data.package_root(project, PACKAGE)
            reserved = root / "_ROOKFRAME_DATA"
            reserved.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "DATA.COLLISION"):
                data.runtime_sources(root)
            reserved.rmdir()
            binary = root / "data.RFDATA"
            binary.touch()
            with self.assertRaisesRegex(ValueError, "DATA.COLLISION"):
                data.runtime_sources(root)
            binary.unlink()
            # Unicode equivalents can coexist even on case-insensitive Windows.
            (root / "\u00e9.json").touch()
            (root / "e\u0301.json").touch()
            with self.assertRaisesRegex(ValueError, "aliases"):
                data.runtime_sources(root)
            for unsafe in ["../data.json", "/data.json", "data\\file.json", "res://data.json", "x//y.json"]:
                with self.subTest(unsafe=unsafe), self.assertRaisesRegex(ValueError, "DATA.PATH"):
                    data.portable_path(unsafe)
            with self.assertRaises(ValueError):
                data.package_root(project, "../elsewhere")

    def test_reports_lossy_values_and_duplicate_keys_with_source_and_field(self):
        for value in ["9223372036854775808", "-9223372036854775809", "1e-500", "1e500",
                      "0.10000000000000001", "NaN", '{"a":1,"a":2}', '"\\u0000"', '"\\ud800"']:
            with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, r'record[.]json at \$\["value"\]\[0\]'):
                data.canonical_input(('{"value":[' + value + "]}").encode(), "record.json", str)


@unittest.skipUnless(GODOT.is_file(), "Set ROOKFRAME_GODOT for stock native Package data checks")
class NativePackageData(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.root = data.package_root(self.project, PACKAGE)
        self.root.mkdir(parents=True)
        self.reader = self.project / "reader"
        self.reader.mkdir()
        (self.reader / "project.godot").write_text("config_version=5\n")

    def read_natively(self, script):
        (self.reader / "read.gd").write_text(script, encoding="utf-8")
        run_godot(GODOT, self.reader, "--script", "res://read.gd")

    def test_binary_round_trip_keeps_types_order_nulls_and_reference_identity(self):
        (self.root / "a.json").write_text("null")
        record = self.root / "z.json"
        raw = ('{"unicode":"雪🌲", "values":[null,true,false,9223372036854775807,'
               '-9223372036854775808,9007199254740993,0.10000000000000002,5e-324,-0.0,2.0],'
               '"id":"stable-id","ref":"res://author/data.tres","":false,"int":"author key"}')
        record.write_text(raw, encoding="utf-8")
        destination = self.project / "compiled"
        destination.mkdir()
        relocate = lambda value: data.relocate_reference(value, "res://author/", "res://runtime/", {})
        result = data.compile_data(GODOT, ENGINE, data.runtime_sources(self.root), destination, relocate)
        record.write_text(json.dumps(json.loads(raw), sort_keys=True), encoding="utf-8")
        other = self.project / "other"
        other.mkdir()
        second = data.compile_data(GODOT, ENGINE, data.runtime_sources(self.root), other, relocate)
        self.assertEqual(result.entries, second.entries)
        for relative in [data.INDEX_PATH, *result.entries.values()]:
            self.assertEqual((destination / relative).read_bytes(), (other / relative).read_bytes())
        self.read_natively(f'''extends SceneTree
func _initialize():
    var root := "{destination.as_posix()}/"
    var index = read(root + "{data.INDEX_PATH}")
    var empty = read(root + index.entries["a.json"])
    var block = read(root + index.entries["z.json"])
    var v = block.payload
    var values = v.values
    if not (index.format == "{data.INDEX_FORMAT}" and index.entries.keys() == ["a.json", "z.json"]
            and block.format == "{data.DATA_FORMAT}" and empty.has("payload") and empty.payload == null
            and v.keys() == ["", "id", "int", "ref", "unicode", "values"] and not v.has("optional")
            and v.id == "stable-id" and v["int"] == "author key" and v[""] == false
            and v.unicode == "雪🌲" and v.ref == "res://runtime/data.tres"
            and values.slice(0, 3) == [null, true, false]
            and typeof(values[3]) == TYPE_INT and values[3] == 9223372036854775807
            and values[4] == -9223372036854775807 - 1 and values[5] == 9007199254740993
            and typeof(values[6]) == TYPE_FLOAT
            and PackedByteArray([0x9b, 0x99, 0x99, 0x99, 0x99, 0x99, 0xb9, 0x3f]).decode_double(0) == values[6]
            and values[7] == PackedByteArray([1, 0, 0, 0, 0, 0, 0, 0]).decode_double(0)
            and values[7] != 0.0 and is_inf(1.0 / values[8]) and 1.0 / values[8] < 0
            and typeof(values[9]) == TYPE_FLOAT and values[9] == 2.0):
        push_error("Native Variant round trip changed the record")
        quit(1)
        return
    quit()

func read(path: String):
    var file := FileAccess.open(path, FileAccess.READ)
    var value = file.get_var(false)
    if file.get_position() != file.get_length():
        push_error("Expected exactly one native Variant")
    return value
''')

    def test_development_reuses_unchanged_data_and_publishes_deletion_and_version_changes(self):
        a = self.root / "a.json"
        b = self.root / "b.json"
        a.write_text('{"count":1}')
        b.write_text("[true,null]")
        config = self.project / ".rookframe/development.json"
        config.parent.mkdir()
        config.write_bytes(b'{"worldName":"Existing World"}')
        out = self.project / data.DEV_DATA / PACKAGE
        cache = out / "cache.json"
        index = out / data.INDEX_PATH

        def compile_editor_data():
            return data.prepare_development_data(self.project, GODOT, ENGINE, PACKAGE)

        first = compile_editor_data()
        files = json.loads(cache.read_bytes())["files"]
        stable = out / files["b.json"]["path"]
        stamp = stable.stat().st_mtime_ns
        with patch.object(data, "run_godot", wraps=data.run_godot) as godot:
            self.assertEqual(compile_editor_data()["changedPaths"], [])
            a.write_text('{ "count": 1 }')
            self.assertEqual(compile_editor_data()["changedPaths"], [])
            godot.assert_not_called()
            a.write_text('{"count":2}')
            self.assertEqual(len(compile_editor_data()["changedPaths"]), 1)
            godot.assert_called_once()
        self.assertEqual(stable.stat().st_mtime_ns, stamp)
        self.assertFalse((out / files["a.json"]["path"]).exists())
        previous = index.read_bytes()
        a.write_text('{"count":1e500}')
        with self.assertRaisesRegex(ValueError, "at"):
            compile_editor_data()
        self.assertEqual(index.read_bytes(), previous)
        a.unlink()
        dropped = compile_editor_data()
        self.assertEqual(dropped["changedPaths"], [f"res://rookframe/packages/{PACKAGE}/a.json"])
        self.assertEqual(list((out / data.PAYLOAD_DIRECTORY).glob("*.rfdata")), [stable])
        with patch.object(data, "COMPILER_VERSION", data.COMPILER_VERSION + 1):
            bumped = compile_editor_data()
            self.assertNotEqual(bumped["packageData"]["indexSha256"], first["packageData"]["indexSha256"])
            self.assertFalse(stable.exists())
            b.unlink()
            self.assertEqual(compile_editor_data()["sizes"]["files"], 0)
        self.assertEqual(list((out / data.PAYLOAD_DIRECTORY).glob("*.rfdata")), [])
        self.assertEqual(config.read_bytes(), b'{"worldName":"Existing World"}')

    def test_stock_export_excludes_source_and_rejected_archive_remains_unapproved(self):
        (self.project / "project.godot").write_bytes(b'config_version=5\n')
        (self.project / "export_presets.cfg").write_text(authoring.preset(0, "package", PACKAGE), encoding="utf-8")
        manifest = b'{ "sdk": {"minimumRevision": 34} }\n'
        (self.project / "rookframe.json").write_bytes(manifest)
        (self.root / "authentication.json").write_bytes(b'{ "providers": [] }')
        (self.root / "record.json").write_bytes(b'{ "answer": 42 }\n')
        (self.root / "keep.gd").write_bytes(b'extends RefCounted\n')
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            output = work / "release.rookpackage"
            with patch.object(authoring, "verify", side_effect=RuntimeError("checker rejected data version")) as verify:
                with self.assertRaisesRegex(authoring.AuthoringError,
                                            "checker rejected data version") as error:
                    authoring.build_project(self.project, GODOT, work, {"id": PACKAGE, "sdk": {"minimumRevision": 34}},
                                            ["package"], output, headless=True)
            verify.assert_called_once()
            self.assertEqual(verify.call_args.args[0], "artifact")
            self.assertFalse(output.exists())
            self.assertIn(BUILD_SCHEMA, str(error.exception))
            candidate, = work.glob("*.unapproved")
            with zipfile.ZipFile(candidate) as archive:
                self.assertEqual(archive.read("rookframe.json"), manifest)
                build = json.loads(archive.read("artifacts/build.json"))
                self.assertEqual(build["data"], data.encoding())
                pack = archive.read("artifacts/package.pck")
                (self.reader / "test.pck").write_bytes(pack)
                pck = pck_members(pack)
                root = build["profiles"]["package"]["root"]
                self.assertEqual(pck[root[6:] + "authentication.json"], before[self.root / "authentication.json"])
                self.assertEqual(pck[root[6:] + "keep.gd"], before[self.root / "keep.gd"])
                self.assertNotIn(root[6:] + "record.json", pck)
            self.read_natively(f'''extends SceneTree
func _initialize():
    var root := "{root}"
    if not ProjectSettings.load_resource_pack("res://test.pck", false):
        quit(1)
        return
    var index := FileAccess.open(root + "{data.INDEX_PATH}", FileAccess.READ)
    var value = index.get_var(false)
    var payload := FileAccess.open(root + value.entries["record.json"], FileAccess.READ)
    quit(0 if payload.get_var(false).payload.answer == 42 else 1)
''')
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual((self.project / "rookframe.json").read_bytes(), manifest)
