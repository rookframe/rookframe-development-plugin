@tool
extends EditorInspectorPlugin

const Selection = preload("res://addons/rookframe_development/content_selection.gd")
const ContentPanel = preload("res://addons/rookframe_development/content_editor.tscn")
var model: RefCounted


func _can_handle(object: Object) -> bool:
	if model == null or model.manifest.is_empty():
		return false
	if object is Selection:
		return true
	if object is Node:
		var root := EditorInterface.get_edited_scene_root()
		return root != null and (model.owns(root.scene_file_path) or root.scene_file_path.is_empty()) and (object == root or root.is_ancestor_of(object))
	return object is Resource and model.owns(object.resource_path) and not object.resource_path.contains("::")


func _parse_begin(object: Object) -> void:
	var path := ""
	var root: Node = null
	var id := ""
	if object is Selection:
		id = object.content_id
		var entry: Dictionary = model.entry_by_id(id)
		if entry.is_empty():
			var removed := Label.new()
			removed.text = "Content declaration removed. Undo restores it."
			removed.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			add_custom_control(removed)
			return
		path = model.root_path() + str(entry.payload)
	elif object is Node:
		root = EditorInterface.get_edited_scene_root()
		path = root.scene_file_path
		if object != root:
			var button := Button.new()
			button.text = "Inspect this scene's Rookframe Content"
			button.pressed.connect(func(): EditorInterface.edit_node(root))
			add_custom_control(button)
			return
	else:
		path = object.resource_path
	var panel := ContentPanel.instantiate()
	panel.configure(model, path, root, id)
	add_custom_control(panel)
