"""Publisher-owned stock Godot scaffolds with no optional library imports."""
import json


def presentation(package_id: str) -> str:
    root = f"res://rookframe/packages/{package_id}"
    return f'''extends Control

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
