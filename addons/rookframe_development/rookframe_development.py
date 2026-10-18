"""Prepare an author's own Godot project to run the compiled development runtime.

The editor owns watching, importing, debugging, live editing and the Game tab.
This module only validates and stages the application team's runtime distribution.
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

BUNDLE_SCHEMA = "rookframe-development-runtime-v1"
REQUIRED_FEATURE = "live-declarations"
CONFIG_SCHEMA = "rookframe-development-v1"
PACK = ".rookframe-development.pck"
NATIVE_ROOT = "addons/webrtc_native/lib/"
RUN_ARGS = (f"--main-pack {PACK} --scene res://rookframe/application/ApplicationRoot.tscn"
            " -- --development=res://.rookframe/development.json")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def host_platform() -> str:
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]


def native_library(target: str, architecture: str) -> str:
    if target == "macos":
        return "libwebrtc_native.macos.template_debug.universal.framework"
    suffix = {"windows": "dll", "linux": "so"}[target]
    return f"libwebrtc_native.{target}.template_debug.{architecture}.{suffix}"


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


def prepare(project: Path, bundle_path: Path, godot_version: str, architecture: str) -> dict:
    project = project.resolve()
    config_path = project / ".rookframe/development.json"
    manifest = json.loads((project / "rookframe.json").read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != CONFIG_SCHEMA or not str(config.get("worldName", "")).strip():
        raise ValueError("DEVELOPMENT.CONFIG: Save a named development World in the Rookframe dock.")
    if config.get("developPackage") != manifest["id"]:
        raise ValueError("DEVELOPMENT.IDENTITY: Save settings again after changing the Package identity.")
    if manifest["kind"] == "optional" and not config.get("systemManifest"):
        raise ValueError("DEVELOPMENT.SYSTEM: An optional Package needs a published System Manifest URL.")
    if manifest["kind"] == "system-extension" and config.get("systemManifest"):
        raise ValueError("DEVELOPMENT.SYSTEM: The editable System already supplies this World's System.")
    if list(project.glob("*.csproj")) or list(project.glob("*.sln")):
        raise ValueError("DEVELOPMENT.PROJECT: Use a GDScript Package project without a host C# build project.")
    bundle_path = bundle_path.resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if bundle.get("schema") != BUNDLE_SCHEMA or bundle.get("platform") != host_platform():
        raise ValueError("DEVELOPMENT.RUNTIME: Choose the Rookframe development runtime for this desktop OS.")
    if REQUIRED_FEATURE not in bundle.get("features", []):
        raise ValueError("DEVELOPMENT.RUNTIME: Update Rookframe development support; this SDK needs a runtime with live Package declarations.")
    if bundle.get("engine") != godot_version or ".mono." not in godot_version:
        raise ValueError(f"DEVELOPMENT.ENGINE: Open this project in stock Godot .NET {bundle.get('engine')}; running {godot_version}.")
    if bundle.get("architecture") != architecture:
        raise ValueError(f"DEVELOPMENT.ARCHITECTURE: Choose the {architecture} runtime for this Godot editor.")
    files = bundle.get("files", {})
    native = "native/" + native_library(bundle["platform"], architecture)
    native_binary = native + "/" + Path(native).stem + ".dylib" if bundle["platform"] == "macos" else native
    if "Rookframe.pck" not in files or "managed/Rookframe.dll" not in files or native_binary not in files:
        raise ValueError("DEVELOPMENT.RUNTIME: Incomplete runtime bundle; reinstall development support.")
    ownership = project / ".rookframe/development-runtime.json"
    previous = json.loads(ownership.read_text(encoding="utf-8")) if ownership.exists() else {}
    checked = []
    for name, expected in files.items():
        is_native = name == native or bundle["platform"] == "macos" and name.startswith(native + "/")
        if name != "Rookframe.pck" and not name.startswith("managed/") and not is_native:
            raise ValueError(f"DEVELOPMENT.RUNTIME: Unsupported member {name}.")
        source = member(bundle_path.parent, name)
        if not source.is_file() or digest(source) != expected:
            raise ValueError(f"DEVELOPMENT.RUNTIME: Damaged or incomplete runtime file: {name}.")
        target = (PACK if name == "Rookframe.pck" else NATIVE_ROOT + name[len("native/"):] if is_native
                  else ".godot/mono/temp/bin/Debug/" + name[len("managed/"):])
        destination = member(project, target)
        if is_native and destination.exists() and target not in previous.get("files", []):
            raise ValueError(f"DEVELOPMENT.COLLISION: Move the author-owned native file before preparing: {target}")
        checked.append((source, destination, expected))
    # All inputs and paths are validated before any write. Only owned, ignored
    # runtime locations are touched; author resources and installed Packages stay put.
    if (project / PACK).exists() and not previous:
        raise ValueError(f"DEVELOPMENT.COLLISION: Move the existing {PACK} before preparing this project.")
    targets = {target.relative_to(project).as_posix() for _, target, _ in checked}
    obsolete = [member(project, name) for name in previous.get("files", [])
                if name not in targets and (name == PACK or name.startswith((".godot/mono/temp/bin/Debug/", NATIVE_ROOT)))]
    # Record ownership before staging. An interrupted copy can then be repaired
    # instead of mistaking our partially prepared PCK for an author's file.
    ownership.write_text(json.dumps({"bundle": str(bundle_path), "files": sorted(
        targets | {path.relative_to(project).as_posix() for path in obsolete})}, indent=2) + "\n", encoding="utf-8")
    for source, target, expected in checked:
        if not target.is_file() or digest(target) != expected:
            atomic_copy(source, target)
    for path in obsolete:
        path.unlink(missing_ok=True)
    ownership.write_text(json.dumps({"bundle": str(bundle_path), "files": sorted(targets)}, indent=2) + "\n", encoding="utf-8")
    return {"status": "ready", "engine": bundle["engine"], "runtimeVersion": bundle["version"],
            "runArguments": RUN_ARGS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--godot", type=Path, required=True)
    parser.add_argument("--architecture", required=True, help="Engine.get_architecture_name() from the author editor")
    args = parser.parse_args(argv)
    try:
        version = subprocess.check_output([str(args.godot), "--version"], text=True).strip()
        print(json.dumps(prepare(args.project, args.bundle, version, args.architecture)))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
