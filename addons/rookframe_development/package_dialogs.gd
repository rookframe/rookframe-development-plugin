@tool
extends Control

var model: RefCounted
var run_tool: Callable
var _presentation := ""
var _media_path := ""
var _media_role := "gallery"


func configure(source: RefCounted, runner: Callable) -> void:
	model = source
	run_tool = runner
	model.changed.connect(func():
		if %ReleaseDialog.visible:
			_media_list())


func _ready() -> void:
	%Setup.confirmed.connect(_create)
	%PackageKind.item_selected.connect(func(_index: int): _setup_description())
	%StarterUi.toggled.connect(func(_value: bool): _setup_description())
	%SettingsDialog.confirmed.connect(_save_settings)
	%CopyPackageId.pressed.connect(func(): DisplayServer.clipboard_set(str(model.manifest.id)))
	%BehaviorConfirm.confirmed.connect(_add_behavior)
	%PresentationDialog.confirmed.connect(_save_presentation)
	for button: CheckBox in [%Desktop, %Tablet, %Phone]:
		button.toggled.connect(func(_value: bool): _coverage())
	%RemovePresentation.pressed.connect(func(): %RemovePresentationConfirm.popup_centered())
	%RemovePresentationConfirm.confirmed.connect(_remove_presentation)
	%ReleaseDialog.confirmed.connect(_save_release)
	%MediaItems.item_selected.connect(_select_media)
	%Caption.focus_exited.connect(_save_caption)
	%Caption.text_submitted.connect(func(_text: String): _save_caption())
	%ChooseCover.pressed.connect(func(): _pick_media("cover"))
	%AddMedia.pressed.connect(func(): _pick_media("gallery"))
	%MediaPicker.file_selected.connect(_attach_media)
	%RemoveMedia.pressed.connect(func(): _edit_media(0))
	%MediaUp.pressed.connect(func(): _edit_media(-1))
	%MediaDown.pressed.connect(func(): _edit_media(1))


func open_setup() -> void:
	%SetupError.text = ""
	_setup_description()
	_show_dialog(%Setup, Vector2i(560, 540))


func _show_dialog(dialog: Window, desired: Vector2i) -> void:
	var available := get_window().size
	var scaled := Vector2i(Vector2(desired) * EditorInterface.get_editor_scale())
	var bounded := Vector2i(mini(scaled.x, int(available.x * 0.9)), mini(scaled.y, int(available.y * 0.85)))
	dialog.popup_centered_clamped(bounded, 0.9)


func _setup_description() -> void:
	var kind: int = %PackageKind.selected
	%KindHelp.text = [
		"Content pack · Miniatures, Props, Wall Styles, Surface Finishes and Themes that people can add to a World. Start with folders; author your own Godot scenes.",
		"Extension · Add behavior or UI to an existing System. Work in GDScript and ordinary Control scenes. The SDK and UI Kit are available separately.",
		"System Extension · Define a game's rules and Actor workflows. A World uses one System. Start with your own GDScript; no game rules or Actor schema are generated."
	][kind]
	%StarterUi.visible = kind != 0
	%Structure.text = "Creates:\nrookframe.json — Package details\nrookframe/packages/<Package ID>/\n  content/ — scenes and Resources you share\n  assets/ — imported models, textures and audio\nexport_presets.cfg — Package build preset"
	if kind != 0:
		%Structure.text += "\n  logic/implementation.gd — behavior entry point"
		if %StarterUi.button_pressed:
			%Structure.text += "\n  ui/presentation.gd + window.tscn — starter UI"


func _create() -> void:
	var name: String = %PackageName.text.strip_edges()
	if name.is_empty():
		%SetupError.text = "Enter a Package name."
		return
	if FileAccess.file_exists("res://rookframe.json"):
		%SetupError.text = "This project already contains rookframe.json. Reload the Package."
		return
	var args := PackedStringArray(["init", "--name", name])
	if %PackageKind.selected == 0:
		args.append("--content-only")
	elif %PackageKind.selected == 2:
		args.append_array(PackedStringArray(["--kind", "system-extension"]))
	if %PackageKind.selected != 0 and %StarterUi.button_pressed:
		args.append("--ui")
	run_tool.call("init", args)
	%Setup.hide()


func open_settings() -> void:
	%SettingsName.text = str(model.manifest.name)
	%Summary.text = str(model.manifest.get("summary", ""))
	%PackageId.text = str(model.manifest.id)
	%Api.text = "Application API " + str(model.manifest.sdk.edition) + ", minimum revision " + str(model.manifest.sdk.minimumRevision)
	var compatibility: Dictionary = model.manifest.get("rookframeCompatibility", {})
	%Minimum.text = str(compatibility.get("minimum", ""))
	%Verified.text = str(compatibility.get("verified", ""))
	%Maximum.text = str(compatibility.get("maximum", ""))
	%SettingsError.text = ""
	_show_dialog(%SettingsDialog, Vector2i(540, 520))


func _version(value: String) -> bool:
	var expression := RegEx.new()
	expression.compile("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?(\\+[0-9A-Za-z.-]+)?$")
	return expression.search(value) != null


func _save_settings() -> void:
	if %SettingsName.text.strip_edges().is_empty() or not _version(%Minimum.text) or not _version(%Verified.text) or (not %Maximum.text.is_empty() and not _version(%Maximum.text)):
		%SettingsError.text = "Enter a name and versions such as 0.1.0. Maximum is optional."
		return
	var next: Dictionary = model.manifest.duplicate(true)
	next.name = %SettingsName.text.strip_edges()
	next.summary = %Summary.text.strip_edges()
	next.rookframeCompatibility = {"minimum": %Minimum.text, "verified": %Verified.text}
	if not %Maximum.text.is_empty():
		next.rookframeCompatibility.maximum = %Maximum.text
	model.change("Edit Package details", next)
	%SettingsDialog.hide()


func open_behavior() -> void:
	%BehaviorConfirm.popup_centered()


func _add_behavior() -> void:
	var path: String = model.root_path() + "logic/implementation.gd"
	if FileAccess.file_exists(path):
		model.problem.emit("Keep the existing logic/implementation.gd. Choose its entry point in rookframe.json.")
		return
	var before: Dictionary = model.manifest.duplicate(true)
	var next := before.duplicate(true)
	next.implementation = {"entryPoint": "logic/implementation.gd"}
	model.change("Add Package behavior", next)
	if not model.save("behavior"):
		model.change("Restore Package after failed source creation", before)
		return
	EditorInterface.get_resource_filesystem().scan()
	EditorInterface.edit_script(ResourceLoader.load(path))


func open_presentation(id := "") -> void:
	_presentation = id
	var entry: Dictionary = {}
	for item: Dictionary in model.manifest.get("presentations", []):
		if item.id == id:
			entry = item
	%PresentationId.text = id if not id.is_empty() else "presentation-" + Crypto.new().generate_random_bytes(4).hex_encode()
	%PresentationId.visible = not entry.is_empty()
	%PresentationIdLabel.visible = not entry.is_empty()
	var starter_path := "ui/default.gd"
	var suffix := 2
	while FileAccess.file_exists(model.root_path() + starter_path) or FileAccess.file_exists(model.root_path() + starter_path.trim_suffix(".gd") + ".tscn"):
		starter_path = "ui/presentation_" + str(suffix) + ".gd"
		suffix += 1
	%EntryPoint.text = str(entry.get("entryPoint", starter_path))
	%CreateSource.button_pressed = entry.is_empty()
	%CreateSource.visible = entry.is_empty()
	%RemovePresentation.visible = not entry.is_empty()
	%Desktop.button_pressed = "desktop" in entry.get("experiences", ["desktop"])
	%Tablet.button_pressed = "tablet" in entry.get("experiences", [])
	%Phone.button_pressed = "phone" in entry.get("experiences", [])
	%PresentationError.text = ""
	_coverage()
	_show_dialog(%PresentationDialog, Vector2i(560, 510))


func _experiences() -> Array:
	var result: Array = []
	for pair in [[%Desktop, "desktop"], [%Tablet, "tablet"], [%Phone, "phone"]]:
		if pair[0].button_pressed:
			result.append(pair[1])
	return result


func _coverage() -> void:
	var lines: PackedStringArray = []
	var removes := false
	for item: Dictionary in model.manifest.get("presentations", []):
		if item.id == _presentation:
			continue
		var transferred: Array = item.experiences.filter(func(value: String): return value in _experiences())
		if not transferred.is_empty():
			lines.append("Move " + ", ".join(transferred) + " from “" + str(item.entryPoint).get_file().get_basename().capitalize() + "” to this Presentation.")
			if transferred.size() == item.experiences.size():
				removes = true
				lines.append("Remove its empty declaration. Its source files stay in the project.")
	if lines.is_empty():
		lines.append("One Presentation handles each selected experience. Unassigned experiences fall back to another available Presentation. Without Presentations, this Package has no UI.")
	%Coverage.text = "\n".join(lines)
	%RemoveEmpty.visible = removes
	%RemoveEmpty.button_pressed = false


func _save_presentation() -> void:
	if %RemoveEmpty.visible and not %RemoveEmpty.button_pressed:
		%PresentationError.text = "Keep an experience on the other Presentation, or confirm removing its empty declaration."
		return
	var path: String = %EntryPoint.text.strip_edges()
	if _experiences().is_empty() or not model.owns(model.root_path() + path) or not path.ends_with(".gd"):
		%PresentationError.text = "Select at least one experience and a .gd path inside this Package."
		return
	var creating: bool = _presentation.is_empty() and %CreateSource.button_pressed
	if creating:
		if not path.begins_with("ui/") or FileAccess.file_exists(model.root_path() + path) or FileAccess.file_exists(model.root_path() + path.trim_suffix(".gd") + ".tscn"):
			%PresentationError.text = "Choose a new path below ui/, or use an existing script without creating a starter."
			return
	elif not FileAccess.file_exists(model.root_path() + path):
		%PresentationError.text = "Choose an existing script, or enable Create starter."
		return
	var before: Dictionary = model.manifest.duplicate(true)
	model.change("Edit Presentation coverage", model.presentation_changes(%PresentationId.text, path, _experiences()))
	if not model.save("presentation" if creating else "save", {"entryPoint": path}):
		model.change("Restore Presentation after failed save", before)
		%PresentationError.text = "Could not save. See the Package status for details."
		return
	%PresentationDialog.hide()


func _remove_presentation() -> void:
	var next: Dictionary = model.manifest.duplicate(true)
	next.presentations = next.get("presentations", []).filter(func(item: Dictionary): return item.id != _presentation)
	if next.presentations.is_empty():
		next.erase("presentations")
	model.change("Remove Presentation declaration", next)
	%PresentationDialog.hide()


func open_release() -> void:
	%Version.text = str(model.manifest.version)
	%ReleaseHelp.text = "Set the release version and optional cover or gallery. Build Package creates a checked local archive." + (
		" This System's cover is also the default image for games using it." if model.manifest.kind == "system-extension" else "")
	%ReleaseError.text = ""
	_media_path = ""
	_media_list()
	_show_dialog(%ReleaseDialog, Vector2i(580, 580))


func _media_list() -> void:
	%MediaItems.clear()
	var media: Dictionary = model.manifest.get("media", {})
	var items: Array = []
	if media.has("cover"):
		items.append(media.cover)
	items.append_array(media.get("gallery", []))
	for item: Dictionary in items:
		var index: int = %MediaItems.add_item(("Cover · " if item == media.get("cover") else "Gallery · ") + str(item.get("caption", "") if not str(item.get("caption", "")).is_empty() else str(item.path).get_file()))
		%MediaItems.set_item_metadata(index, item)
		%MediaItems.set_item_tooltip(index, str(item.path))
		if item.path == _media_path:
			%MediaItems.select(index)
	%Caption.editable = not _media_path.is_empty() and not model.busy
	if not %Caption.has_focus():
		%Caption.text = ""
		for item: Dictionary in items:
			if item.path == _media_path:
				%Caption.text = str(item.get("caption", ""))
	var gallery: Array = media.get("gallery", [])
	var position := -1
	for index in gallery.size():
		if gallery[index].path == _media_path:
			position = index
	%RemoveMedia.disabled = _media_path.is_empty() or model.busy
	%MediaUp.disabled = position <= 0 or model.busy
	%MediaDown.disabled = position < 0 or position >= gallery.size() - 1 or model.busy


func _select_media(index: int) -> void:
	_media_path = str(%MediaItems.get_item_metadata(index).path)
	_media_list()


func _save_caption() -> void:
	if _media_path.is_empty():
		return
	var next: Dictionary = model.manifest.duplicate(true)
	var media: Dictionary = next.get("media", {})
	var items: Array = media.get("gallery", []).duplicate()
	if media.has("cover"):
		items.append(media.cover)
	for item: Dictionary in items:
		if item.path == _media_path:
			item.caption = %Caption.text
	model.change("Edit release media caption", next)


func _edit_media(direction: int) -> void:
	var next: Dictionary = model.manifest.duplicate(true)
	var media: Dictionary = next.get("media", {})
	if direction == 0:
		if media.get("cover", {}).get("path") == _media_path:
			media.erase("cover")
		else:
			media.gallery = media.get("gallery", []).filter(func(item: Dictionary): return item.path != _media_path)
		_media_path = ""
	else:
		var gallery: Array = media.get("gallery", [])
		for index in gallery.size():
			if gallery[index].path == _media_path and index + direction >= 0 and index + direction < gallery.size():
				var item: Dictionary = gallery.pop_at(index)
				gallery.insert(index + direction, item)
				break
	model.change("Edit release media", next)


func _pick_media(role: String) -> void:
	_media_role = role
	%MediaPicker.popup_centered_ratio(0.65)


func _attach_media(path: String) -> void:
	_save_caption()
	if model.save():
		run_tool.call("media", PackedStringArray(["media", "--file", path, "--role", _media_role, "--expected-manifest", FileAccess.get_sha256("res://rookframe.json")]))


func _save_release() -> void:
	if not _version(%Version.text):
		%ReleaseError.text = "Enter a release version such as 0.1.0."
		return
	_save_caption()
	var next: Dictionary = model.manifest.duplicate(true)
	next.version = %Version.text
	model.change("Edit release version", next)
	%ReleaseDialog.hide()


func operation_finished(_success: bool) -> void:
	if %ReleaseDialog.visible:
		_media_list()
