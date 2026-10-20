"""File transactions for the editor. Admission remains the application's job."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from authoring_paths import project_member
from rookframe_authoring_scenes import presentation, window_scene


def preserve_numbers(value, original):
    """Godot JSON uses floats. Retain the publisher's integer representation."""
    if isinstance(value, dict) and isinstance(original, dict):
        return {key: preserve_numbers(item, original.get(key)) for key, item in value.items()}
    if isinstance(value, list) and isinstance(original, list):
        return [preserve_numbers(item, original[index] if index < len(original) else None)
                for index, item in enumerate(value)]
    if type(original) in (int, float) and type(value) in (int, float) and value == original:
        return original
    if type(original) is int and type(value) is float and float(original) == value:
        # Unknown publisher metadata can contain integers beyond JSON's double
        # precision. The editor does not expose those fields for modification.
        return original
    return value


def replace_file(path: Path, data: bytes) -> None:
    """Close before replacing, including on Windows; never truncate the original."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as writer:
        temporary = Path(writer.name)
        try:
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
        except BaseException:
            writer.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def execute(project: Path, request: dict) -> dict:
    manifest_path = project_member(project, "rookframe.json")
    before = manifest_path.read_bytes()
    if hashlib.sha256(before).hexdigest() != request["expected"]:
        raise ValueError("rookframe.json changed outside the editor. Reload it or copy your draft before continuing.")
    current = json.loads(before)
    manifest = preserve_numbers(request["manifest"], current)
    for container, key in ((manifest, "manifestVersion"), (manifest.get("sdk", {}), "minimumRevision")):
        value = container.get(key)
        if type(value) is float and value.is_integer():
            container[key] = int(value)
    if manifest.get("id") != current["id"] or manifest.get("kind") != current["kind"]:
        raise ValueError("Package identity and role cannot be changed by an editor save.")
    if not str(manifest.get("name", "")).strip():
        raise ValueError("Enter a Package name.")
    proposed: dict[Path, bytes] = {}
    operation = request.get("operation", "save")
    root = f"rookframe/packages/{current['id']}"
    if operation == "behavior":
        if current.get("implementation"):
            raise ValueError("This Package already has a behavior entry point.")
        relative = "logic/implementation.gd"
        manifest["implementation"] = {"entryPoint": relative}
        proposed[project_member(project, f"{root}/{relative}")] = b"extends Node\n"
    elif operation == "presentation":
        relative = request["entryPoint"]
        if not relative.startswith("ui/") or not relative.endswith(".gd"):
            raise ValueError("Use a new .gd entry point below ui/.")
        scene_path = relative.removesuffix(".gd") + ".tscn"
        proposed[project_member(project, f"{root}/{relative}")] = presentation(current["id"], scene_path).encode()
        proposed[project_member(project, f"{root}/{scene_path}")] = window_scene(manifest["name"]).encode()
    elif operation != "save":
        raise ValueError("Unknown editor operation.")
    for path in proposed:
        if path.exists():
            raise ValueError(f"Keep your existing file; choose another path: {path.relative_to(project)}")
    created: list[Path] = []
    try:
        for path, data in proposed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as writer:
                writer.write(data)
            created.append(path)
        # Recheck immediately before commit, also after potentially slow I/O.
        if manifest_path.read_bytes() != before:
            raise ValueError("rookframe.json changed while saving. Your draft has been retained.")
        replace_file(manifest_path, (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {"status": "saved", "changedPaths": ["res://rookframe.json"] +
            ["res://" + path.relative_to(project).as_posix() for path in created]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.project.resolve(), json.loads(args.request.read_text(encoding="utf-8")))))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
