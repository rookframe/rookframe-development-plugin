@tool
extends VBoxContainer

const Requirements = preload("res://addons/rookframe_development/content_requirements.gd")
var model: RefCounted
var path := ""
var content_id := ""
var scene: Node
var _adding := false
var _refreshing := false
var _expanded := false


func configure(source: RefCounted, payload: String, root: Node = null, id := "") -> void:
	model = source
	path = payload
	scene = root
	content_id = id
	_expanded = not id.is_empty()
	if content_id.is_empty():
		content_id = str(model.entry_at(path).get("id", ""))


func _ready() -> void:
	add_to_group("rookframe_content_editors")
	%Heading.toggled.connect(func(value: bool): _expanded = value; refresh())
	for type: String in Requirements.NAMES:
		%Type.add_item(type)
	%MakeAvailable.pressed.connect(func():
		_adding = true
		refresh())
	%Type.item_selected.connect(func(_index: int): _draft(); refresh())
	%DisplayName.text_submitted.connect(func(_text: String): _commit_name())
	%DisplayName.focus_exited.connect(_commit_name)
	%DisplayName.text_changed.connect(func(_text: String): _draft())
	%Add.pressed.connect(_add)
	%Check.pressed.connect(_check)
	%Open.pressed.connect(_open)
	%Identity.pressed.connect(func(): %IdentityValue.visible = not %IdentityValue.visible)
	%CopyId.pressed.connect(func(): DisplayServer.clipboard_set(content_id))
	%Remove.pressed.connect(func(): %ConfirmRemove.popup_centered())
	%ConfirmRemove.confirmed.connect(func(): model.remove_entry(content_id); content_id = ""; refresh())
	%Locate.pressed.connect(func(): %ResourcePicker.popup_centered_ratio(0.65))
	%ResourcePicker.file_selected.connect(_locate)
	%Preview.text_submitted.connect(func(_text: String): _commit_preview())
	%Preview.focus_exited.connect(_commit_preview)
	%SwitchableLight.toggled.connect(func(_value: bool): _commit_prop())
	%WallMount.toggled.connect(func(_value: bool): _commit_prop())
	for field: SpinBox in [%MountWidth, %MountHeight, %MountDefault]:
		field.value_changed.connect(func(_value: float): _commit_prop())
	%ApplyAppearance.pressed.connect(_apply_appearance)
	%InspectStructure.pressed.connect(_inspect_structure)
	model.changed.connect(refresh)
	var draft: Dictionary = EditorInterface.get_editor_settings().get_project_metadata("rookframe_content", path, {})
	%DisplayName.text = str(draft.get("name", path.get_file().get_basename().capitalize()))
	%Type.select(clampi(int(draft.get("type", 4 if ResourceLoader.exists(path) and ResourceLoader.load(path) is Theme else 0)), 0, Requirements.TYPES.size() - 1))
	refresh()


func _exit_tree() -> void:
	if model != null and model.changed.is_connected(refresh):
		model.changed.disconnect(refresh)


func refresh() -> void:
	_refreshing = true
	var entry: Dictionary = model.entry_by_id(content_id)
	var exists := not entry.is_empty()
	if exists:
		path = model.root_path() + str(entry.payload)
		if not %DisplayName.has_focus():
			%DisplayName.text = entry.displayName
		%TypeName.text = Requirements.title(str(entry.type))
		if not %Preview.has_focus():
			%Preview.text = str(entry.get("preview", ""))
	var selected_type: String = str(entry.type) if exists else Requirements.TYPES[%Type.selected]
	var type_index := Requirements.TYPES.find(selected_type)
	%Help.text = Requirements.HELP[type_index] if type_index >= 0 else Requirements.HELP[-1]
	%Payload.text = path.get_file()
	%Payload.tooltip_text = path
	%MakeAvailable.visible = not exists and not _adding
	%Heading.visible = exists
	%Heading.text = "Rookframe Content · " + str(entry.get("displayName", ""))
	%Heading.tooltip_text = str(entry.get("displayName", "")) + " · " + Requirements.title(selected_type)
	%Heading.set_pressed_no_signal(_expanded)
	%Fields.visible = _adding or (exists and _expanded)
	%Type.visible = not exists
	%TypeName.visible = exists
	%System.visible = not exists and selected_type == "system"
	%Add.visible = not exists and _adding
	%Existing.visible = exists
	%Appearance.visible = selected_type in ["wall_style", "surface_finish"]
	%ApplyAppearance.disabled = not is_instance_valid(scene) or scene != EditorInterface.get_edited_scene_root()
	if is_instance_valid(scene) and not %SampleWidth.get_line_edit().has_focus():
		%SampleWidth.value = float(scene.get_meta("authored_sample_span", 1.0))
	%AppearanceContext.text = "These settings are saved on this scene root. One authored unit equals one world unit." if not %ApplyAppearance.disabled else "Open this scene and select its root to edit its appearance settings."
	%InspectStructure.visible = selected_type in ["prop", "wall_style", "surface_finish"]
	%InspectStructure.text = "Inspect collision" if selected_type == "prop" else "Inspect Surface mesh"
	%InspectStructure.disabled = not is_instance_valid(scene)
	%PropFields.visible = exists and selected_type == "prop"
	%SwitchableLight.button_pressed = bool(entry.get("switchableLight", false))
	%WallMount.button_pressed = entry.has("wallMount")
	%MountFields.visible = entry.has("wallMount")
	if entry.has("wallMount"):
		%MountWidth.value = float(entry.wallMount.width)
		%MountHeight.value = float(entry.wallMount.height)
		%MountDefault.value = float(entry.wallMount.defaultHeight)
	%IdentityValue.get_node("Value").text = content_id
	%Status.text = model.conflict if not model.conflict.is_empty() else (
		"Unsaved Package changes" if model.dirty() else "Saved in this Package" if exists else "Supporting resource — not yet Content")
	%Locate.visible = exists and _expanded
	%Check.visible = _adding or (exists and _expanded)
	%Open.visible = _adding or (exists and _expanded)
	%Add.disabled = not model.owns(path) or not FileAccess.file_exists(path) or model.busy
	if %Add.disabled and not exists:
		%Status.text = "Save this scene or Resource inside your Package folder first."
	%Open.disabled = not ResourceLoader.exists(path)
	var blocked: bool = model.busy or not model.conflict.is_empty()
	%DisplayName.editable = not blocked
	%Preview.editable = not blocked
	%Type.disabled = blocked
	%MakeAvailable.disabled = blocked
	%Remove.disabled = blocked
	%Locate.disabled = blocked
	%SwitchableLight.disabled = blocked
	%WallMount.disabled = blocked
	for field: SpinBox in [%MountWidth, %MountHeight, %MountDefault]:
		field.editable = not blocked
	%Add.disabled = %Add.disabled or blocked
	_refreshing = false


func scene_saved(_saved_path: String) -> void:
	if is_instance_valid(scene) and not scene.scene_file_path.is_empty() and scene.scene_file_path != path:
		path = scene.scene_file_path
		content_id = str(model.entry_at(path).get("id", ""))
		_adding = false
		%DisplayName.text = path.get_file().get_basename().capitalize()
		refresh()


func _apply_appearance() -> void:
	if not is_instance_valid(scene) or scene != EditorInterface.get_edited_scene_root():
		return
	var entry: Dictionary = model.entry_by_id(content_id)
	var type: String = str(entry.type) if not entry.is_empty() else Requirements.TYPES[%Type.selected]
	var fields := {"content_kind": type, "authored_units_per_world_unit": 1.0, "authored_sample_span": %SampleWidth.value}
	if type == "surface_finish":
		fields.merge({"supports_floor": true, "supports_ceiling": true})
	model.undo.create_action("Edit appearance settings", UndoRedo.MERGE_DISABLE, scene)
	for key: String in fields:
		model.undo.add_do_method(scene, "set_meta", key, fields[key])
		if scene.has_meta(key):
			model.undo.add_undo_method(scene, "set_meta", key, scene.get_meta(key))
		else:
			model.undo.add_undo_method(scene, "remove_meta", key)
	model.undo.commit_action()
	EditorInterface.mark_scene_as_unsaved()
	_check()


func _inspect_structure() -> void:
	if not is_instance_valid(scene):
		return
	var entry: Dictionary = model.entry_by_id(content_id)
	var type: String = str(entry.type) if not entry.is_empty() else Requirements.TYPES[%Type.selected]
	var target: Node = scene.get_node_or_null("PropCollision/CollisionShape3D") if type == "prop" else scene.find_child("Surface", true, false)
	if target != null:
		EditorInterface.edit_node(target)
	else:
		%Status.text = "Create PropCollision/CollisionShape3D with a primitive shape in the Scene tree." if type == "prop" else "Create a MeshInstance3D named Surface in the Scene tree, with one mesh surface and a BaseMaterial3D."


func _commit_prop() -> void:
	if _refreshing or content_id.is_empty():
		return
	var mount: Variant = {"width": %MountWidth.value, "height": %MountHeight.value, "defaultHeight": %MountDefault.value} if %WallMount.button_pressed else null
	model.update_entry(content_id, {"switchableLight": %SwitchableLight.button_pressed, "wallMount": mount})


func _draft() -> void:
	if content_id.is_empty():
		EditorInterface.get_editor_settings().set_project_metadata("rookframe_content", path,
			{"name": %DisplayName.text, "type": %Type.selected})


func _commit_name() -> void:
	if content_id.is_empty():
		return
	var name: String = %DisplayName.text.strip_edges()
	if name.is_empty():
		%Status.text = "Enter a display name."
		return
	model.update_entry(content_id, {"displayName": name})


func _commit_preview() -> void:
	if content_id.is_empty():
		return
	var value: String = %Preview.text.strip_edges()
	if not value.is_empty() and (not model.owns(model.root_path() + value) or not ResourceLoader.exists(model.root_path() + value) or not ResourceLoader.load(model.root_path() + value) is Texture2D):
		%Status.text = "Preview must be an existing image path relative to this Package."
		return
	model.update_entry(content_id, {"preview": null if value.is_empty() else value})


func commit_fields() -> void:
	_commit_name()
	_commit_preview()


func _check() -> bool:
	var entry: Dictionary = model.entry_by_id(content_id)
	var type: String = str(entry.type) if not entry.is_empty() else Requirements.TYPES[%Type.selected]
	var issue := Requirements.inspect(path, type, scene if is_instance_valid(scene) else null)
	%Status.text = "Structure checked. Use Check Package for application verification." if issue.is_empty() else issue
	return issue.is_empty()


func _add() -> void:
	if not _check():
		return
	var type: String = Requirements.TYPES[%Type.selected]
	var system: Dictionary = {}
	if type == "system":
		type = %SystemType.text.strip_edges()
		var uuid := RegEx.new()
		uuid.compile("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
		if uuid.search(%TargetSystem.text.strip_edges()) == null or type.is_empty() or %Contract.text.strip_edges().is_empty() or %Versions.text.strip_edges().is_empty():
			%Status.text = "Enter the target System UUID, contract, compatible versions and its documented Content type."
			return
		system = {"targetSystemPackageId": %TargetSystem.text.strip_edges(),
			"targetContractId": %Contract.text.strip_edges(), "compatibleVersions": %Versions.text.strip_edges()}
	if is_instance_valid(scene) and scene == EditorInterface.get_edited_scene_root():
		if EditorInterface.save_scene() != OK:
			%Status.text = "Save the scene before adding it to the Package."
			return
	if model.add_entry(path, %DisplayName.text, type, system):
		content_id = str(model.entry_at(path).get("id", ""))
		_adding = false
		_expanded = true
		refresh()


func _open() -> void:
	if ResourceLoader.load(path) is PackedScene:
		EditorInterface.open_scene_from_path(path)
	else:
		EditorInterface.edit_resource(ResourceLoader.load(path))


func _locate(value: String) -> void:
	if not model.owns(value):
		%Status.text = "Choose a resource inside this Package's folder."
		return
	var existing: Dictionary = model.entry_at(value)
	if not existing.is_empty() and existing.id != content_id:
		%Status.text = "This resource already belongs to another Content declaration."
		return
	var entry: Dictionary = model.entry_by_id(content_id)
	var issue := Requirements.inspect(value, str(entry.get("type", "")))
	if not issue.is_empty():
		%Status.text = issue
		return
	model.update_entry(content_id, {"payload": value.trim_prefix(model.root_path())})
