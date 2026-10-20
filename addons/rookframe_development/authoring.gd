@tool
extends VBoxContainer

signal package_changed(paths: PackedStringArray)
var model: RefCounted
var _worker: Thread
var _job := ""
var _kind := -1
var _code_draft: Dictionary = {}
var _settings_open_manifest: Dictionary = {}
var _code_rows: Array[Dictionary] = []
var _implementation: EditorResourcePicker
var _implementation_changed := false
var _rebuilding := false
var _pending_settings: Dictionary = {}
var _repair_id := ""
var _after_scan := ""
const API := "https://github.com/rookframe/rookframe-sdk/blob/main/API.md"


func _ready() -> void:
	%Create.pressed.connect(func(): %Setup.popup_centered_clamped(Vector2i(580, 540) * EditorInterface.get_editor_scale(), 0.85); %PackageName.grab_focus())
	var group := ButtonGroup.new()
	for index in range(3):
		var choice: CheckBox = [%ContentChoice, %ExtensionChoice, %SystemChoice][index]
		choice.button_group = group
		choice.toggled.connect(func(value: bool):
			if value:
				_kind = index
				%StarterUI.visible = index > 0
				%Setup.get_ok_button().disabled = false)
	%Setup.get_ok_button().disabled = true
	%Setup.confirmed.connect(_create)
	%Check.pressed.connect(func(): run_command("check"))
	%Build.pressed.connect(func():
		%BuildVersion.text = str(model.manifest.get("version", "0.1.0"))
		%BuildDialog.popup_centered_clamped(Vector2i(520, 320) * EditorInterface.get_editor_scale(), 0.85))
	%BuildDialog.confirmed.connect(_build)
	var menu: PopupMenu = %SettingsMenu.get_popup()
	for item in ["Package settings…", "Optional libraries…", "Authoring guide"]:
		menu.add_item(item)
	menu.id_pressed.connect(_menu)
	%Settings.confirmed.connect(_save_settings)
	%Settings.get_cancel_button().pressed.connect(func(): _pending_settings = {})
	%ConfirmAssignments.confirmed.connect(_commit_settings)
	%NewUI.pressed.connect(_new_ui)
	%CreateUI.confirmed.connect(_create_ui)
	%SDK.pressed.connect(func(): install_library("sdk"))
	%UIKit.pressed.connect(func(): install_library("ui-kit"))
	%API.pressed.connect(func(): OS.shell_open(API))
	%EnableCode.pressed.connect(func():
		%SettingsTabs.set_tab_hidden(1, false)
		%SettingsTabs.current_tab = 1
		%EnableCode.hide())
	%RepairPicker.file_selected.connect(_repair)
	%Results.meta_clicked.connect(func(meta: Variant): _open_source(str(meta)))
	%CopyDraft.pressed.connect(func(): DisplayServer.clipboard_set(JSON.stringify(model.manifest, "\t")))
	%Reload.pressed.connect(func(): model.reload(true))
	%OpenManifest.pressed.connect(func(): EditorInterface.get_file_system_dock().navigate_to_path("res://rookframe.json"))
	EditorInterface.get_resource_filesystem().filesystem_changed.connect(_scanned)
	%Setup.dialog_hide_on_ok = false
	%Settings.dialog_hide_on_ok = false
	%CreateUI.dialog_hide_on_ok = false
	%BuildDialog.dialog_hide_on_ok = false
	# Subdialogs are children of the active native modal.
	%CreateUI.reparent(%Settings)
	%ConfirmAssignments.reparent(%Settings)
	%RepairPicker.reparent(%Settings)


func _menu(id: int) -> void:
	match id:
		0: show_settings()
		1: show_libraries()
		2: show_guide()


func show_libraries() -> void:
	var inventory: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://.rookframe/optional-libraries.json")) if FileAccess.file_exists("res://.rookframe/optional-libraries.json") else {}
	if not inventory is Dictionary: inventory = {}
	for pair in [["sdk", %SDK], ["ui-kit", %UIKit]]:
		var installed: String = str(inventory.get(pair[0], {}).get("version", ""))
		pair[1].text = ("Download latest " if installed.is_empty() else "Update ") + ("SDK" if pair[0] == "sdk" else "UI Kit")
		pair[1].tooltip_text = "Installed: " + installed if not installed.is_empty() else "Not installed locally"
	%Libraries.popup_centered_clamped(Vector2i(520, 320) * EditorInterface.get_editor_scale(), 0.85)


func configure(package: RefCounted) -> void:
	model = package
	model.changed.connect(refresh)
	model.saved.connect(func(paths: PackedStringArray): package_changed.emit(paths))
	model.problem.connect(_problem)
	refresh()


func refresh() -> void:
	if model == null: return
	var exists: bool = not model.manifest.is_empty()
	%Create.visible = not exists
	%PackageHeader.visible = exists
	%PackageActions.visible = exists
	%Conflict.visible = not model.conflict.is_empty()
	%ConflictText.text = model.conflict
	if exists:
		%PackageTitle.text = str(model.manifest.get("name", "Package")) + (" *" if model.dirty() else "")
		%PackageTitle.tooltip_text = str(model.manifest.get("name", ""))
		%Check.disabled = model.busy
		%Build.disabled = model.busy
	%SettingsMenu.disabled = model.busy


func _create() -> void:
	var name: String = %PackageName.text.strip_edges()
	if name.is_empty() or _kind < 0:
		%SetupError.text = "Enter a name and choose what you want to make."
		return
	var args := PackedStringArray(["init", "--name", name, "--kind", "system-extension" if _kind == 2 else "optional"])
	if _kind == 0: args.append("--content-only")
	elif %StarterUI.button_pressed: args.append("--ui")
	_start("init", "rookframe_authoring.py", args)


func show_guide() -> void:
	var root: String = model.root_path()
	var has_code: bool = model.manifest.has("implementation") or model.manifest.has("presentations")
	%GuideText.text = (
		"1. Keep source models and images in assets/.\n\n"
		+ "2. Create and save your scenes in content/ using Godot. Build their nodes and collision shapes yourself.\n\n"
		+ "3. Select a scene's root. Open the Rookframe Content tab beside the Inspector, choose its use, and Add to Package.\n\n"
		+ "This tab follows the scene or Resource you inspect. Edit its Content properties there and save normally (Ctrl+S).\n\n"
		+ "Your Package folder:\n" + root)
	if has_code:
		%GuideText.text = (
			"Edit logic/implementation.gd for behavior. Its comments explain the entry contract and link to the domain API.\n\n"
			+ "Build UI scenes normally in Godot. Package settings → Code & UI assigns the UI entry script and its devices. New UI creates an editable scene and entry script.\n\n"
			+ ("A System defines the World's Actors, rules and shared data through the domain API.\n\n" if model.manifest.get("kind") == "system-extension" else "")
			+ "Scenes and Resources can also be Content: save in content/, select the scene root or Resource, then open the Rookframe Content tab beside the Inspector.\n\n"
			+ "Your Package folder:\n" + root)
	%Guide.title = "Author your Package"
	%Guide.popup_centered_clamped(Vector2i(600, 460) * EditorInterface.get_editor_scale(), 0.85)


func _scanned() -> void:
	if _after_scan.is_empty(): return
	var path := _after_scan
	_after_scan = ""
	EditorInterface.get_file_system_dock().navigate_to_path(path)
	if path.ends_with(".gd"): _open_source(path)


func show_settings() -> void:
	if model.busy: return
	if model.manifest.is_empty():
		_problem("Create a Package from the Rookframe dock first.")
		return
	%SettingsName.text = str(model.manifest.get("name", ""))
	%Summary.text = str(model.manifest.get("summary", ""))
	%Minimum.text = str(model.manifest.get("rookframeCompatibility", {}).get("minimum", "0.1.0"))
	%Verified.text = str(model.manifest.get("rookframeCompatibility", {}).get("verified", "0.1.0"))
	%Edition.text = str(model.manifest.get("sdk", {}).get("edition", "2029"))
	%Revision.value = int(model.manifest.get("sdk", {}).get("minimumRevision", 34))
	%PackageId.text = str(model.manifest.get("id", ""))
	%SettingsError.text = ""
	_code_draft = model.manifest.duplicate(true)
	_settings_open_manifest = model.manifest.duplicate(true)
	var has_code: bool = _code_draft.has("implementation") or _code_draft.has("presentations") or _code_draft.get("kind") == "system-extension"
	%SettingsTabs.set_tab_hidden(1, not has_code)
	%EnableCode.visible = not has_code
	%SettingsTabs.current_tab = 0
	_refresh_code()
	_missing_sources()
	%Settings.popup_centered_clamped(Vector2i(650, 560) * EditorInterface.get_editor_scale(), 0.85)


func _picker(path: String) -> EditorResourcePicker:
	var picker := EditorResourcePicker.new()
	picker.base_type = "GDScript"
	picker.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	if not path.is_empty() and ResourceLoader.exists(model.root_path() + path):
		picker.edited_resource = load(model.root_path() + path)
	picker.resource_selected.connect(func(resource: Resource, _inspect: bool): EditorInterface.edit_resource(resource))
	return picker


func _refresh_code() -> void:
	_rebuilding = true
	for child in %BehaviorSlot.get_children(): child.queue_free()
	for child in %Presentations.get_children(): child.queue_free()
	_code_rows.clear()
	_implementation = _picker(_code_draft.get("implementation", {}).get("entryPoint", ""))
	%BehaviorSlot.add_child(_implementation)
	_implementation_changed = false
	_implementation.resource_changed.connect(func(_resource: Resource): _implementation_changed = true)
	var behavior_path: String = _code_draft.get("implementation", {}).get("entryPoint", "")
	%MissingBehavior.text = "Missing: " + behavior_path if not behavior_path.is_empty() and _implementation.edited_resource == null else ""
	for entry: Dictionary in _code_draft.get("presentations", []):
		var box := VBoxContainer.new()
		box.add_theme_constant_override("separation", 6)
		%Presentations.add_child(box)
		if not ResourceLoader.exists(model.root_path() + str(entry.entryPoint)):
			var missing := Label.new()
			missing.text = "Missing: " + str(entry.entryPoint)
			missing.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			box.add_child(missing)
		var row := HBoxContainer.new()
		box.add_child(row)
		var picker := _picker(str(entry.entryPoint))
		row.add_child(picker)
		var remove := Button.new()
		remove.text = "Remove"
		remove.tooltip_text = "Remove this UI assignment; source files stay"
		row.add_child(remove)
		var devices := HBoxContainer.new()
		box.add_child(devices)
		var record := {"entry": entry, "picker": picker, "devices": {}}
		for device in ["desktop", "tablet", "phone"]:
			var check := CheckBox.new()
			check.text = device.capitalize()
			check.button_pressed = device in entry.experiences
			devices.add_child(check)
			record.devices[device] = check
			check.toggled.connect(func(value: bool):
				if _rebuilding or not value: return
				for other in _code_rows:
					if other != record and not other.get("removed", false) and other.devices[device].button_pressed:
						other.devices[device].set_pressed_no_signal(false)
						%SettingsError.text = device.capitalize() + " will move from " + str(other.entry.entryPoint) + " to this UI when you save.")
		_code_rows.append(record)
		remove.pressed.connect(func():
			record["removed"] = true
			box.hide()
			%SettingsError.text = "This UI assignment will be removed on Save. Its files stay.")
		box.add_child(HSeparator.new())
	_rebuilding = false


func _collect_code(next: Dictionary) -> bool:
	if %SettingsTabs.is_tab_hidden(1): return true
	var resource: Resource = _implementation.edited_resource
	if resource == null and not _implementation_changed and next.has("implementation"):
		pass # A missing file is not an author request to remove its declaration.
	elif resource == null:
		if next.kind == "system-extension":
			%SettingsError.text = "A System needs a behavior entry script."
			return false
		next.erase("implementation")
	elif not _valid_script(resource):
		%SettingsError.text = "Save the behavior script as .gd inside your Package."
		return false
	else:
		var implementation: Dictionary = next.get("implementation", {}).duplicate(true)
		implementation.entryPoint = resource.resource_path.trim_prefix(model.root_path())
		next["implementation"] = implementation
	var presentations := []
	for row in _code_rows:
		if row.get("removed", false): continue
		if not _valid_script(row.picker.edited_resource):
			%SettingsError.text = "Each UI needs a saved .gd script inside your Package."
			return false
		var devices := []
		for device in row.devices:
			if row.devices[device].button_pressed: devices.append(device)
		if devices.is_empty():
			%SettingsError.text = "Give " + str(row.entry.entryPoint) + " a device, or Remove its assignment."
			return false
		var presentation: Dictionary = row.entry.duplicate(true)
		presentation.entryPoint = row.picker.edited_resource.resource_path.trim_prefix(model.root_path())
		presentation.experiences = devices
		presentations.append(presentation)
	if presentations.is_empty(): next.erase("presentations")
	else: next["presentations"] = presentations
	return true


func _valid_script(resource: Resource) -> bool:
	return resource is GDScript and model.owns(resource.resource_path) and resource.resource_path.ends_with(".gd") and FileAccess.file_exists(resource.resource_path)


func _save_settings() -> void:
	var next := _settings_value()
	if next.is_empty(): return
	_pending_settings = next
	var before: Array = model.manifest.get("presentations", [])
	if before != next.get("presentations", []) or model.manifest.get("implementation") != next.get("implementation"):
		%ConfirmAssignments.dialog_text = "Save these Package entry assignments?\n\n" + _assignment_summary(next) + "\n\nSource files stay. Existing UI coverage is replaced by the assignments above."
		%ConfirmAssignments.popup_centered_clamped(Vector2i(600, 460) * EditorInterface.get_editor_scale(), 0.85)
	else: _commit_settings()


func _settings_value() -> Dictionary:
	if model.manifest != _settings_open_manifest:
		%SettingsError.text = "The Package changed while settings were open. Cancel and reopen settings before saving; your files have been preserved."
		return {}
	if %SettingsName.text.strip_edges().is_empty():
		%SettingsError.text = "Enter a Package name."
		return {}
	var semver := RegEx.new()
	semver.compile("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$")
	if semver.search(%Minimum.text.strip_edges()) == null or semver.search(%Verified.text.strip_edges()) == null:
		%SettingsError.text = "Use application versions such as 0.1.0 in General."
		return {}
	if %Edition.text.strip_edges() != "2029":
		%SettingsError.text = "This application supports API Edition 2029, revisions 1–34."
		return {}
	var next: Dictionary = model.manifest.duplicate(true)
	next.name = %SettingsName.text.strip_edges()
	next.summary = %Summary.text.strip_edges()
	var compatibility: Dictionary = next.get("rookframeCompatibility", {}).duplicate(true)
	compatibility.merge({"minimum": %Minimum.text.strip_edges(), "verified": %Verified.text.strip_edges()}, true)
	next.rookframeCompatibility = compatibility
	var sdk: Dictionary = next.get("sdk", {}).duplicate(true)
	sdk.merge({"edition": %Edition.text.strip_edges(), "minimumRevision": int(%Revision.value)}, true)
	next.sdk = sdk
	if not _collect_code(next): return {}
	return next


func _assignment_summary(value: Dictionary) -> String:
	var lines := PackedStringArray()
	lines.append("Behavior: " + str(value.get("implementation", {}).get("entryPoint", "none")))
	for item: Dictionary in value.get("presentations", []):
		lines.append(str(item.entryPoint) + " → " + ", ".join(item.experiences))
	return "\n".join(lines)


func _commit_settings() -> void:
	if model.manifest != _settings_open_manifest:
		%SettingsError.text = "The Package changed while confirmation was open. Cancel and reopen settings before saving."
		return
	model.change("Edit Package settings", _pending_settings)
	if model.save(): %Settings.hide()


func _new_ui() -> void:
	%UIScriptName.text = ""
	%UIError.text = ""
	%UIConsequences.text = ""
	%CreateUI.popup_centered_clamped(Vector2i(560, 320) * EditorInterface.get_editor_scale(), 0.85)


func _create_ui() -> void:
	var name: String = %UIScriptName.text.strip_edges()
	var pattern := RegEx.new()
	pattern.compile("^[a-z][a-z0-9_]*$")
	if pattern.search(name) == null:
		%UIError.text = "Name the script with lowercase letters, numbers and underscores."
		return
	var devices := []
	for device in ["desktop", "tablet", "phone"]:
		if get_node("%UI" + device.capitalize()).button_pressed: devices.append(device)
	if devices.is_empty():
		%UIError.text = "Choose at least one device."
		return
	var next := _settings_value()
	# Keep all open settings edits in the same transaction as the new UI.
	if next.is_empty():
		%UIError.text = %SettingsError.text
		return
	var replacements := PackedStringArray()
	for item: Dictionary in next.get("presentations", []):
		for device in devices:
			if device in item.experiences: replacements.append(device.capitalize() + ": " + str(item.entryPoint))
	var warning := "\n".join(replacements)
	if not warning.is_empty() and %UIConsequences.text != "Reassign on Create:\n" + warning:
		%UIConsequences.text = "Reassign on Create:\n" + warning
		%UIError.text = "Review the device changes above, then choose Create to continue."
		return
	var path := "ui/" + name + ".gd"
	if FileAccess.file_exists(model.root_path() + path) or FileAccess.file_exists(model.root_path() + "ui/" + name + ".tscn"):
		%UIError.text = "That source already exists. Assign it in Code & UI, or choose a new name."
		return
	# Construct the whole change before committing or creating files.
	var presentations := []
	for item: Dictionary in next.get("presentations", []):
		item.experiences = item.experiences.filter(func(device: String): return device not in devices)
		if not item.experiences.is_empty(): presentations.append(item)
	presentations.append({"id": "ui-" + Crypto.new().generate_random_bytes(8).hex_encode(), "entryPoint": path, "experiences": devices})
	next.presentations = presentations
	if model.save("presentation", {"entryPoint": path}, next, "Create Package UI"):
		%CreateUI.hide()
		%Settings.hide()
		_after_scan = model.root_path() + path
		EditorInterface.get_resource_filesystem().scan()


func _missing_sources() -> void:
	for child in %Repairs.get_children(): child.queue_free()
	var missing := 0
	for entry: Dictionary in model.entries():
		var path: String = model.root_path() + str(entry.payload)
		if FileAccess.file_exists(path): continue
		missing += 1
		var row := VBoxContainer.new()
		%Repairs.add_child(row)
		var label := Label.new()
		label.text = str(entry.displayName) + "\nMissing: " + str(entry.payload)
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		row.add_child(label)
		var button := Button.new()
		button.text = "Locate source…"
		button.pressed.connect(func(): _repair_id = entry.id; %RepairPicker.popup_centered_ratio(0.7))
		row.add_child(button)
		var remove := Button.new()
		remove.text = "Remove declaration…"
		remove.pressed.connect(func():
			var confirm := ConfirmationDialog.new()
			%Settings.add_child(confirm)
			confirm.dialog_text = "Remove " + str(entry.displayName) + "?\nWorlds using this Content ID will no longer find it. Undo restores it."
			confirm.confirmed.connect(func():
				if model.manifest != _settings_open_manifest:
					%SettingsError.text = "The Package changed while settings were open. Reopen settings before removing this declaration."
					confirm.queue_free()
					return
				model.remove_entry(entry.id)
				model.save()
				_settings_open_manifest = model.manifest.duplicate(true)
				_missing_sources()
				confirm.queue_free())
			confirm.canceled.connect(confirm.queue_free)
			confirm.popup_centered())
		row.add_child(remove)
	%SettingsTabs.set_tab_hidden(3, missing == 0)


func _repair(path: String) -> void:
	if model.manifest != _settings_open_manifest:
		%SettingsError.text = "The Package changed while settings were open. Reopen settings before repairing this source."
		return
	if not model.owns(path) or not model.entry_at(path).is_empty():
		%SettingsError.text = "Choose an undeclared source inside this Package."
		return
	var entry: Dictionary = model.entry_by_id(_repair_id)
	var resource := ResourceLoader.load(path)
	if (entry.get("type") == "theme" and not resource is Theme) or (entry.get("type") in ["miniature", "prop", "wall_style", "surface_finish"] and not resource is PackedScene):
		%SettingsError.text = "Choose a source matching this Content type."
		return
	model.update_entry(_repair_id, {"payload": path.trim_prefix(model.root_path())})
	model.save()
	_settings_open_manifest = model.manifest.duplicate(true)
	_missing_sources()


func _build() -> void:
	var version: String = %BuildVersion.text.strip_edges()
	var regex := RegEx.new()
	regex.compile("^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$")
	if regex.search(version) == null:
		%BuildError.text = "Use a version such as 1.0.0 or 1.0.0-alpha.1."
		return
	var next: Dictionary = model.manifest.duplicate(true)
	next.version = version
	model.change("Set Package build version", next)
	%BuildDialog.hide()
	run_command("build")


func run_command(command: String) -> void:
	if model.manifest.is_empty():
		_problem("Create a Package from the Rookframe dock first.")
		return
	get_tree().call_group("rookframe_content_properties", "commit_fields")
	if not EditorInterface.get_unsaved_scenes().is_empty():
		_problem("Save your scenes in Godot before checking or building this Package.\n\n" + "\n".join(EditorInterface.get_unsaved_scenes()))
		return
	EditorInterface.get_script_editor().save_all_scripts()
	if not model.save(): return
	_start(command, "rookframe_authoring.py", PackedStringArray([command]))


func install_library(library: String) -> void:
	_start(library, "optional_libraries.py", PackedStringArray(["--library", library]))


func _start(job: String, script: String, arguments: PackedStringArray) -> void:
	if _worker != null: return
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty(): python = "python" if OS.get_name() == "Windows" else "python3"
	var args := PackedStringArray(["-X", "utf8", ProjectSettings.globalize_path("res://addons/rookframe_development/" + script)])
	args.append_array(arguments)
	args.append_array(["--project", ProjectSettings.globalize_path("res://")])
	if script == "rookframe_authoring.py" and job in ["check", "build"]:
		args.append_array(["--godot", OS.get_executable_path()])
	_job = job
	model.busy = true
	refresh()
	%Operation.title = {"init": "Create Package", "check": "Check Package", "build": "Build Package", "sdk": "Download SDK", "ui-kit": "Download UI Kit"}[job]
	%Results.text = "Working…"
	_place_operation()
	%Operation.popup_centered_clamped(Vector2i(680, 400) * EditorInterface.get_editor_scale(), 0.85)
	_worker = Thread.new()
	var started := _worker.start(func():
		var output: Array = []
		var code := OS.execute(python, args, output, true)
		return {"code": code, "output": "\n".join(output)})
	if started != OK:
		_worker = null
		model.busy = false
		_problem("Could not start the Package operation: " + error_string(started))


func _process(_delta: float) -> void:
	if _worker == null or _worker.is_alive(): return
	var result: Dictionary = _worker.wait_to_finish()
	_worker = null
	model.busy = false
	print(result.output)
	%Results.clear()
	%Results.append_text(("Completed.\n\n" if result.code == 0 else "Needs attention.\n\n"))
	# Native editor links are actionable without a second resource browser.
	var regex := RegEx.new()
	regex.compile("res://[^\\s\\\"'<>]+\\.(?:tscn|tres|gd|glb|png|jpg)")
	var cursor := 0
	for match_result in regex.search_all(result.output):
		%Results.add_text(result.output.substr(cursor, match_result.get_start() - cursor))
		%Results.push_meta(match_result.get_string())
		%Results.add_text(match_result.get_string())
		%Results.pop()
		cursor = match_result.get_end()
	%Results.add_text(result.output.substr(cursor))
	if result.code == 0:
		model.reload()
		if _job == "init":
			%Setup.hide()
			%Operation.hide()
			_after_scan = model.root_path() + ("content/" if _kind == 0 else "logic/implementation.gd")
			show_guide()
			package_changed.emit(PackedStringArray(["res://rookframe.json"]))
		EditorInterface.get_resource_filesystem().scan()
	refresh()


func _problem(message: String) -> void:
	%Results.text = message
	%Operation.title = "Package needs attention"
	_place_operation()
	%Operation.popup_centered_clamped(Vector2i(680, 400) * EditorInterface.get_editor_scale(), 0.85)
	refresh()


func _place_operation() -> void:
	var parent: Node = self
	for modal in [%Libraries, %BuildDialog, %Setup, %Settings, %CreateUI]:
		if modal.visible: parent = modal
	if %Operation.get_parent() != parent: %Operation.reparent(parent)


func _open_source(path: String) -> void:
	if not ResourceLoader.exists(path): return
	var resource := ResourceLoader.load(path)
	if resource is PackedScene: EditorInterface.open_scene_from_path(path)
	else: EditorInterface.edit_resource(resource)


func _exit_tree() -> void:
	if _worker != null:
		_worker.wait_to_finish()
		_worker = null
