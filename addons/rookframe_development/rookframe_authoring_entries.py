"""Register authored scenes without replacing Publisher-owned code or files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import os
from authoring_paths import project_member


CONTENT_TYPES = ("miniature", "prop", "wall_style", "surface_finish", "theme")
UI_SLOTS = ("left", "right", "ui_root", "actor_creation", "selected_rook")



def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def register(project: Path, scene: str, entry_id: str, name: str, kind: str,
             presentation_id: str | None = None) -> dict:
    from rookframe_authoring import check_edition
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", entry_id) or not name.strip():
        raise ValueError("Choose a name and a stable ID using lowercase letters, numbers, '-' or '_'.")
    if kind not in CONTENT_TYPES + UI_SLOTS:
        raise ValueError("Choose a supported Content type or UI contribution slot.")
    manifest_path = project / "rookframe.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if kind == "actor_creation" and manifest["kind"] != "system-extension":
        raise ValueError("Only a System Extension can register Actor Creation UI.")
    if (kind in UI_SLOTS[2:] and manifest["sdk"]["edition"] == "2027"
            and manifest["sdk"]["minimumRevision"] < 4):
        raise ValueError("This UI slot requires SDK Edition 2027 revision 4 or a later Edition.")
    package_id = manifest["id"]
    root = project / "rookframe/packages" / package_id
    resource = (project / scene.removeprefix("res://")).resolve()
    if not resource.is_relative_to(root.resolve()) or not resource.is_file():
        raise ValueError("Choose a saved scene inside this Package's UUID directory.")
    if kind == "theme":
        if resource.suffix != ".tres":
            raise ValueError("Choose a native Godot Theme saved as a .tres resource.")
    elif resource.suffix not in (".tscn", ".glb", ".gltf"):
        raise ValueError("Choose a Godot scene or imported glTF model.")
    relative = resource.relative_to(root.resolve()).as_posix()
    changes: dict[Path, str] = {}
    if kind in CONTENT_TYPES:
        groups = manifest.setdefault("content", [])
        if any(entry["id"] == entry_id for group in groups for entry in group.get("entries", [])):
            raise ValueError("That Content ID already exists. Edit its scene or choose another ID.")
        group = next((item for item in groups if item.get("kind") == "visual"), None)
        if group is None:
            group = {"kind": "visual", "entries": []}
            groups.append(group)
        group["entries"].append({"id": entry_id, "displayName": name.strip(),
                                "type": kind, "payload": relative})
    else:
        presentations = manifest.setdefault("presentations", [])
        if not presentations:
            presentations.append({"id": "default", "experiences": ["desktop", "tablet", "phone"],
                                  "entryPoint": "ui/base_presentation.gd"})
            from rookframe_authoring_scenes import base_presentation
            changes[root / "ui/base_presentation.gd"] = base_presentation()
        selected = next((p for p in presentations if presentation_id is None or p["id"] == presentation_id), None)
        if selected is None:
            raise ValueError("The selected Presentation no longer exists.")
        state_path = project / ".rookframe/ui-entries.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"presentations": {}}
        key = selected["id"]
        suffix = hashlib.sha256(key.encode()).hexdigest()[:12]
        wrapper = f"ui/editor_{suffix}.gd"
        owned = state["presentations"].get(key)
        if owned is None:
            if (root / wrapper).exists():
                raise ValueError("The generated Presentation path is already occupied.")
            owned = {"base": selected["entryPoint"], "entries": [], "sha256": None}
        else:
            current = root / wrapper
            if (selected["entryPoint"] != wrapper or not current.is_file()
                    or hashlib.sha256(current.read_bytes()).hexdigest() != owned["sha256"]):
                raise ValueError("The generated Presentation was edited. Reconcile it before adding another editor entry.")
        if any(item["id"] == entry_id for item in owned["entries"]):
            raise ValueError("That UI entry ID already exists.")
        owned["entries"].append({"id": entry_id, "slot": kind})
        state["presentations"][key] = owned
        base = f"res://rookframe/packages/{package_id}"
        quote = lambda value: json.dumps(value, ensure_ascii=False)
        if kind in ("left", "right"):
            from rookframe_authoring_scenes import rail_scene
            changes[root / f"ui/entries/{entry_id}.tscn"] = rail_scene(name.strip())
        for path in changes:
            if path.exists():
                raise ValueError(f"An author file already occupies {path.relative_to(project)}.")
        owned["entries"][-1]["scene"] = relative
        if any("scene" not in item for item in owned["entries"]):
            raise ValueError("This Presentation uses legacy editor descriptors. Keep it intact and register in a new Presentation.")
        lines = ["# Generated by Rookframe editor authoring; customize source scenes or the base Presentation.",
                 f"extends {quote(base + '/' + owned['base'])}", ""]
        for index, item in enumerate(owned["entries"]):
            lines.append(f"const ENTRY_{index} = preload({quote(base + '/' + item['scene'])})")
            if item["slot"] in ("left", "right"):
                lines.append(f"const BUTTON_{index} = preload({quote(base + '/ui/entries/' + item['id'] + '.tscn')})")
        lines.extend(["", "func compose_presentation(host: Object) -> Control:",
                      "\tvar control: Control = super.compose_presentation(host)"])
        for index, item in enumerate(owned["entries"]):
            if item["slot"] in ("left", "right"):
                lines.append(f"\thost.RegisterWindowButton({quote(item['slot'])}, BUTTON_{index}, ENTRY_{index})")
            else:
                slot = {"ui_root": "ui-root", "actor_creation": "actor-creation", "selected_rook": "selected-rook"}[item["slot"]]
                lines.append(f"\thost.RegisterContribution({quote(slot)}, ENTRY_{index})")
        lines.append("\treturn control")
        wrapper_text = "\n".join(lines) + "\n"
        changes[root / wrapper] = wrapper_text
        owned["sha256"] = hashlib.sha256(wrapper_text.encode()).hexdigest()
        selected["entryPoint"] = wrapper
        changes[state_path] = encoded(state)
        edition = manifest["sdk"]["edition"]
        revision = manifest["sdk"]["minimumRevision"]
        check_edition(edition, revision)
    # All semantic and collision checks precede writes. Restore previous bytes on
    # an I/O failure, including the Manifest and editor ownership record.
    changes[manifest_path] = encoded(manifest)
    for path in changes:
        project_member(project, path.relative_to(project).as_posix())
    backup = {path: path.read_bytes() if path.exists() else None for path in changes}
    try:
        for path, text in changes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode())
    except OSError:
        for path, previous in backup.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
        raise
    return {"status": "registered", "id": entry_id, "kind": kind, "scene": relative,
            "changedPaths": ["res://" + path.relative_to(project).as_posix()
                             for path in changes if path.is_relative_to(root) or path == manifest_path]}
