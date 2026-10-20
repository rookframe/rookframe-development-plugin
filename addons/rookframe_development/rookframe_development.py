"""Prepare an author's own Godot project to run the installed Rookframe application.

The editor owns watching, importing, debugging, live editing and the Game tab.
This module only locates the ordinary application and copies its published libraries into Godot's cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tempfile

CONFIG_SCHEMA = "rookframe-development-v1"
NATIVE_ROOT = "dependencies/webrtc_native/lib/"
MANAGED_ROOT = ".godot/mono/temp/bin/Debug/"


def run_arguments(pack: Path) -> str:
    return ("--main-pack " + json.dumps(pack.as_posix(), ensure_ascii=False)
            + " --scene res://rookframe/application/ApplicationRoot.tscn"
              " -- --development=res://.rookframe/development.json")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def host_platform() -> str:
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]


def native_library(target: str, architecture: str, configuration: str = "release") -> str:
    if target == "macos":
        return f"libwebrtc_native.macos.template_{configuration}.universal.framework"
    suffix = {"windows": "dll", "linux": "so"}[target]
    return f"libwebrtc_native.{target}.template_{configuration}.{architecture}.{suffix}"


def member(root: Path, name: str) -> Path:
    parts = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or parts.is_absolute() or any(
            part in ("", ".", "..") for part in name.split("/")):
        raise ValueError(f"DEVELOPMENT.PATH: Invalid runtime member: {name}")
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()) or any(
            parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
        raise ValueError(f"DEVELOPMENT.PATH: Runtime member must not use symlinks: {name}")
    return path


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        pending = Path(stream.name)
    try:
        shutil.copy2(source, pending)
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)


def prepare(project: Path, application: Path, godot_version: str, architecture: str) -> dict:
    project = project.resolve()
    config_path = project / ".rookframe/development.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != CONFIG_SCHEMA or not str(config.get("worldName", "")).strip():
        raise ValueError("DEVELOPMENT.CONFIG: Save a named development World in the Rookframe dock.")
    if config.get("developPackage") is not None:
        if config.get("sourceManifest", "res://rookframe.json") != "res://rookframe.json":
            raise ValueError("DEVELOPMENT.CONFIG: Select the project's rookframe.json Package Manifest.")
        manifest = json.loads((project / "rookframe.json").read_text(encoding="utf-8"))
        if config["developPackage"] != manifest["id"]:
            raise ValueError("DEVELOPMENT.IDENTITY: Save settings again after changing the Package identity.")
        if manifest["kind"] == "optional" and not config.get("systemManifest"):
            raise ValueError("DEVELOPMENT.SYSTEM: An optional Package needs a published System Manifest URL.")
        if manifest["kind"] == "system-extension" and config.get("systemManifest"):
            raise ValueError("DEVELOPMENT.SYSTEM: The editable System already supplies this World's System.")
    else:
        if config.get("sourceManifest") is not None:
            raise ValueError("DEVELOPMENT.CONFIG: A source Manifest requires an Editable Package.")
        if not config.get("systemManifest"):
            raise ValueError("DEVELOPMENT.SYSTEM: Choose a published System Manifest URL to run this World.")
    if list(project.glob("*.csproj")) or list(project.glob("*.sln")):
        raise ValueError("DEVELOPMENT.PROJECT: Use a GDScript Package project without a host C# build project.")
    application = application.resolve()
    if application.suffix == ".app":
        application = application / "Contents/MacOS/Rookframe"
    if not application.is_file():
        raise ValueError("DEVELOPMENT.ROOKFRAME: Choose the installed Rookframe application.")
    version = subprocess.check_output([str(application), "--version"], text=True, timeout=30).strip()
    if version != godot_version or ".mono." not in godot_version:
        raise ValueError(f"DEVELOPMENT.ENGINE: Open this project in stock Godot .NET {version}; running {godot_version}.")
    target = host_platform()
    resources = application.parent.parent / "Resources" if target == "macos" else application.parent
    packs = list(resources.glob("*.pck"))
    platform_id = "linuxbsd" if target == "linux" else target
    managed = resources / f"data_Rookframe_{platform_id}_{architecture}"
    native_root = application.parent.parent / "Frameworks" if target == "macos" else resources
    natives = [native_root / native_library(target, architecture, configuration)
               for configuration in ("debug", "release")]
    natives = [native for native in natives if native.exists()]
    if len(packs) != 1 or not (managed / "Rookframe.dll").is_file() or not natives:
        raise ValueError("DEVELOPMENT.ROOKFRAME: The installed application is incomplete or does not match this editor's architecture.")
    ownership = project / ".rookframe/installed-application.json"
    previous = json.loads(ownership.read_text(encoding="utf-8")) if ownership.exists() else {}
    inputs = [(source, MANAGED_ROOT + source.relative_to(managed).as_posix())
              for source in managed.rglob("*") if source.is_file()]
    native_files = [source for native in natives
                    for source in (list(native.rglob("*")) if native.is_dir() else [native])]
    inputs.extend((source, NATIVE_ROOT + source.relative_to(native_root).as_posix())
                  for source in native_files if source.is_file())
    checked = []
    for source, relative in inputs:
        destination = member(project, relative)
        if destination.exists() and relative.startswith(NATIVE_ROOT) and relative not in previous.get("files", []):
            raise ValueError(f"DEVELOPMENT.COLLISION: Move the author-owned native file before preparing: {relative}")
        checked.append((source, destination, digest(source)))
    targets = {relative for _, relative in inputs}
    obsolete = [member(project, name) for name in previous.get("files", [])
                if name not in targets and name.startswith((MANAGED_ROOT, NATIVE_ROOT))]
    # Only derived libraries are copied. The installed application's PCK is used
    # directly; there is no second application export or development bundle.
    ownership.write_text(json.dumps({"application": str(application), "files": sorted(
        targets | {path.relative_to(project).as_posix() for path in obsolete})}, indent=2) + "\n", encoding="utf-8")
    for source, destination, expected in checked:
        if not destination.is_file() or digest(destination) != expected:
            atomic_copy(source, destination)
    for path in obsolete:
        path.unlink(missing_ok=True)
    ownership.write_text(json.dumps({"application": str(application), "files": sorted(targets)}, indent=2) + "\n", encoding="utf-8")
    return {"status": "ready", "engine": version, "application": str(application),
            "runArguments": run_arguments(packs[0])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--rookframe", type=Path, required=True)
    parser.add_argument("--godot", type=Path, required=True)
    parser.add_argument("--architecture", required=True, help="Engine.get_architecture_name() from the author editor")
    args = parser.parse_args(argv)
    try:
        version = subprocess.check_output([str(args.godot), "--version"], text=True).strip()
        print(json.dumps(prepare(args.project, args.rookframe, version, args.architecture)))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
