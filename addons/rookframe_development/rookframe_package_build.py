"""Prepare immutable build/profile identities in disposable Godot projects.

This is author tooling, not the nonexecuting admission gate for installed Packages.
Godot import/export may execute the Publisher's editor tools in the temporary copy.
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import uuid


BUILD_METADATA = "artifacts/build.json"
BUILD_SCHEMA = "rookframe-package-build-v1"
AUTHOR_STATE = {".godot", "__pycache__", ".git", "build", "bin", "obj", ".plugged",
                ".rookframe-development", ".rookframe-development.pck", "rookframe_ui_kit_provenance.json"}
DEVELOPMENT_CONFIG = {"development.json", "installed-application.json"}
TEXT_RESOURCES = {
    ".gd",
    ".tscn",
    ".tres",
    ".godot",
    ".cfg",
    ".import",
    ".uid",
    ".remap",
}
UID = re.compile(r"uid://[a-z0-9]+")
# Strings are matched as complete tokens: escapes, triple strings and comments
# cannot turn an arbitrary substring into a Package resource reference.
TOKENS = re.compile(
    r'''\# [^\n]* | """(?:\\.|[^\\])*?""" | '''
    + r"'''(?:\\.|[^\\])*?'''"
    + r""" | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*' """,
    re.X | re.S,
)


def run_godot(godot: Path, project: Path, *arguments: str) -> str:
    result = subprocess.run(
        [str(godot), "--headless", "--path", str(project), *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "ROOKFRAME_AUTHOR_COPY": "1"},
    )
    output = result.stdout + result.stderr
    if result.returncode or "ERROR:" in output or "SCRIPT ERROR:" in output:
        raise RuntimeError(
            f"Godot preparation failed (exit {result.returncode}; {' '.join(arguments)}):\n{output}"
        )
    return output


def source_identity(source: Path) -> str:
    digest = hashlib.sha256()
    development_files = development_native_files(source)
    for path in sorted(source.rglob("*")):
        if (
            not path.is_file()
            or any(
                part
                in AUTHOR_STATE
                for part in path.relative_to(source).parts
            )
            or path.is_relative_to(source / "rookframe/ui")
            or path.is_relative_to(source / "rookframe/sdk")
            or path.is_relative_to(source / "rookframe/development")
            or path.parent == source / ".rookframe" and path.name in DEVELOPMENT_CONFIG
            or path.relative_to(source).as_posix() in development_files
        ):
            continue
        relative = path.relative_to(source).as_posix().encode()
        contents = path.read_bytes()
        digest.update(struct.pack("<Q", len(relative)) + relative)
        digest.update(struct.pack("<Q", len(contents)) + contents)
    return digest.hexdigest()


def copy_author_project(source: Path, destination: Path) -> None:
    development_files = development_native_files(source)
    def ignore(directory, names):
        ignored = AUTHOR_STATE
        if Path(directory) == source / ".rookframe":
            ignored = ignored | DEVELOPMENT_CONFIG
        if Path(directory) == source / "rookframe":
            ignored = ignored | {"development"}
        return (set(names) & ignored) | {name for name in names
            if (Path(directory) / name).relative_to(source).as_posix() in development_files}
    shutil.copytree(source, destination, ignore=ignore)


def development_native_files(source: Path) -> set[str]:
    inventory = source / ".rookframe/installed-application.json"
    if not inventory.is_file():
        return set()
    return {name for name in json.loads(inventory.read_text(encoding="utf-8")).get("files", [])
            if name.startswith(("dependencies/webrtc_native/lib/", "addons/webrtc_native/lib/"))}


def new_build_id() -> str:
    return str(uuid.uuid4())


def relocate_strings(
    text: str, source_root: str, runtime_root: str, uids: dict[str, str]
) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match[0]
        if token.startswith("#"):
            return token
        width = 3 if token.startswith(('"""', "'''")) else 1
        value = token[width:-width]
        if value in uids:
            value = uids[value]
        elif value.startswith(source_root):
            value = runtime_root + value[len(source_root) :]
        return token[:width] + value + token[-width:]

    return TOKENS.sub(replace, text)


def declared_uids(project: Path, root: Path) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".uid", ".tscn", ".tres", ".import"}:
            continue
        contents = path.read_text(encoding="utf-8")
        if path.suffix == ".uid":
            uid = contents.strip()
            resource = path.with_suffix("")
        else:
            header = (
                contents.split("[deps]", 1)[0]
                if path.suffix == ".import"
                else contents.splitlines()[0]
            )
            match = re.search(r'\buid="(uid://[a-z0-9]+)"', header)
            if match is None:
                continue
            uid = match[1]
            resource = path.with_suffix("") if path.suffix == ".import" else path
        if not UID.fullmatch(uid):
            raise RuntimeError(f"{path}: malformed resource UID")
        target = "res://" + resource.relative_to(project).as_posix()
        if uid in mappings and mappings[uid] != target:
            raise RuntimeError(f"{path}: conflicting author UID {uid}: {mappings[uid]}")
        mappings[uid] = target
    return mappings


def allocate_uids(godot: Path, work: Path, originals: dict[str, str]) -> dict[str, str]:
    allocator = work / "uid-allocator"
    allocator.mkdir()
    (allocator / "project.godot").write_text("config_version=5\n")
    (allocator / "input.json").write_text(json.dumps(sorted(originals)))
    (allocator / "allocate.gd").write_text(
        """extends SceneTree
func _init():
    var originals = JSON.parse_string(FileAccess.get_file_as_string("res://input.json"))
    var result = {}
    for original in originals:
        var id = ResourceUID.create_id()
        ResourceUID.add_id(id, "res://reserved/" + str(id))
        result[original] = ResourceUID.id_to_text(id)
    var output = FileAccess.open("res://output.json", FileAccess.WRITE)
    output.store_string(JSON.stringify(result))
    output.close()
    quit()
"""
    )
    run_godot(godot, allocator, "--script", "res://allocate.gd")
    return json.loads((allocator / "output.json").read_text())


def prepare_profile(
    godot: Path,
    source: Path,
    destination: Path,
    package_id: str,
    build_id: str,
    profile: str,
) -> tuple[Path, str]:
    """Leave all Publisher inputs untouched, including import parameters."""
    uuid_value = uuid.UUID(build_id)
    if uuid_value.version != 4 or str(uuid_value) != build_id:
        raise RuntimeError("A new export requires a canonical build UUIDv4")
    source_root = f"res://rookframe/packages/{package_id}/"
    runtime_root = f"res://rookframe/package-artifacts/{build_id}/{profile}/"
    project = destination / "project"
    copy_author_project(source, project)
    old_root = project / source_root.removeprefix("res://")
    # A precise preparation failure is preferable to silently exporting binary
    # references at their old identity. Native binary normalization is handled
    # before relocation by the author-project resource conversion pass.
    normalize_binary_resources(godot, project, old_root)
    originals = declared_uids(project, old_root)
    replacements = allocate_uids(godot, destination, originals)
    for path in sorted(project.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_RESOURCES:
            continue
        if path.is_relative_to(project / "rookframe/ui") or path.is_relative_to(project / "rookframe/sdk"):
            continue
        text = path.read_text(encoding="utf-8")
        text = relocate_strings(text, source_root, runtime_root, replacements)
        if path.suffix == ".uid" and text.strip() in replacements:
            text = replacements[text.strip()] + "\n"
        if path.suffix == ".import" and path.is_relative_to(old_root):
            # Keep the complete author params and importer metadata. Only
            # generated destinations/checksums are thrown away for reimport.
            remap, separator, params = text.partition("[params]")
            if not separator:
                raise RuntimeError(f"{path}: import settings lack [params]")
            remap = remap.split("[deps]", 1)[0]
            remap = re.sub(r"^path(?:\.[^=]+)?=.*\n?", "", remap, flags=re.M)
            text = remap.rstrip() + "\n\n[params]" + params
        if path.name == "export_presets.cfg":
            text = text.replace(source_root[6:], runtime_root[6:])
        path.write_bytes(text.encode("utf-8"))
    new_root = project / runtime_root.removeprefix("res://")
    new_root.parent.mkdir(parents=True)
    old_root.rename(new_root)
    validate_global_class_references(new_root)
    return project, runtime_root


def validate_global_class_references(root: Path) -> None:
    """Diagnose reliance on an author-project global cache that cannot be merged.

    A named script attached or preloaded by path is supported. Bare references
    to its global name need an unavailable registration mechanism in a stock
    non-replacing PCK mount; don't report a successful build for that case.
    """
    sources = {
        path: TOKENS.sub("", path.read_text(encoding="utf-8"))
        for path in root.rglob("*.gd")
    }
    declarations = {
        match[1]
        for text in sources.values()
        for match in re.finditer(r"^\s*class_name\s+(\w+)", text, re.M)
    }
    for path, text in sources.items():
        body = re.sub(r"^\s*class_name\s+\w+[^\n]*", "", text, flags=re.M)
        for name in declarations:
            if re.search(r"\b" + re.escape(name) + r"\b", body):
                raise RuntimeError(
                    f"{path}: unresolved global class {name}; use a path-addressed "
                    "preload type. Stock non-replacing mounts cannot merge the author global-class cache."
                )


def normalize_binary_resources(godot: Path, project: Path, root: Path) -> None:
    """Use Godot's serializer for native binary author inputs, not byte rewriting.

    A .res/.scn gets a text counterpart and a normal Godot remap at its original
    semantic path. The converted dependency graph then participates in the same
    path/UID preparation as ordinary text resources.
    """
    paths = sorted(p for p in root.rglob("*") if p.suffix in {".res", ".scn", ".glb", ".gltf"})
    if not paths:
        return
    run_godot(godot, project, "--editor", "--import")
    mapping = {
        "res://"
        + p.relative_to(project).as_posix(): "res://"
        + p.with_suffix(".tres" if p.suffix == ".res" else ".tscn")
        .relative_to(project)
        .as_posix()
        for p in paths
    }
    for old, new in mapping.items():
        if (project / new[6:]).exists():
            raise RuntimeError(f"{old}: binary normalization collides with {new}")
    (project / "normalize-input.json").write_text(json.dumps(mapping))
    script = project / "normalize-author.gd"
    script.write_text(
        """extends SceneTree
func _init():
    var paths = JSON.parse_string(FileAccess.get_file_as_string("res://normalize-input.json"))
    var resources = {}
    var uids = {}
    for path in paths:
        uids[path] = ResourceLoader.get_resource_uid(path)
        var resource = ResourceLoader.load(path)
        if resource == null:
            push_error("Cannot normalize " + path)
            quit(1)
            return
        if path.get_extension() in ["glb", "gltf"]:
            _prepare_imported_meshes(resource, {})
        resources[path] = resource
    for path in resources:
        resources[path].take_over_path(paths[path])
    for path in resources:
        var flags = ResourceSaver.FLAG_BUNDLE_RESOURCES if path.get_extension() in ["glb", "gltf"] else 0
        if ResourceSaver.save(resources[path], paths[path], flags) != OK:
            push_error("Cannot save normalized " + path)
            quit(1)
            return
        if uids[path] >= 0 and ResourceSaver.set_uid(paths[path], uids[path]) != OK:
            push_error("Cannot preserve binary resource UID for " + path)
            quit(1)
            return
    quit()

func _prepare_imported_meshes(value, visited: Dictionary) -> void:
    if value is Resource:
        var id: int = value.get_instance_id()
        if visited.has(id):
            return
        visited[id] = true
        # The imported shadow mesh is an optional rendering optimization.
        # Use the original mesh for normal Godot shadows in the prepared copy.
        if value is ArrayMesh:
            value.shadow_mesh = null
        if value is PackedScene:
            var state: SceneState = value.get_state()
            for node in state.get_node_count():
                for property in state.get_node_property_count(node):
                    _prepare_imported_meshes(state.get_node_property_value(node, property), visited)
        for property in value.get_property_list():
            if property.usage & PROPERTY_USAGE_STORAGE:
                _prepare_imported_meshes(value.get(property.name), visited)
    elif value is Array:
        for item in value:
            _prepare_imported_meshes(item, visited)
    elif value is Dictionary:
        for item in value.values():
            _prepare_imported_meshes(item, visited)
"""
    )
    run_godot(godot, project, "--script", "res://normalize-author.gd")
    for old, new in mapping.items():
        path = project / old[6:]
        path.unlink()
        Path(str(path) + ".import").unlink(missing_ok=True)
        Path(str(path) + ".remap").write_text('[remap]\npath="' + new + '"\n')
    script.unlink()
    (project / "normalize-input.json").unlink()
    cache = project / ".godot"
    if not cache.resolve().is_relative_to(project.resolve()):
        raise RuntimeError("Disposable import cache escaped the prepared project.")
    shutil.rmtree(cache, ignore_errors=True)


ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

def deterministic_archive(
    manifest: bytes,
    pcks: dict[str, bytes],
    shared_gd: dict[str, bytes],
    build_metadata: dict,
    media: dict[str, bytes] | None = None,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        entries = [
            (f"artifacts/{profile}.pck", pcks[profile])
            for profile in sorted(pcks)
        ]
        entries.extend(
            (f"artifacts/shared-gd/{path}", contents)
            for path, contents in sorted(shared_gd.items())
        )
        entries.append(("rookframe.json", manifest))
        entries.extend(sorted((media or {}).items()))
        entries.append(
            (BUILD_METADATA, json.dumps(build_metadata, sort_keys=True).encode())
        )
        for name, contents in entries:
            entry = zipfile.ZipInfo(name, ARCHIVE_TIMESTAMP)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.flag_bits = 0x800
            archive.writestr(entry, contents, compresslevel=9)
    return output.getvalue()


def collect_shared_gd(
    source: Path, package_root: Path | None = None
) -> dict[str, bytes]:
    package_root = package_root or source
    shared: dict[str, bytes] = {}
    for path in sorted(package_root.rglob("*.gd")):
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        contents = path.read_bytes()
        try:
            decoded = contents.decode("utf-8")
        except UnicodeDecodeError as exception:
            raise RuntimeError(
                f"{path}: Package implementation must be textual GDScript"
            ) from exception
        if not decoded or "\x00" in decoded:
            raise RuntimeError(
                f"{path}: Package implementation must be textual GDScript"
            )
        shared[relative] = contents
    if not shared:
        raise RuntimeError(
            f"{package_root}: Package has no shared textual GDScript tree"
        )
    return shared


def pck_members(pck: bytes) -> dict[str, bytes]:
    if len(pck) < 104 or pck[:4] != b"GDPC":
        raise RuntimeError("Godot export did not produce a standalone PCK")

    version = struct.unpack_from("<I", pck, 4)[0]
    if version != 4:
        raise RuntimeError(f"Unsupported Godot PCK format version: {version}")

    flags = struct.unpack_from("<I", pck, 20)[0]
    if flags & 1:
        raise RuntimeError("Encrypted Godot PCK directories cannot be verified")

    directory_offset = struct.unpack_from("<Q", pck, 32)[0]
    if directory_offset + 4 > len(pck):
        raise RuntimeError("Godot PCK directory offset is outside the artifact")

    file_count = struct.unpack_from("<I", pck, directory_offset)[0]
    cursor = directory_offset + 4
    members: dict[str, bytes] = {}
    for _ in range(file_count):
        if cursor + 4 > len(pck):
            raise RuntimeError("Godot PCK directory is truncated")
        path_length = struct.unpack_from("<I", pck, cursor)[0]
        cursor += 4
        entry_end = cursor + path_length + 36
        if entry_end > len(pck):
            raise RuntimeError("Godot PCK directory entry is truncated")
        path_bytes = pck[cursor : cursor + path_length]
        try:
            path = path_bytes.rstrip(b"\0").decode("utf-8")
            offset, length = struct.unpack_from("<QQ", pck, cursor + path_length)
            if flags & 2:
                offset += struct.unpack_from("<Q", pck, 24)[0]
            if offset + length > directory_offset or path in members:
                raise RuntimeError("Invalid Package PCK member bounds or duplicate path")
            members[path] = pck[offset:offset + length]
        except UnicodeDecodeError as exception:
            raise RuntimeError("Godot PCK directory path is not UTF-8") from exception
        cursor = entry_end
    return members


def pck_paths(pck: bytes) -> tuple[str, ...]:
    return tuple(pck_members(pck))


def shared_profile_sources(pcks: dict[str, bytes], metadata: dict) -> dict[str, bytes]:
    """Compare actual exported textual code at its stable author identity."""
    source_root = "res://rookframe/packages/" + metadata["packageId"] + "/"
    shared = {}
    for profile, pck in pcks.items():
        binding = metadata["profiles"][profile]
        root = binding["root"]
        uids = {uid: source_root + target[len(root):] for uid, target in binding["uids"].items()}
        for path, contents in pck_members(pck).items():
            if not path.endswith(".gd") or not path.startswith(root[6:]):
                continue
            relative = source_root[6:] + path[len(root[6:]):]
            canonical = relocate_strings(contents.decode("utf-8"), root, source_root, uids).encode("utf-8")
            if relative in shared and shared[relative] != canonical:
                raise RuntimeError(f"BUILD.IMPLEMENTATION: {relative} differs in {profile}; profiles must retain shared textual code.")
            shared[relative] = canonical
    return shared


def strip_global_mappings(pck: bytes) -> bytes:
    """Do not let an exported author project publish global host registries."""
    pck_paths(pck)  # Validate the directory bounds first.
    offset = struct.unpack_from("<Q", pck, 32)[0]
    count = struct.unpack_from("<I", pck, offset)[0]
    cursor = offset + 4
    entries = []
    for _ in range(count):
        start = cursor
        length = struct.unpack_from("<I", pck, cursor)[0]
        cursor += 4
        path = pck[cursor : cursor + length].rstrip(b"\0").decode()
        cursor += length + 36
        if path not in {
            "project.binary",
            ".godot/uid_cache.bin",
            ".godot/global_script_class_cache.cfg",
        }:
            entries.append(pck[start:cursor])
    return pck[:offset] + struct.pack("<I", len(entries)) + b"".join(entries)


def godot_export_command(
    godot: Path,
    source: Path,
    profile: str,
    output: Path,
    *,
    headless: bool,
) -> list[str]:
    return [
        str(godot),
        *(["--headless"] if headless else []),
        "--path",
        str(source),
        "--export-pack",
        profile,
        str(output),
    ]



def export_prepared_profile(godot: Path, project: Path, work: Path, profile: str, runtime_root: str, *, headless: bool = True) -> tuple[bytes, dict]:
    # Keep native text resources inspectable in finished Packages. Imported
    # assets still use the selected profile's native Godot importer/output.
    with (project / "project.godot").open("a") as config:
        compressions = ("s3tc_bptc", "etc2_astc") if profile == "package" else (
            "etc2_astc" if profile in {"android", "ios"} else "s3tc_bptc",)
        config.write("\n[rendering]\n")
        for compression in compressions:
            config.write("textures/vram_compression/import_" + compression + "=true\n")
        config.write("\n[editor]\nexport/convert_text_resources_to_binary=false\n")
    run_godot(godot, project, "--editor", "--import")
    validate_global_class_references(project / runtime_root.removeprefix("res://"))
    output = work / "profile.pck"
    result = subprocess.run(
        godot_export_command(godot, project, profile, output, headless=headless),
        env={**os.environ, "ROOKFRAME_AUTHOR_COPY": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode or "ERROR:" in result.stdout + result.stderr:
        raise RuntimeError(
            f"{profile}: export failed\n{result.stdout}{result.stderr}"
        )
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Godot did not produce {output}")
    pck = strip_global_mappings(output.read_bytes())
    paths = pck_paths(pck)
    root = project / runtime_root.removeprefix("res://")
    uids = {
        uid: path
        for uid, path in declared_uids(project, root).items()
        if path[6:] in paths
        or path[6:] + ".remap" in paths
        or path[6:] + ".import" in paths
    }
    return pck, {
        "root": runtime_root,
        "pckSha256": hashlib.sha256(pck).hexdigest(),
        "uids": uids,
    }
