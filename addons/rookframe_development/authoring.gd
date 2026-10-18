@tool
extends VBoxContainer

signal package_changed(paths: PackedStringArray)
var _install_worker: Thread
var _media_role := "gallery"
const TYPES := ["prop", "wall_style", "surface_finish", "miniature",
	"left", "right", "ui_root", "actor_creation", "selected_rook"]


func _ready() -> void:
	%Initialize.pressed.connect(_initialize)
	%InstallDependencies.pressed.connect(_install_dependencies)
	%ChooseScene.pressed.connect(func(): %ScenePicker.popup_centered_ratio(0.7))
	%ScenePicker.file_selected.connect(_select_scene)
	%UseScene.pressed.connect(func():
		var root := EditorInterface.get_edited_scene_root()
		if root != null:
			_select_scene(root.scene_file_path))
	%Register.pressed.connect(_register)
	%PrepareModel.pressed.connect(_prepare_model)
	%EntryName.text_changed.connect(func(value: String):
		if not %EntryId.has_focus():
			%EntryId.text = _slug(value))
	%EntryType.item_selected.connect(func(_index: int): _update_help())
	%ChooseCover.pressed.connect(func(): _choose_media("cover"))
	%AddMedia.pressed.connect(func(): _choose_media("gallery"))
	%MediaPicker.file_selected.connect(func(path: String):
		_run_tool(PackedStringArray(["media", "--file", path, "--role", _media_role,
			"--caption", %MediaCaption.text.strip_edges()])))
	%RemoveMedia.pressed.connect(func(): _change_media(0))
	%MediaUp.pressed.connect(func(): _change_media(-1))
	%MediaDown.pressed.connect(func(): _change_media(1))
	refresh()


func _choose_media(role: String) -> void:
	_media_role = role
	%MediaPicker.filters = PackedStringArray(["*.png,*.jpg,*.jpeg,*.webp ; Images"])
	if role == "gallery": %MediaPicker.add_filter("*.ogv,*.mp4,*.webm", "Videos")
	%MediaPicker.popup_centered_ratio(0.7)


func _change_media(direction: int) -> void:
	var selected: PackedInt32Array = %MediaItems.get_selected_items()
	if selected.is_empty(): return
	var path: String = %MediaItems.get_item_metadata(selected[0])
	var args := PackedStringArray(["media", "--remove", path])
	if direction != 0: args.append_array(["--move", str(direction)])
	_run_tool(args)


func refresh() -> void:
	var exists := FileAccess.file_exists("res://rookframe.json")
	%NewPackage.visible = not exists
	%RegisterEntry.visible = exists
	%ReleaseMedia.visible = exists
	if exists:
		var manifest = JSON.parse_string(FileAccess.get_file_as_string("res://rookframe.json"))
		if manifest is Dictionary:
			%EntryType.set_item_disabled(7, manifest.get("kind") != "system-extension")
			%PackagePath.text = "Save Package scenes below:\nres://rookframe/packages/" + str(manifest.get("id", "")) + "/"
			%MediaItems.clear()
			var media: Dictionary = manifest.get("media", {})
			var entries: Array = media.get("gallery", []).duplicate()
			if media.get("cover") != null: entries.push_front(media.cover)
			for entry in entries:
				var label := "Cover" if entry == media.get("cover") else str(entry.type).capitalize()
				var index: int = %MediaItems.add_item(label + " · " + str(entry.get("caption", entry.path.get_file())))
				%MediaItems.set_item_metadata(index, entry.path)
			%Presentation.clear()
			for entry in manifest.get("presentations", []):
				%Presentation.add_item(entry.id)
	_update_help()


func _install_dependencies() -> void:
	if _install_worker != null:
		return
	%InstallDependencies.disabled = true
	%AuthorStatus.text = "Installing the pinned UI Kit and gd-plug bootstrap…"
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var args := PackedStringArray(["-X", "utf8",
		ProjectSettings.globalize_path("res://addons/rookframe_sdk/rookframe_authoring.py"),
		"install-dependencies", "--project", ProjectSettings.globalize_path("res://")])
	_install_worker = Thread.new()
	var error := _install_worker.start(func():
		var output: Array = []
		var code := OS.execute(python, args, output, true)
		return {"code": code, "output": "\n".join(output)})
	if error != OK:
		_install_worker = null
		%InstallDependencies.disabled = false
		%AuthorStatus.text = "Cannot start dependency installation: " + error_string(error)


func _process(_delta: float) -> void:
	if _install_worker != null and not _install_worker.is_alive():
		var result = _install_worker.wait_to_finish()
		_install_worker = null
		%InstallDependencies.disabled = false
		%AuthorStatus.text = "Dependencies installed. Open your Package scene or configure the Development tab." if result.code == OK else result.output
		EditorInterface.get_resource_filesystem().scan()


func _exit_tree() -> void:
	if _install_worker != null:
		_install_worker.wait_to_finish()
		_install_worker = null


func _slug(value: String) -> String:
	var result := ""
	for character in value.to_lower():
		result += character if character in "abcdefghijklmnopqrstuvwxyz0123456789_-" else "-"
	result = result.strip_edges().trim_prefix("-").trim_suffix("-")
	if result.is_empty() or result[0] not in "abcdefghijklmnopqrstuvwxyz":
		result = "entry-" + result
	return result


func _run_tool(arguments: PackedStringArray) -> bool:
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var args := PackedStringArray(["-X", "utf8",
		ProjectSettings.globalize_path("res://addons/rookframe_sdk/rookframe_authoring.py")])
	args.append_array(arguments)
	args.append_array(PackedStringArray(["--project", ProjectSettings.globalize_path("res://")]))
	var output: Array = []
	var code := OS.execute(python, args, output, true)
	%AuthorStatus.text = "\n".join(output)
	if code != OK:
		%AuthorStatus.grab_focus()
		return false
	EditorInterface.get_resource_filesystem().scan()
	refresh()
	var result = JSON.parse_string("\n".join(output))
	package_changed.emit(PackedStringArray(result.get("changedPaths", [])) if result is Dictionary else PackedStringArray())
	return true


func _initialize() -> void:
	if %PackageName.text.strip_edges().is_empty():
		%AuthorStatus.text = "Enter a Package name."
		%PackageName.grab_focus()
		return
	var form: int = %PackageKind.selected
	var args := PackedStringArray(["init", "--name", %PackageName.text.strip_edges(),
		"--kind", "system-extension" if form == 2 else "optional"])
	args.append("--content-only" if form == 1 else "--ui")
	if _run_tool(args):
		%AuthorStatus.text = "Package created. Install authoring dependencies, then configure the Development tab."


func _select_scene(path: String) -> void:
	%ScenePath.text = path
	if %EntryName.text.is_empty():
		%EntryName.text = path.get_file().get_basename().capitalize()
		%EntryId.text = _slug(%EntryName.text)


func _update_help() -> void:
	var kind: String = TYPES[%EntryType.selected]
	%PresentationRow.visible = kind not in TYPES.slice(0, 4)
	%EntryHelp.text = {
		"prop": "A Node3D scene with PropCollision/CollisionShape3D. Use Prepare model to add an editable box collider.",
		"wall_style": "A wall sample with a mesh named Surface; +X along the wall, +Y up. Prepare model adds the authored scale and sample width.",
		"surface_finish": "A floor/ceiling sample with a mesh named Surface in the XZ plane. Prepare model adds the appearance metadata.",
		"miniature": "An imported model or Node3D scene. It appears in Miniatures.",
	}.get(kind, "A Control scene. Its attached scripts and signals work normally. Rookframe registers it in the selected slot.")
	%PrepareModel.visible = kind in ["prop", "wall_style", "surface_finish"]


func _register() -> void:
	var path: String = %ScenePath.text.strip_edges()
	var edited := EditorInterface.get_edited_scene_root()
	# save_all_scenes() stops the running game. Save only the selected open scene
	# through the normal editor operation so the Development World stays alive.
	if edited != null and edited.scene_file_path == path:
		var saved := EditorInterface.save_scene()
		if saved != OK:
			%AuthorStatus.text = "Save the scene before registering it: " + error_string(saved)
			return
	EditorInterface.get_script_editor().save_all_scripts()
	var kind: String = TYPES[%EntryType.selected]
	if not ResourceLoader.exists(path):
		%AuthorStatus.text = "Choose a saved, imported scene."
		return
	var packed = ResourceLoader.load(path)
	if not packed is PackedScene:
		%AuthorStatus.text = "The selected file is not a scene."
		return
	var scene: Node = packed.instantiate()
	var valid := scene is Node3D if kind in TYPES.slice(0, 4) else scene is Control
	if kind == "prop":
		var collider := scene.get_node_or_null("PropCollision/CollisionShape3D") as CollisionShape3D
		valid = valid and collider != null and collider.shape != null
	if kind in ["wall_style", "surface_finish"]:
		var surfaces := scene.find_children("Surface", "MeshInstance3D", true, false)
		var surface: MeshInstance3D = null if surfaces.is_empty() else surfaces[0] as MeshInstance3D
		var span: float = scene.get_meta("authored_sample_span", 0.0)
		valid = valid and scene.get_meta("content_kind", "") == kind and scene.get_meta("authored_units_per_world_unit", 0.0) == 1.0 and is_finite(span) and span >= 0.05 and span <= 100.0
		valid = valid and surface != null and surface.mesh != null and surface.mesh.get_surface_count() == 1 and surface.get_active_material(0) is BaseMaterial3D
		if kind == "surface_finish":
			valid = valid and scene.get_meta("supports_floor", false) and scene.get_meta("supports_ceiling", false)
	scene.free()
	if not valid:
		%AuthorStatus.text = "The scene needs the structure described above. Prepare the model or edit its scene first."
		return
	var args := PackedStringArray(["register", "--scene", path,
		"--entry-id", %EntryId.text.strip_edges(), "--name", %EntryName.text.strip_edges(), "--entry-type", kind])
	if %Presentation.item_count > 0 and kind not in TYPES.slice(0, 4):
		args.append_array(PackedStringArray(["--presentation", %Presentation.get_item_text(%Presentation.selected)]))
	if _run_tool(args):
		%AuthorStatus.text = "Registered. The running Development World receives this change through Godot's importer."


func _prepare_model() -> void:
	var path: String = %ScenePath.text.strip_edges()
	var kind: String = TYPES[%EntryType.selected]
	var packed = ResourceLoader.load(path)
	if not packed is PackedScene:
		%AuthorStatus.text = "Choose an imported model or a saved Node3D scene."
		return
	var model: Node = packed.instantiate()
	if not model is Node3D:
		model.free()
		%AuthorStatus.text = "This operation requires a Node3D scene."
		return
	var root := Node3D.new()
	root.name = "Content"
	model.name = "Surface" if model is MeshInstance3D and model.name == "Surface" else "Model"
	root.add_child(model)
	model.owner = root
	var meshes: Array[Node] = model.find_children("*", "MeshInstance3D", true, false)
	if model is MeshInstance3D:
		meshes.push_front(model)
	var bounds := AABB()
	var first := true
	var surface_span := 0.0
	for mesh: MeshInstance3D in meshes:
		var pose := mesh.transform
		var parent := mesh.get_parent()
		while parent != root and parent is Node3D:
			pose = parent.transform * pose
			parent = parent.get_parent()
		var box: AABB = pose * mesh.get_aabb()
		if mesh.name == "Surface":
			surface_span = box.size.x
		bounds = box if first else bounds.merge(box)
		first = false
	if first or bounds.size.length_squared() == 0.0:
		root.free()
		%AuthorStatus.text = "The model has no usable mesh."
		return
	if kind == "prop":
		var body := StaticBody3D.new()
		body.name = "PropCollision"
		root.add_child(body)
		body.owner = root
		var collider := CollisionShape3D.new()
		collider.name = "CollisionShape3D"
		collider.shape = BoxShape3D.new()
		collider.shape.size = bounds.size.max(Vector3(0.01, 0.01, 0.01))
		collider.position = bounds.get_center()
		body.add_child(collider)
		collider.owner = root
	elif kind in ["wall_style", "surface_finish"]:
		if model.find_children("Surface", "MeshInstance3D", true, false).is_empty() and not (model is MeshInstance3D and model.name == "Surface"):
			root.free()
			%AuthorStatus.text = "Name the appearance's main mesh Surface in the source model or an authored scene, then prepare again."
			return
		root.set_meta("content_kind", kind)
		root.set_meta("authored_units_per_world_unit", 1.0)
		root.set_meta("authored_sample_span", surface_span)
		root.set_meta("supports_floor", kind == "surface_finish")
		root.set_meta("supports_ceiling", kind == "surface_finish")
	var manifest = JSON.parse_string(FileAccess.get_file_as_string("res://rookframe.json"))
	if not manifest is Dictionary or not manifest.get("id") is String:
		root.free()
		%AuthorStatus.text = "Correct rookframe.json before preparing a model."
		return
	var prefix := "res://rookframe/packages/" + str(manifest.id) + "/"
	if not path.begins_with(prefix):
		root.free()
		%AuthorStatus.text = "Move the source model into the Package directory first."
		return
	var target := prefix + "content/" + _slug(%EntryId.text) + ".tscn"
	if FileAccess.file_exists(target):
		root.free()
		%AuthorStatus.text = "That scene already exists. Choose a new ID or edit the existing scene."
		return
	DirAccess.make_dir_recursive_absolute(target.get_base_dir())
	var wrapper := PackedScene.new()
	var result := wrapper.pack(root)
	if result == OK:
		result = ResourceSaver.save(wrapper, target)
	root.free()
	if result != OK:
		%AuthorStatus.text = "Cannot save the prepared scene: " + error_string(result)
		return
	EditorInterface.get_resource_filesystem().scan()
	%ScenePath.text = target
	EditorInterface.open_scene_from_path(target)
	%AuthorStatus.text = "Scene prepared. Inspect its scale and collision, then register it. The imported model remains linked."
