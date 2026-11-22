"""Compile the Package's runtime JSON with stock Godot, outside the World."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tempfile
import unicodedata
import uuid

from authoring_paths import is_link, project_member
from rookframe_package_build import run_godot


DATA_FORMAT = "rookframe-package-data-v1"
INDEX_FORMAT = "rookframe-package-data-index-v1"
COMPILER_VERSION = 1
GENERATED = "_rookframe_data"
INDEX_PATH = f"{GENERATED}/index.rfdata"
PAYLOAD_DIRECTORY = f"{GENERATED}/payloads"
DEV_DATA = ".rookframe-development/package-data"
COMPILER = Path(__file__).with_name("package_data_compiler.gd")

# Exact Package-relative paths. The outer Manifest and build metadata also
# remain text, and are assembled separately from the Package resource tree.
CONTROL_DOCUMENTS = frozenset({"rookframe.json", "authentication.json", "services.json",
                               "artifacts/build.json"})
AUTHOR_DIRECTORIES = frozenset({"authoring", "build", "licenses", "licences", "provenance", "tools"})
AUTHOR_NAMES = frozenset({"credits.json", "license.json", "licence.json", "provenance.json",
                          "rookframe_ui_kit_provenance.json"})
AUTHOR_FILES = frozenset({"ui/attack_assets/icons/manifest.json"})
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"}


class Number(str):
    """Original JSON token, before committing to an engine number type."""


class Object(list):
    """Retain pairs long enough to diagnose duplicate keys with their location."""


@dataclass
class Compilation:
    entries: dict[str, str]
    source_bytes: int
    payload_bytes: int
    index_bytes: int
    index_sha256: str
    changed: list[str]

    def sizes(self) -> dict:
        return {"files": len(self.entries), "sourceBytes": self.source_bytes,
                "payloadBytes": self.payload_bytes, "indexBytes": self.index_bytes}


def encoding() -> dict:
    return {"format": DATA_FORMAT, "indexFormat": INDEX_FORMAT,
            "indexPath": INDEX_PATH, "compilerVersion": COMPILER_VERSION}


def package_root(project: Path, package_id: str) -> Path:
    identity = uuid.UUID(package_id)
    if str(identity) != package_id or identity.version != 4:
        raise ValueError(f"DATA.PATH: Invalid Package identity: {package_id}")
    return project_member(project, f"rookframe/packages/{package_id}")


def portable_path(path: str) -> str:
    parts = path.split("/")
    if any(not part or part in {".", ".."} or part.endswith((".", " "))
           or any(ord(char) < 32 or char in '\\:<>|"?*' for char in part)
           or part.partition(".")[0].rstrip().upper() in WINDOWS_DEVICES for part in parts):
        raise ValueError(f"DATA.PATH: Ambiguous Package-relative path: {path!r}")
    return unicodedata.normalize("NFC", path).casefold()


def runtime_sources(root: Path) -> dict[str, Path]:
    sources = {}
    seen = {}
    if is_link(root):
        raise ValueError(f"DATA.PATH: Package data must use real directories: {root}")
    if not root.is_dir():
        raise ValueError(f"DATA.PATH: Package resource namespace is missing: {root}")
    for directory, children, files in os.walk(root):
        for name in sorted(children + files):
            path = Path(directory) / name
            logical = path.relative_to(root).as_posix()
            if is_link(path):
                raise ValueError(f"DATA.PATH: Package data must not use a link or junction: {logical}")
            if ((Path(directory) == root and name.casefold() == GENERATED)
                    or path.suffix.casefold() == ".rfdata"):
                raise ValueError(f"DATA.COLLISION: Reserved generated Package data path: {logical}")
        children.sort()
        relative_directory = Path(directory).relative_to(root)
        if any(part.startswith(".") or part.casefold() in AUTHOR_DIRECTORIES
               for part in relative_directory.parts):
            continue
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.casefold() != ".json":
                continue
            logical = path.relative_to(root).as_posix()
            normalized = portable_path(logical)
            if normalized in seen:
                raise ValueError(f"DATA.COLLISION: {logical} aliases {seen[normalized]}")
            seen[normalized] = logical
            if normalized in CONTROL_DOCUMENTS:
                if logical != normalized:
                    raise ValueError(f"DATA.PATH: Control document {logical} must be named {normalized}")
                continue
            lower = name.casefold()
            if (normalized in AUTHOR_FILES or lower.startswith((".", "license-", "licence-"))
                or lower in AUTHOR_NAMES
                or lower.endswith(("-provenance.json", ".provenance.json"))):
                continue
            sources[logical] = path
    return dict(sorted(sources.items()))


def canonical_input(raw: bytes, logical: str, relocate) -> bytes:
    def fail(field, reason):
        raise ValueError(f"DATA.VALUE: {logical} at {field}: {reason}")

    def string(value, field):
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            fail(field, "Expected Unicode scalar values without NUL")
        return value

    def prepare(value, field, depth):
        if depth >= 32 and isinstance(value, list):
            fail(field, "JSON nesting exceeds 32 containers")
        if isinstance(value, Number):
            if value.lstrip("-").isdigit() and value != "-0":
                try:
                    return {"int": int(value).to_bytes(8, "little", signed=True).hex()}
                except (ValueError, OverflowError):
                    fail(field, f"Number {value} is outside the signed 64-bit integer range")
            try:
                native = float(value)
                if math.isfinite(native) and Decimal(repr(native)) == Decimal(value):
                    return {"float": struct.pack("<d", native).hex()}
            except (ValueError, InvalidOperation):
                pass
            fail(field, f"Number {value} cannot round-trip through a finite 64-bit float")
        if isinstance(value, Object):
            result = {}
            for key, member in value:
                location = field + "[" + json.dumps(key, ensure_ascii=True) + "]"
                token = ":" + string(key, location)
                if token in result:
                    fail(location, "Duplicate dictionary key")
                result[token] = prepare(member, location, depth + 1)
            return result
        if isinstance(value, list):
            return [prepare(member, f"{field}[{index}]", depth + 1)
                    for index, member in enumerate(value)]
        if isinstance(value, str):
            string(value, field)
            try:
                return relocate(value)
            except ValueError as error:
                fail(field, str(error))
        return value

    try:
        value = json.loads(raw.decode("utf-8"), parse_int=Number, parse_float=Number,
                           parse_constant=Number, object_pairs_hook=Object)
    except (UnicodeError, ValueError, RecursionError) as error:
        fail("$", str(error))
    return wire_bytes(prepare(value, "$", 0))


def wire_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def relocate_reference(value: str, source_root: str, runtime_root: str,
                       uids: dict[str, str]) -> str:
    if value in uids:
        return uids[value]
    if value.startswith(source_root):
        relative = value[len(source_root):]
        if relative:
            portable_path(relative.removesuffix("/"))
        return runtime_root + relative
    return value


def cache_records(path: Path, key: str) -> dict | None:
    try:
        record = json.loads(path.read_bytes())
        files = record["files"]
        if record["compiler"] == key and isinstance(files, dict) and all(
                isinstance(entry, dict) and isinstance(entry.get("canonical"), str)
                and entry.get("path") == payload_path(logical, entry["canonical"], key)
                for logical, entry in files.items()):
            return files
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass
    return None


def payload_path(logical: str, canonical_sha: str, key: str) -> str:
    identifier = sha256(wire_bytes([logical, canonical_sha, key])).hexdigest()
    return f"{PAYLOAD_DIRECTORY}/{identifier}.rfdata"


def compile_data(godot: Path, engine: str, sources: dict[str, Path], destination: Path,
                 relocate=lambda value: value, *, cache: Path | None = None,
                 export: dict | None = None) -> Compilation:
    """Write one complete index last. Cache state is private to authoring."""
    key = sha256(wire_bytes([encoding(), engine,
                            sha256(COMPILER.read_bytes()).hexdigest(),
                            sha256(Path(__file__).read_bytes()).hexdigest()])).hexdigest()
    cached_records = cache_records(cache, key) if cache is not None else None
    previous = cached_records or {}
    entries = {}
    current = {}
    source_bytes = 0
    index_path = project_member(destination, INDEX_PATH)
    with tempfile.TemporaryDirectory(prefix="rookframe-data-", dir=destination) as directory:
        work = Path(directory)
        jobs = []
        for logical, source in sorted(sources.items()):
            portable_path(logical)
            raw = source.read_bytes()
            source_bytes += len(raw)
            source_sha = sha256(raw).hexdigest()
            old = previous.get(logical, {})
            canonical_sha = old.get("canonical")
            if old.get("source") != source_sha:
                canonical = canonical_input(raw, logical, relocate)
                canonical_sha = sha256(canonical).hexdigest()
            relative = payload_path(logical, canonical_sha, key)
            entries[logical] = relative
            current[logical] = {"source": source_sha, "canonical": canonical_sha, "path": relative}
            if old.get("path") != relative or not project_member(destination, relative).is_file():
                # Whitespace and key reordering need no new native serialization.
                if old.get("source") == source_sha:
                    canonical = canonical_input(raw, logical, relocate)
                input_name = f"{len(jobs)}.json"
                (work / input_name).write_bytes(canonical)
                jobs.append({"source": logical, "input": f"res://{input_name}",
                             "output": f"res://{len(jobs)}.rfdata"})
        changed = sorted(logical for logical in previous.keys() | entries.keys()
                         if previous.get(logical, {}).get("path") != entries.get(logical))
        replace_index = bool(changed or cached_records is None or not index_path.is_file())
        if jobs or replace_index or export is not None:
            request = {"format": DATA_FORMAT, "jobs": jobs}
            if replace_index:
                request["index"] = {"format": INDEX_FORMAT, "entries": entries}
            if export is not None:
                request["export"] = export
            (work / "request.json").write_bytes(wire_bytes(request))
            (work / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            shutil.copyfile(COMPILER, work / COMPILER.name)
            run_godot(godot, work, "--script", "res://" + COMPILER.name)
        for job in jobs:
            output = project_member(destination, entries[job["source"]])
            output.parent.mkdir(parents=True, exist_ok=True)
            os.replace(work / job["output"].removeprefix("res://"), output)
        if replace_index:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(work / "index.rfdata", index_path)
        if cache is not None and current != cached_records:
            (work / "cache.json").write_bytes(wire_bytes({"compiler": key, "files": current}))
            os.replace(work / "cache.json", cache)
        payloads = project_member(destination, PAYLOAD_DIRECTORY)
        expected = {PurePosixPath(path).name for path in entries.values()}
        for stale in payloads.glob("*.rfdata"):
            if stale.name not in expected:
                stale.unlink()
        index_bytes = index_path.read_bytes()
    return Compilation(entries, source_bytes,
                       sum((destination / path).stat().st_size for path in entries.values()),
                       len(index_bytes), sha256(index_bytes).hexdigest(), changed)


@contextmanager
def development_lock(path: Path):
    # Save and Prepare, editor reloads, and the headless path may overlap.
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def prepare_development_data(project: Path, godot: Path, engine: str, package_id: str) -> dict:
    root = package_root(project, package_id)
    relative = f"{DEV_DATA}/{package_id}"
    destination = project_member(project, relative)
    destination.mkdir(parents=True, exist_ok=True)
    with development_lock(project_member(destination, "compile.lock")):
        result = compile_data(godot, engine, runtime_sources(root), destination,
                              cache=project_member(destination, "cache.json"))
    return {"packageData": {**encoding(), "packageId": package_id, "root": f"res://{relative}/",
                            "indexSha256": result.index_sha256},
            "sizes": result.sizes(),
            "changedPaths": [f"res://{root.relative_to(project).as_posix()}/{path}"
                             for path in result.changed]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--godot", type=Path, required=True)
    args = parser.parse_args()
    try:
        project = args.project.resolve()
        config = json.loads((project / ".rookframe/development.json").read_bytes())
        package_id = config.get("developPackage")
        if package_id is None:
            print("{}")
            return 0
        manifest = json.loads((project / "rookframe.json").read_bytes())
        if package_id != manifest.get("id"):
            raise ValueError("DEVELOPMENT.IDENTITY: Save settings again after changing the Package identity.")
        engine = subprocess.check_output([str(args.godot), "--version"], text=True, timeout=30).strip()
        print(json.dumps(prepare_development_data(project, args.godot, engine, package_id)))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
