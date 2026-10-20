#!/usr/bin/env python3
"""Standalone Package tools. The installed application owns admission checks."""

from __future__ import annotations

import argparse
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from rookframe_authoring_checks import configured_profiles, configure_application, verify
from rookframe_package_build import (BUILD_SCHEMA,
    copy_author_project, deterministic_archive, export_prepared_profile, new_build_id, normalize_binary_resources,
    prepare_profile, run_godot, shared_profile_sources, source_identity)

from authoring_paths import project_member

SDK_EDITION = "2029"
SDK_EDITIONS = {"2029": 34}
RESOURCE_EXPORT = "package"


class AuthoringError(RuntimeError):
    pass


def check_edition(edition: str, revision: int) -> None:
    if type(revision) is not int or not 1 <= revision <= SDK_EDITIONS.get(edition, 0):
        raise AuthoringError("SDK.REVISION: Choose a supported application API Edition and minimum revision (2029, 1–34). The optional SDK library does not need to be installed.")


def json_text(value: object) -> str:
    return json.dumps(value, indent=2) + "\n"


def preset(index: int, profile: str, package_id: str) -> str:
    desktop_platform = {"darwin": "macOS", "win32": "Windows Desktop"}.get(sys.platform, "Linux")
    platform = desktop_platform
    return f'''[preset.{index}]
name="{profile}"
platform="{platform}"
custom_features="s3tc,bptc,etc2,astc"
runnable=false
dedicated_server=false
export_filter="selected_resources"
include_filter="rookframe/packages/{package_id}/**"
exclude_filter="rookframe.json,rookframe/ui/**,rookframe/sdk/**,addons/**,.rookframe/**,.plugged/**,plug.gd,README.md,presentation/**"
export_path="build/{profile}.pck"
script_export_mode=0
encrypt_pck=false
encrypt_directory=false

[preset.{index}.options]
texture_format/s3tc_bptc=true
texture_format/etc2_astc=true
codesign/codesign=0
application/bundle_identifier="org.rookframe.package"
'''


def read_presets(project: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None, strict=True)
    config.read(project / "export_presets.cfg")
    return config


def initialize(project: Path, name: str, kind: str, ui: bool = False, edition: str = SDK_EDITION,
               content_only: bool = False) -> None:
    if content_only and (kind != "optional" or ui):
        raise AuthoringError("INIT.CONFLICT: A content-only Package must be optional and has no initial UI.")
    presets_path = project / "export_presets.cfg"
    if presets_path.exists() and not presets_path.is_file():
        raise AuthoringError("INIT.CONFLICT: export_presets.cfg must be a regular file.")
    manifest_path = project / "rookframe.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        package_id = manifest["id"]
        if manifest.get("kind") != kind or manifest.get("sdk", {}).get("edition") != edition:
            raise AuthoringError("INIT.CONFLICT: Existing Manifest has a different Kind or SDK Edition.")
    else:
        package_id = str(uuid.uuid4())
        manifest = {"manifestVersion": 2, "id": package_id, "kind": kind,
                    "name": name, "version": "0.1.0", "summary": name,
                    "sdk": {"edition": edition, "minimumRevision": SDK_EDITIONS[edition]},
                    "rookframeCompatibility": {"minimum": "0.1.0", "verified": "0.1.0"},
                    "implementation": {"entryPoint": "logic/implementation.gd"}}
        if content_only:
            del manifest["implementation"]
    identity = uuid.UUID(package_id)
    if identity.version != 4 or str(identity) != package_id:
        raise AuthoringError("INIT.CONFLICT: Package ID must be a canonical UUIDv4.")
    revision = manifest["sdk"]["minimumRevision"]
    check_edition(edition, revision)
    package_root = f"rookframe/packages/{package_id}"
    proposed = {
        "rookframe.json": json_text(manifest),
    }
    if not (project / ".gitignore").exists():
        proposed[".gitignore"] = ".godot/\n.plugged/\naddons/rookframe_sdk/\naddons/rookframe_development/\naddons/gd-plug/\ndependencies/webrtc_native/lib/\naddons/webrtc_native/lib/\nrookframe/ui/\nrookframe/sdk/\nrookframe_ui_kit_provenance.json\nrookframe/development/\n.rookframe-development/\n.rookframe/development.json\n.rookframe/installed-application.json\nbuild/\n__pycache__/\n"
    if ui and not manifest_path.exists():
        from rookframe_authoring_scenes import presentation, window_scene
        manifest["presentations"] = [{"id": "default", "experiences": ["desktop", "tablet", "phone"],
                                      "entryPoint": "ui/presentation.gd"}]
        proposed["rookframe.json"] = json_text(manifest)
        proposed[f"{package_root}/ui/presentation.gd"] = presentation(package_id)
        proposed[f"{package_root}/ui/window.tscn"] = window_scene(name)
    implementation = manifest.get("implementation")
    if implementation and not manifest_path.exists():
        proposed[f"{package_root}/logic/implementation.gd"] = 'extends Node\n'
    if not (project / "project.godot").exists():
        proposed["project.godot"] = 'config_version=5\n[application]\nconfig/name=' + json.dumps(name) + '\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n'
        main = f'run/main_scene="res://{package_root}/ui/window.tscn"\n' if ui else ""
        proposed["project.godot"] = proposed["project.godot"].replace('[rendering]',
            main + '[editor_plugins]\nenabled=PackedStringArray("res://addons/rookframe_development/plugin.cfg")\n[rendering]')
    config = read_presets(project)
    existing_names = {config.get(s, "name", fallback="").strip('"'): s
                      for s in config.sections() if not s.endswith(".options")}
    additions = []
    indices = [int(s.split(".")[1]) for s in config.sections() if s.startswith("preset.") and not s.endswith(".options")]
    index = max(indices, default=-1) + 1
    for profile in [RESOURCE_EXPORT]:
        if profile in existing_names:
            section = config[existing_names[profile]]
            if section.get("script_export_mode") != "0" or package_root not in section.get("include_filter", ""):
                raise AuthoringError(f"INIT.CONFLICT: Export preset {profile} is incompatible; configure textual Package exports explicitly.")
        else:
            additions.append(preset(index, profile, package_id))
            index += 1
    # Preflight every owned/generated collision before adding any scaffolding.
    for relative in (*proposed, "export_presets.cfg", package_root,
                     *(package_root + "/" + folder for folder in ("assets", "content", "models"))):
        try:
            project_member(project, relative)
        except ValueError as error:
            raise AuthoringError(f"INIT.CONFLICT: {relative}: {error}") from error
    if (project / package_root).exists() and not (project / package_root).is_dir():
        raise AuthoringError("INIT.CONFLICT: The Package root must be a directory.")
    for relative, content in proposed.items():
        path = project / relative
        if path.exists() and relative != "rookframe.json" and path.read_text(encoding="utf-8") != content:
            raise AuthoringError(f"INIT.CONFLICT: {relative} differs; reconcile it explicitly. No files were overwritten.")
        if any(parent.exists() and not parent.is_dir() for parent in path.parents):
            raise AuthoringError(f"INIT.CONFLICT: Parent of {relative} is not a directory.")
    (project / package_root).mkdir(parents=True, exist_ok=True)
    (project / package_root / "models").mkdir(exist_ok=True)
    for folder in ("assets", "content"):
        (project / package_root / folder).mkdir(exist_ok=True)
    for relative, content in proposed.items():
        path = project / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
    if additions:
        with (project / "export_presets.cfg").open("a", encoding="utf-8") as output:
            output.write("\n" + "\n".join(additions))


def check_manifest(project: Path) -> dict:
    manifest = json.loads((project / "rookframe.json").read_text(encoding="utf-8"))
    identity = uuid.UUID(manifest["id"])
    revision = manifest["sdk"]["minimumRevision"]
    if str(identity) != manifest["id"] or identity.version != 4:
        raise AuthoringError("MANIFEST.ID: Use a canonical UUIDv4 Package identity.")
    edition = manifest["sdk"]["edition"]
    check_edition(edition, revision)
    return manifest


def check_project(project: Path, godot: Path, work: Path) -> tuple[dict, list[str], dict]:
    # All discovery and independent input checks happen without opening Godot.
    errors = []
    manifest = None
    profiles = []
    manifest_check = {}
    for operation in (
        lambda: check_manifest(project),
        lambda: configured_profiles(project),
        lambda: verify("manifest", project / "rookframe.json"),
    ):
        try:
            value = operation()
            if isinstance(value, dict) and "id" in value:
                manifest = value
            elif isinstance(value, list):
                profiles = value
            elif isinstance(value, dict):
                manifest_check = value
        except (RuntimeError, OSError, ValueError, KeyError, configparser.Error) as error:
            errors.append(str(error))
    if manifest:
        from rookframe_media import project_media
        try:
            project_media(project, manifest)
        except (OSError, ValueError, KeyError) as error:
            errors.append(str(error))
        prefix = f"res://rookframe/packages/{manifest['id']}/"
        for path in manifest_check.get("paths", []):
            if not path.startswith(prefix) or not (project / path[6:]).is_file():
                errors.append(f"MANIFEST.RESOURCE: {path} must exist within the owning Package namespace.")
    if errors:
        raise AuthoringError("\n".join(errors))
    for path in project.rglob("*"):
        if path.is_symlink():
            raise AuthoringError(f"SOURCE.SYMLINK: Materialize {path} before author checking.")
    snapshot = work / "check"
    copy_author_project(project, snapshot)
    root = snapshot / f"rookframe/packages/{manifest['id']}"
    normalize_binary_resources(godot, snapshot, root)
    run_godot(godot, snapshot, "--editor", "--import")
    # No PCK/export is produced by check. Source diagnostics cannot serve as a
    # runtime receipt; finished profile contents pass inspection again at build.
    source_checks = {profile: verify("source", snapshot, profile) for profile in profiles}
    for checked in source_checks.values():
        check_revision(manifest, checked)
    return manifest, profiles, {"status": "source_checked",
        "scope": "Source only; prepared exports and runtime admission are still required.",
        "profiles": source_checks}


def check_revision(manifest: dict, checked: dict) -> None:
    required = checked.get("minimumSdkRevision", 1)
    if manifest["sdk"]["minimumRevision"] < required:
        raise AuthoringError(f"SDK.MINIMUM: These operations require application API revision {required}; update the Manifest explicitly.")


def build_project(project: Path, godot: Path, work: Path, manifest: dict,
                  profiles: list[str], output: Path, *, headless: bool) -> dict:
    if output.exists():
        raise AuthoringError(f"BUILD.OUTPUT_EXISTS: Choose a new output path; {output} already contains an earlier build.")
    package_id = manifest["id"]
    build_id = new_build_id()
    metadata = {"schema": BUILD_SCHEMA, "packageId": package_id, "buildId": build_id,
                "sourceSha256": source_identity(project),
                "engine": subprocess.check_output([str(godot), "--version"], text=True).strip(), "profiles": {}}
    pcks = {}
    for profile in profiles:
        profile_work = work / profile
        prepared, runtime_root = prepare_profile(godot, project, profile_work, package_id, build_id, profile)
        pcks[profile], metadata["profiles"][profile] = export_prepared_profile(
            godot, prepared, profile_work, profile, runtime_root, headless=headless)
    staged = work / "candidate.rookpackage"
    from rookframe_media import project_media
    staged.write_bytes(deterministic_archive(
        (project / "rookframe.json").read_bytes(), pcks,
        shared_profile_sources(pcks, metadata), metadata, project_media(project, manifest)))
    checked = verify("artifact", staged)
    check_revision(manifest, checked)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish only the fully checked set, atomically even across filesystems.
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".rookframe-", delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(staged.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.link(pending, output)  # Atomic publication; never replace an existing build.
        finally:
            pending.unlink(missing_ok=True)
    return {"status": "built", "archive": str(output), "buildId": build_id,
            "profiles": profiles, "verification": checked}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("catalogue", "publish-github"):
        from rookframe_publication import main as publication_main
        return publication_main(sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "register", "check", "build", "media"))
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--name", default="My Package")
    parser.add_argument("--kind", choices=("optional", "system-extension"), default="optional")
    parser.add_argument("--edition", choices=tuple(SDK_EDITIONS), default=SDK_EDITION)
    parser.add_argument("--ui", action="store_true", help="Scaffold an ordinary Control scene and an author-owned Presentation.")
    parser.add_argument("--content-only", action="store_true", help="Create an optional Package without scripts or UI.")
    parser.add_argument("--scene")
    parser.add_argument("--entry-id")
    parser.add_argument("--entry-type")
    parser.add_argument("--presentation")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--role", choices=("cover", "gallery"), default="gallery")
    parser.add_argument("--caption", default="")
    parser.add_argument("--url")
    parser.add_argument("--remove")
    parser.add_argument("--move", type=int, choices=(-1, 0, 1), default=0)
    parser.add_argument("--expected-manifest", help="Refuse media edits if the Manifest changed since this SHA-256.")
    parser.add_argument("--godot", type=Path, default=Path(os.environ.get("ROOKFRAME_GODOT", "/Applications/Godot_mono.app/Contents/MacOS/Godot")))
    parser.add_argument("--rookframe", type=Path, help="Installed Rookframe executable used to check this Package.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--graphical", action="store_true", help="Use the graphical export renderer when required by the host.")
    args = parser.parse_args()
    try:
        project = args.project.resolve()
        if args.command == "init":
            initialize(project, args.name, args.kind, args.ui, args.edition, args.content_only)
            result = {"status": "initialized", "project": str(project)}
        elif args.command == "media":
            from rookframe_media import edit
            result = edit(project, args.file, args.role, args.caption, args.url, args.remove, args.move, args.expected_manifest)
        elif args.command == "register":
            from rookframe_authoring_entries import register
            if not args.scene or not args.entry_id or not args.entry_type:
                raise AuthoringError("Choose a scene, entry ID and Content type or UI slot.")
            result = register(project, args.scene, args.entry_id, args.name,
                              args.entry_type, args.presentation)
        else:
            configure_application(project, args.rookframe)
            with tempfile.TemporaryDirectory(prefix="rookframe-author-") as directory:
                work = Path(directory)
                manifest, profiles, result = check_project(project, args.godot, work)
                if args.command == "build":
                    output = args.output or project / f"build/{manifest['id']}-{manifest['version']}-{uuid.uuid4()}.rookpackage"
                    result = build_project(project, args.godot, work, manifest, profiles,
                                           output.resolve(), headless=not args.graphical)
        print(json_text(result), end="")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, configparser.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1



if __name__ == "__main__":
    raise SystemExit(main())
