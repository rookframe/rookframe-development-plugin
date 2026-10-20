@tool
extends VBoxContainer

signal configuration_saved

const CONFIG := "res://.rookframe/development.json"
var _prepared := false
var _worker: Thread
var _pending_result: Dictionary = {}
var _initial_args := ""
var _initial_embedding := false


func _ready() -> void:
	_initial_args = ProjectSettings.get_setting("editor/run/main_run_args", "")
	var settings := EditorInterface.get_editor_settings()
	_initial_embedding = settings.get_project_metadata("game_view", "embed_on_play", false) and not settings.get_project_metadata("game_view", "make_floating_on_play", true)
	%Browse.pressed.connect(func(): %RuntimePicker.popup_centered_ratio(0.7))
	%RuntimePicker.file_selected.connect(_select_application)
	%Runtime.text_changed.connect(func(path: String): %Runtime.tooltip_text = path)
	%Prepare.pressed.connect(_prepare)
	%Configure.pressed.connect(show_configuration)
	%WorldSettings.close_requested.connect(%WorldSettings.hide)
	%RuntimePicker.reparent(%WorldSettings)
	%Restart.confirmed.connect(func(): EditorInterface.restart_editor(true))
	refresh_package()
	%UsePackage.toggled.connect(func(_enabled: bool): _prepared = false; refresh_package())
	if FileAccess.file_exists(CONFIG):
		%Configure.text = "Configure World…"
		var config = JSON.parse_string(FileAccess.get_file_as_string(CONFIG))
		if config is Dictionary:
			%UsePackage.button_pressed = config.get("developPackage") != null
			_select_application(config.get("rookframe", ""))
			%WorldName.text = config.get("worldName", "Package development")
			%WorldSummary.text = %WorldName.text
			%SystemManifest.text = config.get("systemManifest", "") if config.get("systemManifest") != null else ""
			%Packages.text = "\n".join(config.get("packageManifests", []))
			if DisplayServer.get_name() != "headless":
				_prepare.call_deferred()
	if %Runtime.text.is_empty():
		_select_application(_installed_rookframe())
	for field in [%Runtime, %WorldName, %SystemManifest]:
		field.text_changed.connect(func(_value: String): _prepared = false)
	%Packages.text_changed.connect(func(): _prepared = false)


func show_configuration() -> void:
	refresh_package()
	%WorldSettings.popup_centered_clamped(Vector2i(620, 570) * EditorInterface.get_editor_scale(), 0.85)


func refresh_package() -> void:
	var exists := FileAccess.file_exists("res://rookframe.json")
	var previously_disabled: bool = %UsePackage.disabled
	%UsePackage.disabled = not exists
	if not exists:
		%UsePackage.button_pressed = false
	elif previously_disabled or not FileAccess.file_exists(CONFIG):
		%UsePackage.button_pressed = true
	%PackageStatus.text = "Changes to the selected Package reload while the World runs." if exists else "No Package in this project. Choose a published System below to run a World."
	var manifest: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://rookframe.json")) if exists else null
	var own_system: bool = manifest is Dictionary and manifest.get("kind") == "system-extension" and %UsePackage.button_pressed
	%SystemManifest.visible = not own_system
	%SystemLabel.visible = not own_system


func _select_application(path: String) -> void:
	%Runtime.text = path
	%Runtime.tooltip_text = path
	%Runtime.caret_column = path.length()
	_prepared = false


func can_run() -> bool:
	if not FileAccess.file_exists(CONFIG):
		return true
	if not _prepared:
		push_error("Rookframe: prepare the development World in the Rookframe dock before playing.")
	if _prepared and %UsePackage.button_pressed and not ResourceLoader.exists("res://rookframe/development/package.rookframe-dev"):
		push_error("Rookframe: wait for Godot to finish importing the development declarations before playing.")
		return false
	return _prepared


func _prepare() -> void:
	if _worker != null or not _pending_result.is_empty() or EditorInterface.is_playing_scene():
		%Status.text = "Stop the development session before changing its launch settings. Saves to Package code and resources reload during the session."
		return
	_prepared = false
	if %WorldName.text.strip_edges().is_empty():
		%Status.text = "Enter a Development World name."
		return
	var manifest: Variant = null
	if %UsePackage.button_pressed:
		manifest = JSON.parse_string(FileAccess.get_file_as_string("res://rookframe.json"))
		if not manifest is Dictionary or not manifest.has("id"):
			%Status.text = "The selected Editable Package needs a valid rookframe.json Manifest."
			return
	var links: Array[String] = []
	for line in %Packages.text.split("\n", false):
		links.append(line.strip_edges())
	var system: String = %SystemManifest.text.strip_edges()
	if manifest is Dictionary and manifest.get("kind") == "system-extension":
		system = ""
	elif system.is_empty():
		%Status.text = "Enter a published System Manifest URL, or select an editable System Extension."
		return
	var config := {"schema": "rookframe-development-v1", "worldName": %WorldName.text.strip_edges(),
		"systemManifest": null if system.is_empty() else system, "packageManifests": links,
		"developPackage": manifest.id if manifest is Dictionary else null,
		"sourceManifest": "res://rookframe.json" if manifest is Dictionary else null,
		"rookframe": %Runtime.text.strip_edges()}
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://.rookframe"))
	var file := FileAccess.open(CONFIG, FileAccess.WRITE)
	if file == null:
		%Status.text = "Cannot save development settings: " + error_string(FileAccess.get_open_error())
		return
	file.store_string(JSON.stringify(config, "\t") + "\n")
	file.close()
	%WorldSummary.text = config.worldName
	%Configure.text = "Configure World…"
	%WorldSettings.hide()
	configuration_saved.emit()
	%Status.text = "Preparing the project with your installed Rookframe…"
	_set_preparing(true)
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var args := PackedStringArray([
		"-X", "utf8",
		ProjectSettings.globalize_path("res://addons/rookframe_development/rookframe_development.py"),
		"--project", ProjectSettings.globalize_path("res://"), "--rookframe", config.rookframe,
		"--godot", OS.get_executable_path(), "--architecture", Engine.get_architecture_name()])
	var worker := Thread.new()
	var started := worker.start(func():
		var output: Array = []
		var status := OS.execute(python, args, output, true)
		return {"status": status, "output": "\n".join(output)})
	if started != OK:
		_set_preparing(false)
		%Status.text = "Cannot prepare the project: " + error_string(started)
		return
	_worker = worker


func _process(_delta: float) -> void:
	%ConfigStatus.text = %Status.text
	if _worker != null:
		if _worker.is_alive():
			return
		_pending_result = _worker.wait_to_finish()
		_worker = null
	if _pending_result.is_empty():
		return
	var result := _pending_result
	_pending_result = {}
	_set_preparing(false)
	if result.status != 0:
		%Status.text = "Preparation failed. Review the error and configure the World."
		%PreparationError.dialog_text = result.output
		%PreparationError.popup_centered_clamped(Vector2i(650, 400) * EditorInterface.get_editor_scale(), 0.85)
		push_error(result.output)
		return
	var settings := EditorInterface.get_editor_settings()
	settings.set_project_metadata("game_view", "embed_on_play", true)
	settings.set_project_metadata("game_view", "make_floating_on_play", false)
	var prepared = JSON.parse_string(result.output)
	if not prepared is Dictionary or not prepared.has("runArguments"):
		%Status.text = "Rookframe preparation returned an invalid result."
		return
	var run_args: String = prepared.runArguments
	ProjectSettings.set_setting("editor/run/main_run_args", run_args)
	if str(ProjectSettings.get_setting("application/run/main_scene", "")) in ["", "res://addons/rookframe_sdk/development_entry.tscn"]:
		ProjectSettings.set_setting("application/run/main_scene", "res://addons/rookframe_development/development_entry.tscn")
	var saved := ProjectSettings.save()
	if saved != OK:
		%Status.text = "Cannot save project launch settings: " + error_string(saved)
		return
	if _initial_args != run_args or not _initial_embedding:
		%Status.text = "Project prepared. Reopen this editor once to activate Godot's launch and Game tab settings."
		%Restart.popup_centered()
		return
	_prepared = true
	%Status.text = "Ready. Use Play (F5)."
	print(result.output)


func _run() -> void:
	if EditorInterface.is_playing_scene():
		%Status.text = "The development World is already running. Save your edits to update it."
	elif can_run() and _prepared:
		EditorInterface.get_script_editor().save_all_scripts()
		EditorInterface.play_main_scene()


func _set_preparing(active: bool) -> void:
	%Prepare.disabled = active
	%Browse.disabled = active
	%Configure.disabled = active
	%UsePackage.disabled = active or not FileAccess.file_exists("res://rookframe.json")
	for field in [%Runtime, %WorldName, %SystemManifest, %Packages]:
		field.editable = not active


func _exit_tree() -> void:
	if _worker != null:
		# Godot can reparent the dock while restoring the editor layout. Retain
		# completion for the next frame instead of consuming the same Thread twice.
		_pending_result = _worker.wait_to_finish()
		_worker = null

func _installed_rookframe() -> String:
	var candidates := PackedStringArray([OS.get_environment("ROOKFRAME_APP")])
	if OS.get_name() == "Windows":
		candidates.append(OS.get_environment("ProgramFiles").path_join("Rookframe/Rookframe.exe"))
	elif OS.get_name() == "macOS":
		candidates.append("/Applications/Rookframe.app/Contents/MacOS/Rookframe")
	else:
		candidates.append("/usr/bin/rookframe")
	for path in candidates:
		if not path.is_empty() and FileAccess.file_exists(path):
			return path
	return ""
