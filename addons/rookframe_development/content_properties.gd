@tool
extends VBoxContainer

const TYPES := ["miniature", "prop", "wall_style", "surface_finish", "theme", "system"]
const TITLES := ["Miniature", "Prop", "Wall style", "Surface finish", "Theme", "System content"]
const HELP := [
	"A Rook's appearance on the tabletop. Author a Node3D scene; its game data belongs to an Actor.",
	"Decorative objects and scenery. Author PropCollision/CollisionShape3D with a primitive shape in your scene.",
	"A finish for Builder walls. Author a Surface mesh: +X along the wall, +Y up, with one BaseMaterial3D surface.",
	"A finish for Builder floors and ceilings. Author a Surface mesh in the XZ plane with one BaseMaterial3D surface.",
	"A native Godot Theme available to Worlds. Edit it in Godot's Theme editor.",
	"Data for a particular System, such as its authored records. Use that System's Resource schema and ask its author for the contract below."
]
var model: RefCounted
var source: Object
var path := ""
var content_id := ""
var _updating := false


func _ready() -> void:
	add_to_group("rookframe_content_properties")
	path = source.scene_file_path if source is Node else source.resource_path
	content_id = str(model.entry_at(path).get("id", ""))
	for title in TITLES:
		%Type.add_item(title)
	%Type.select(-1)
	%Type.text = "Choose type…"
	%Type.item_selected.connect(func(_value: int): _type_changed())
	%Name.text = (str(source.name) if source is Node else path.get_file().get_basename()).capitalize()
	%Name.text_submitted.connect(func(_value: String): commit_fields())
	%Name.focus_exited.connect(commit_fields)
	%Add.pressed.connect(_add)
	%Actions.get_popup().add_item("Copy Content ID", 0)
	%Actions.get_popup().add_separator()
	%Actions.get_popup().add_item("Remove from Package…", 1)
	%Actions.get_popup().id_pressed.connect(func(id: int):
		if id == 0: DisplayServer.clipboard_set(content_id)
		else: %RemoveConfirm.popup_centered())
	%RemoveConfirm.confirmed.connect(func(): model.remove_entry(content_id); content_id = ""; refresh())
	%ChooseThumbnail.pressed.connect(func():
		%ThumbnailPicker.current_dir = model.root_path() + "assets/"
		%ThumbnailPicker.popup_centered_ratio(0.7))
	%ThumbnailPicker.file_selected.connect(_thumbnail_selected)
	%ClearThumbnail.pressed.connect(func(): _preview_changed(null))
	%Switchable.toggled.connect(func(_value: bool): _prop_changed())
	%WallMount.toggled.connect(func(_value: bool): _prop_changed())
	for field in [%Width, %Height, %DefaultHeight]:
		field.value_changed.connect(func(_value: float): _prop_changed())
	%SampleSpan.value_changed.connect(func(_value: float): _appearance_changed())
	model.changed.connect(refresh)
	refresh()


func refresh() -> void:
	_updating = true
	var entry: Dictionary = model.entry_by_id(content_id)
	if entry.is_empty():
		entry = model.entry_at(path)
		content_id = str(entry.get("id", ""))
	else:
		path = model.root_path() + str(entry.payload)
	var exists := not entry.is_empty()
	%Section.text = "Content properties" if exists else "Add as Content"
	%Actions.visible = exists
	%Add.visible = not exists
	%Type.disabled = exists or source is Theme
	if exists:
		if not %Name.has_focus(): %Name.text = entry.displayName
		%Type.select(TYPES.find(entry.type) if entry.type in TYPES else 5)
	elif source is Theme:
		%Type.select(4)
	elif not source is Node and not source is PackedScene:
		%Type.select(5)
	%Type.set_item_disabled(4, not source is Theme)
	for index in range(4):
		%Type.set_item_disabled(index, not (source is Node3D or source is PackedScene))
	%Thumbnail.visible = exists
	if exists:
		var preview_path: String = model.root_path() + str(entry.get("preview", ""))
		var preview: Resource = load(preview_path) if entry.has("preview") and ResourceLoader.exists(preview_path) else null
		%ThumbnailImage.texture = preview as Texture2D
		%ThumbnailImage.visible = preview is Texture2D
		%ThumbnailFile.text = str(entry.preview).get_file() if entry.has("preview") else "No image selected"
		%ThumbnailFile.tooltip_text = preview_path if entry.has("preview") else ""
		%ChooseThumbnail.text = "Change image…" if entry.has("preview") else "Choose image…"
		%ClearThumbnail.visible = entry.has("preview")
	%Add.disabled = not _eligible()
	%Location.visible = not _eligible()
	if path.is_empty():
		%Location.text = ("Save this scene in " if source is Node else "Save this Resource in ") + model.root_path() + "content/ to add it to the Package."
	elif not model.owns(path):
		%Location.text = "Move this source into " + model.root_path() + " using Godot's FileSystem."
	elif path.get_extension() in ["glb", "gltf", "fbx", "blend"]:
		%Location.text = "Create an inherited scene from this model in Godot. Save it in content/, then select the scene root."
	%Switchable.set_pressed_no_signal(entry.get("switchableLight", false))
	%WallMount.set_pressed_no_signal(entry.has("wallMount"))
	var mount: Dictionary = entry.get("wallMount", {})
	%Width.value = mount.get("width", 1.0)
	%Height.value = mount.get("height", 1.0)
	%DefaultHeight.value = mount.get("defaultHeight", 1.0)
	%MountDimensions.visible = entry.has("wallMount")
	if source is Node:
		%SampleSpan.value = source.get_meta("authored_sample_span", 1.0)
	_type_changed()
	_updating = false


func _eligible() -> bool:
	return model.owns(path) and FileAccess.file_exists(path) and path.get_extension() not in ["glb", "gltf", "fbx", "blend"]


func _type_changed() -> void:
	var index: int = %Type.selected
	%Help.text = HELP[index] if index >= 0 else "Choose how this scene is used in Rookframe."
	%System.visible = index == 5 and content_id.is_empty()
	%Prop.visible = index == 1 and not content_id.is_empty()
	%Appearance.visible = index in [2, 3] and not content_id.is_empty() and source is Node


func _add() -> void:
	%Error.text = ""
	if not _eligible() or %Type.selected < 0:
		%Error.text = "Choose a content type after saving the source in your Package."
		return
	var kind: String = TYPES[%Type.selected]
	var system := {}
	if kind == "system":
		var uuid := RegEx.new()
		uuid.compile("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
		var versions := RegEx.new()
		versions.compile("^(?:(?:>=|<=|>|<|=)(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?)(?:\\s+(?:>=|<=|>|<|=)(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?)*$")
		if uuid.search(%SystemId.text.strip_edges()) == null or %Contract.text.strip_edges().is_empty() or %ResourceType.text.strip_edges().is_empty() or versions.search(%Versions.text.strip_edges()) == null:
			%Error.text = "Enter the System UUID, contract, content type and a version expression such as >=1.0.0 <2.0.0."
			return
		system = {"targetSystemPackageId": %SystemId.text.strip_edges(), "targetContractId": %Contract.text.strip_edges(), "compatibleVersions": %Versions.text.strip_edges()}
		kind = %ResourceType.text.strip_edges()
	if model.add_entry(path, %Name.text, kind, system):
		content_id = str(model.entry_at(path).get("id", ""))
		if kind in ["wall_style", "surface_finish"]: _appearance_changed()
		refresh()


func commit_fields() -> void:
	if _updating or content_id.is_empty() or not is_inside_tree():
		return
	if %Name.text.strip_edges().is_empty():
		%Error.text = "Content needs a name."
		return
	model.update_entry(content_id, {"displayName": %Name.text.strip_edges()})


func _preview_changed(resource: Resource) -> void:
	if _updating or content_id.is_empty(): return
	if resource != null and (not model.owns(resource.resource_path) or resource.resource_path.contains("::")):
		%ThumbnailError.text = "Save the image inside this Package, then choose it again."
		refresh()
		return
	%ThumbnailError.text = ""
	model.update_entry(content_id, {"preview": null if resource == null else resource.resource_path.trim_prefix(model.root_path())})


func _thumbnail_selected(image_path: String) -> void:
	var resource := ResourceLoader.load(image_path)
	if not resource is Texture2D:
		%ThumbnailError.text = "Choose an image that Godot imports as a Texture2D."
		return
	_preview_changed(resource)


func _prop_changed() -> void:
	if _updating or content_id.is_empty(): return
	model.update_entry(content_id, {"switchableLight": %Switchable.button_pressed,
		"wallMount": {"width": %Width.value, "height": %Height.value, "defaultHeight": %DefaultHeight.value} if %WallMount.button_pressed else null})


func _appearance_changed() -> void:
	if _updating or not source is Node or content_id.is_empty(): return
	var type: String = model.entry_by_id(content_id).get("type", "")
	if type not in ["wall_style", "surface_finish"]: return
	var values := {"content_kind": type, "authored_units_per_world_unit": 1.0, "authored_sample_span": %SampleSpan.value}
	if type == "surface_finish": values.merge({"supports_floor": true, "supports_ceiling": true})
	model.undo.create_action("Set Rookframe appearance scale", UndoRedo.MERGE_DISABLE, source)
	for key: String in values:
		model.undo.add_do_method(source, "set_meta", key, values[key])
		if source.has_meta(key): model.undo.add_undo_method(source, "set_meta", key, source.get_meta(key))
		else: model.undo.add_undo_method(source, "remove_meta", key)
	model.undo.commit_action()


func source_saved() -> void:
	if not is_instance_valid(source): return
	# Save As produces another source. It must not steal the old Content identity.
	var current: String = source.scene_file_path if source is Node else source.resource_path
	if current != path:
		path = current
		content_id = str(model.entry_at(path).get("id", ""))
	refresh()
