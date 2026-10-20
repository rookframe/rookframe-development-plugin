"""Publisher-owned stock Godot scaffolds with no optional library imports."""
import json


def implementation(kind: str = "optional") -> str:
    purpose = ("Define the System's Actors, rules and shared World data here."
               if kind == "system-extension" else
               "Add this Extension's behavior here.")
    return f'''extends Node
## {purpose}
## This is the Package's behavior entry point. Rookframe instantiates it once
## per World; ordinary _ready(), signals and _exit_tree() are available.
## Use the domain API for Actors, Rooks, access and shared World data:
## https://github.com/rookframe/rookframe-sdk/blob/main/API.md
## Local SDK source is an optional helper (Rookframe > Optional libraries).

func _ready() -> void:
\t# The application supplies its domain API before _ready().
\tvar _rookframe: Object = get_meta("rookframe_sdk")
'''


def presentation(package_id: str, scene_path: str | None = None) -> str:
    root = f"res://rookframe/packages/{package_id}"
    if scene_path is not None:
        return f'''extends Control
## UI entry point. Build the scene normally in Godot, then return its root.
## Device coverage is assigned in Package settings > Code & UI.
## Domain API: https://github.com/rookframe/rookframe-sdk/blob/main/API.md
const VIEW = preload("{root}/{scene_path}")

func compose_presentation(_host: Object) -> Control:
\tvar view: Control = VIEW.instantiate()
\tadd_child(view)
\treturn self
'''
    return f'''extends Control
## UI entry point. Edit window.tscn and window_button.tscn normally in Godot.
## This callback registers a button that opens your authored window.
## Domain API: https://github.com/rookframe/rookframe-sdk/blob/main/API.md

const BUTTON = preload("{root}/ui/window_button.tscn")
const WINDOW = preload("{root}/ui/window.tscn")

func compose_presentation(host: Object) -> Control:
\thost.RegisterWindowButton("left", BUTTON, WINDOW)
\treturn self
'''


def base_presentation() -> str:
    return '''extends Control

func compose_presentation(_host: Object) -> Control:
\treturn self
'''


def window_scene(name: str) -> str:
    return f'''[gd_scene format=3]

[node name="PackageWindow" type="VBoxContainer"]
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
grow_horizontal = 2
grow_vertical = 2
size_flags_horizontal = 3
theme_override_constants/separation = 12

[node name="Heading" type="Label" parent="."]
layout_mode = 2
text = {json.dumps(name)}
autowrap_mode = 2

[node name="Description" type="Label" parent="."]
layout_mode = 2
text = "Replace this content with your authored scene."
autowrap_mode = 2
'''


def rail_scene(name: str) -> str:
    return f'''[gd_scene format=3]

[node name="PackageRailEntry" type="Button"]
custom_minimum_size = Vector2(50, 50)
layout_mode = 2
text = {json.dumps(name[:1])}
tooltip_text = {json.dumps(name)}
accessibility_name = {json.dumps("Open " + name)}
'''
