@tool
extends EditorDock
## Follow Godot's inspected object, including Resource navigation and Inspector pinning.

var model: RefCounted
var _panel: Control
var _source: Object


func _enter_tree() -> void:
	EditorInterface.get_inspector().edited_object_changed.connect(_queue_refresh)
	model.changed.connect(_queue_refresh)
	_queue_refresh()


func _exit_tree() -> void:
	EditorInterface.get_inspector().edited_object_changed.disconnect(_queue_refresh)
	model.changed.disconnect(_queue_refresh)


func _queue_refresh() -> void:
	_refresh.call_deferred()


func _refresh() -> void:
	if not is_inside_tree(): return
	var inspected := EditorInterface.get_inspector().get_edited_object()
	if is_instance_valid(inspected):
		var source_path: String = inspected.scene_file_path if inspected is Node else (inspected.resource_path if inspected is Resource else "")
		%Subject.text = str(inspected.name) if inspected is Node else (source_path.get_file() if not source_path.is_empty() else inspected.get_class())
		%SourcePath.text = source_path.trim_prefix(model.root_path()) if model.owns(source_path) else source_path
		%SourcePath.tooltip_text = source_path
	if is_instance_valid(_source) and _source == inspected and is_instance_valid(_panel) and not model.manifest.is_empty():
		return
	if is_instance_valid(_panel):
		_panel.commit_fields()
		%Content.remove_child(_panel)
		_panel.queue_free()
		_panel = null
	_source = inspected
	%Empty.visible = true
	if model.manifest.is_empty():
		%Subject.text = "No Package"
		%SourcePath.text = ""
		%Empty.text = "Create a Package in the Rookframe tab to add scenes and Resources as Content."
		return
	if not is_instance_valid(inspected):
		%Subject.text = "Select Content"
		%SourcePath.text = ""
		%Empty.text = "Select a scene root or a saved Resource to edit its Rookframe Content properties."
		return
	if inspected is Node and (inspected != EditorInterface.get_edited_scene_root() or not inspected is Node3D):
		%Empty.text = "Content describes a whole scene. Select its Node3D root to edit the scene's Rookframe properties."
		return
	if not inspected is Node and not _content_resource(inspected):
		%Empty.text = "This object has no Rookframe Content properties. Select a scene root, Theme, or saved System Resource."
		return
	%Empty.visible = false
	_panel = preload("res://addons/rookframe_development/content_properties.tscn").instantiate()
	_panel.model = model
	_panel.source = inspected
	%Content.add_child(_panel)


func _content_resource(object: Object) -> bool:
	if not object is Resource or object.resource_path.contains("::"): return false
	return object is PackedScene or object is Theme or (object.get_script() != null and not object is Script)
