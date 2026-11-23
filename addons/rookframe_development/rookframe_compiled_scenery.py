"""Prepare reusable scenery assets through stock Godot in the author project."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata

from authoring_paths import is_link, project_member
from rookframe_package_build import run_godot
from rookframe_package_data import portable_path, wire_bytes


FORMAT = "rookframe-compiled-scenery-asset-v1"
INDEX_FORMAT = "rookframe-compiled-scenery-index-v1"
DIRECTORY = "_rookframe_scenery"
INDEX = f"{DIRECTORY}/index.rfscenery"
ASSETS = f"{DIRECTORY}/assets"
EDITABLE = ".rookframe-development/compiled-scenery"
MAX_RECORD = 32 * 1024 * 1024
COMPILER = Path(__file__).with_name("compiled_scenery_compiler.gd")
SCENERY_TYPES = {"grass", "trees", "prop", "wall_style"}


def canonical_path(path: str) -> str:
    key = portable_path(path)
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in path):
        raise ValueError(f"Invalid Unicode in Package path {path!r}")
    return key


def inputs(root: Path, profile: str) -> dict[str, tuple[int, int, int]]:
    """Inventory exact names and capture a source generation around preparation."""
    names = {}
    files = {}
    try:
        if is_link(root) or not root.is_dir():
            raise ValueError(f"Invalid Package root {root}")
        for directory, children, members in os.walk(root):
            for name in sorted(children + members):
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                alias = canonical_path(relative)
                if alias in names or is_link(path):
                    raise ValueError(f"{relative} aliases {names.get(alias, 'a linked path')}")
                if (alias.split("/")[0] == DIRECTORY or path.suffix.casefold() == ".rfscenery"):
                    raise ValueError(f"{relative} uses the reserved scenery namespace or extension")
                names[alias] = relative
                if path.is_file():
                    stat = path.stat()
                    files[relative] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except (OSError, ValueError) as error:
        raise ValueError(f"SCENERY.REFERENCE: {profile}: {error}") from error
    return files


def jobs(manifest: dict, files: dict[str, tuple], profile: str) -> list[dict]:
    result = []
    seen = set()
    for group in manifest.get("content", []):
        for entry in group.get("entries", []):
            target = entry.get("type")
            if target not in SCENERY_TYPES:
                continue
            path = entry.get("payload")
            local_id = entry.get("id")
            if not isinstance(local_id, str) or not local_id or ":" in local_id or any(
                    unicodedata.category(char) in {"Cc", "Cs"} for char in local_id):
                raise ValueError(f"SCENERY.REFERENCE: {profile}: invalid Content ID {local_id!r}")
            if not isinstance(path, str) or not any(path + suffix in files for suffix in ("", ".remap")):
                raise ValueError(f"SCENERY.REFERENCE: {profile}: {local_id}: missing exact payload {path!r}")
            if local_id in seen:
                raise ValueError(f"SCENERY.REFERENCE: {profile}: duplicate Content ID {local_id}")
            seen.add(local_id)
            result.append({"content": f"{manifest['id']}:{local_id}", "path": path, "target": target})
    return sorted(result, key=lambda entry: ordinal(entry["content"]))


def ordinal(value: str) -> bytes:
    # The application's ordinal comparer orders UTF-16 code units.
    return value.encode("utf-16-be")


def fingerprint(manifest: bytes, engine: str, files: dict, settings: bytes) -> str:
    return sha256(wire_bytes([sha256(manifest).hexdigest(), engine,
                              sha256(COMPILER.read_bytes()).hexdigest(),
                              sha256(settings).hexdigest(), files])).hexdigest()


def replace_bytes(path: Path, content: bytes) -> bool:
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".rookframe-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


@dataclass
class Compilation:
    declaration: dict
    members: dict[str, str]
    counts: dict
    fallbacks: list[dict]
    vegetation_sources: list[dict] = field(default_factory=list)


def compile_scenery(godot: Path, engine: str, project: Path, root: Path, output: Path,
                    manifest: dict, build_id: str, profile: str, files: dict | None = None,
                    settings: bytes = b"", *, export: bool = False,
                    application_pack: Path | None = None) -> Compilation:
    files = inputs(root, profile) if files is None else files
    requested = jobs(manifest, files, profile)
    if not requested:
        return Compilation({}, {}, {}, [])
    algorithm = {
        "compiler:godot": sha256(engine.encode()).hexdigest(),
        "compiler:scenery": sha256(COMPILER.read_bytes()).hexdigest(),
        "compiler:import-settings": sha256(settings).hexdigest(),
    }
    output.mkdir(parents=True, exist_ok=True)
    # Keep worker script and request outside the public namespace. The Godot
    # process runs against the actual imported project, without any autoloads
    # being introduced by this tool.
    work = project_member(project, ".rookframe-development")
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scenery-", dir=work) as directory:
        script = Path(directory) / COMPILER.name
        script.write_bytes(COMPILER.read_bytes())
        request = Path(directory) / "request.json"
        report = Path(directory) / "report.json"
        generated = Path(directory) / "output"
        resource_root = "res://" + root.relative_to(project).as_posix() + "/"
        request.write_bytes(wire_bytes({
            "format": FORMAT, "indexFormat": INDEX_FORMAT, "maximum": MAX_RECORD,
            "root": resource_root, "output": generated.as_posix(), "report": report.as_posix(),
            "paths": list(files), "jobs": requested, "algorithm": algorithm,
            "packageId": manifest["id"], "buildId": build_id, "profile": profile,
            "applicationPack": application_pack.as_posix() if application_pack else "",
            "export": resource_root.removeprefix("res://") if export else "",
        }))
        try:
            arguments = ("--script", "res://" + script.relative_to(project).as_posix(), "--", str(request))
            try:
                run_godot(godot, project, *arguments)
            except RuntimeError as error:
                # The stock headless dummy server cannot preprocess shader
                # includes. A renderer can load the same authored materials.
                if "servers/rendering/dummy/storage/material_storage.cpp" not in str(error):
                    raise
                if generated.exists():
                    shutil.rmtree(generated)
                run_godot(godot, project, *arguments, headless=False)
        except RuntimeError as error:
            diagnostic = next((line.removeprefix("ERROR: ") for line in str(error).splitlines()
                               if line.startswith("ERROR: SCENERY.")), None)
            if diagnostic:
                raise RuntimeError(diagnostic) from error
            raise RuntimeError(f"SCENERY.FORMAT: {profile}: {error}") from error
        result = json.loads(report.read_bytes())
        for path, digest in result["members"].items():
            member = project_member(generated, path)
            content = member.read_bytes()
            if sha256(content).hexdigest() != digest:
                raise RuntimeError(f"SCENERY.DIGEST: {profile}: {path} changed during compilation")
            replace_bytes(project_member(output, path), content)
    members = result["members"]
    declaration = {}
    if members:
        declaration = {"format": FORMAT, "indexFormat": INDEX_FORMAT, "compilerVersion": 1,
                       "indexPath": INDEX, "indexSha256": members[INDEX]}
    return Compilation(declaration, members, result["counts"], result["fallbacks"], result["vegetationSources"])


def application_pack(application: Path | None) -> Path | None:
    if application is None:
        return None
    directory = application.parent
    if directory.name == "MacOS" and directory.parent.name == "Contents":
        directory = directory.parent / "Resources"
    packs = list(directory.glob("*.pck"))
    return packs[0] if len(packs) == 1 else None


def verify_export(members: dict[str, bytes], root: str, compiled: Compilation) -> None:
    prefix = root.removeprefix("res://")
    expected = {prefix + path: digest for path, digest in compiled.members.items()}
    actual = {path: sha256(data).hexdigest() for path, data in members.items()
              if (path.casefold().startswith((prefix + DIRECTORY + "/").casefold())
                  or path.casefold().endswith(".rfscenery"))}
    if actual != expected:
        differences = sorted(set(actual) ^ set(expected)
                             | {path for path in actual.keys() & expected.keys()
                                if actual[path] != expected[path]})
        raise RuntimeError(f"SCENERY.DIGEST: {root}: export changed the scenery inventory: {differences}")
    for artwork in sorted({source["artwork"] for source in compiled.vegetation_sources}):
        path = prefix + artwork
        if not any(path + suffix in members for suffix in ("", ".remap", ".import")):
            raise RuntimeError(f"SCENERY.REFERENCE: {root}: export omitted vegetation source {artwork}")
