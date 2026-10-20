@tool
extends VBoxContainer

signal package_changed(paths: PackedStringArray)

const Selection = preload("res://addons/rookframe_development/content_selection.gd")
const Requirements = preload("res://addons/rookframe_development/content_requirements.gd")
const API_DOCS := "https://github.com/rookframe/rookframe-sdk/blob/v0.36.0-rc.4/API.md"
var model: RefCounted
var _worker: Thread
var _job := ""
var _refreshing := false
var _selection: Resource
var _logs := ""
var _pending_result: Dictionary = {}
var _selected_content_id := ""
var _source_revision := 0
var _job_source_revision := 0


func configure(source: RefCounted) -> void:
	model = source
	model.changed.connect(refresh)
	model.changed.connect(source_changed)
	model.problem.connect(func(message: String): %Status.text = message)
	model.saved.connect(func(paths: PackedStringArray):
		package_changed.emit(paths)
		EditorInterface.get_resource_filesystem().scan())
	%Dialogs.configure(model, _run_tool)
	refresh()


func _ready() -> void:
	%Create.pressed.connect(func(): %Dialogs.open_setup())
	%Settings.pressed.connect(func(): %Dialogs.open_settings())
	%Folder.pressed.connect(func(): EditorInterface.get_file_system_dock().navigate_to_path(model.root_path()))
	%Search.text_changed.connect(func(_text: String): _content_list())
	for item in ["All Content", "Miniatures", "Props", "Wall Styles", "Surface Finishes", "Themes", "System Content"]:
		%Filter.add_item(item)
	%Filter.item_selected.connect(func(_index: int): _content_list())
	%ClearFilter.pressed.connect(func():
		%Search.clear()
		%Filter.select(0)
		_content_list())
	%Contents.item_selected.connect(_inspect)
	%Contents.item_activated.connect(_open_content)
	%OpenContent.pressed.connect(_open_content)
	%AddExisting.pressed.connect(func():
		%ContentPicker.current_dir = model.root_path()
		%ContentPicker.popup_centered_ratio(0.65))
	%ContentPicker.file_selected.connect(func(path: String):
		if not model.owns(path):
			%Status.text = "Choose a saved scene or Resource inside this Package's folder."
			return
		var resource := ResourceLoader.load(path)
		if resource != null:
			EditorInterface.edit_resource(resource))
	%Sources.item_selected.connect(_source_selected)
	%Sources.item_activated.connect(_open_source)
	%OpenSource.pressed.connect(_open_source)
	%EditPresentation.pressed.connect(func(): %Dialogs.open_presentation(str(_source_data().get("id", ""))))
	%AddPresentation.pressed.connect(func(): %Dialogs.open_presentation())
	%AddBehavior.pressed.connect(func(): %Dialogs.open_behavior())
	%Libraries.toggled.connect(func(value: bool): %LibraryRows.visible = value)
	%ApiDocs.pressed.connect(func(): OS.shell_open(API_DOCS))
	%ActorDocs.pressed.connect(func(): OS.shell_open(API_DOCS + "#actors-and-system-records"))
	%InstallSdk.pressed.connect(func(): install_library("sdk"))
	%InstallUi.pressed.connect(func(): install_library("ui-kit"))
	%Save.pressed.connect(func(): model.save())
	%CheckPackage.pressed.connect(func(): run_command("check"))
	%BuildPackage.pressed.connect(func(): run_command("build"))
	%Release.pressed.connect(func(): %Dialogs.open_release())
	%CopyDraft.pressed.connect(func(): DisplayServer.clipboard_set(JSON.stringify(model.manifest, "\t")))
	%Reload.pressed.connect(func(): %ConfirmReload.popup_centered())
	%ConfirmReload.confirmed.connect(func(): model.reload(true))
	%Logs.pressed.connect(func():
		%LogText.text = _logs
		%LogDialog.popup_centered_ratio(0.75))
	%ShowBuild.pressed.connect(func(): OS.shell_show_in_file_manager(%BuildPath.text))
	%CopyBuild.pressed.connect(func(): DisplayServer.clipboard_set(%BuildPath.text))
	%ProblemSources.item_activated.connect(func(index: int): _open_path(str(%ProblemSources.get_item_metadata(index))))


func refresh() -> void:
	if model == null:
		return
	_refreshing = true
	var exists: bool = not model.manifest.is_empty()
	%Empty.visible = not exists
	%PackageBody.visible = exists
	%Conflict.visible = not model.conflict.is_empty()
	%ConflictText.text = model.conflict
	%CopyDraft.visible = exists and model.dirty()
	if exists:
		%PackageTitle.text = str(model.manifest.name)
		%PackageRole.text = "System Extension" if model.manifest.kind == "system-extension" else (
			"Extension" if model.manifest.has("implementation") or model.manifest.has("presentations") else "Content pack")
		%Dirty.text = "Unsaved Package changes" if model.dirty() else "Package saved"
		%Save.disabled = not model.dirty() or not model.conflict.is_empty() or model.busy
		var scripted: bool = model.manifest.has("implementation") or model.manifest.has("presentations") or model.manifest.kind == "system-extension"
		%SourceArea.visible = true
		%Sources.visible = scripted
		%SourceActions.visible = scripted
		%SourceHelp.text = "Edit GDScript and Control scenes in Godot. Presentations select UI for desktop, tablet or phone." if scripted else "Add behavior or UI only if your Package needs it."
		%SystemHelp.visible = model.manifest.kind == "system-extension"
		%ActorDocs.visible = %SystemHelp.visible
		%ApiDocs.visible = scripted
		%ApiDocs.text = "API " + str(model.manifest.sdk.edition) + " reference ↗"
		%ApiDocs.tooltip_text = "Domain operations and their revision requirements. This Package requires revision " + str(model.manifest.sdk.minimumRevision) + " or later. The SDK is optional."
		%AddBehavior.visible = not model.manifest.has("implementation")
		_content_list()
		_source_list()
		_library_status()
	for button: Button in [%CheckPackage, %BuildPackage, %InstallSdk, %InstallUi, %Settings, %Release, %AddBehavior, %AddPresentation]:
		button.disabled = model.busy or not model.conflict.is_empty()
	%Create.disabled = model.busy or not model.conflict.is_empty()
	_refreshing = false


func source_changed() -> void:
	_source_revision += 1
	if %Status.text.begins_with("Saved source checked"):
		%Status.text = "Sources changed since the last check. Check Package again before building."


func _content_list() -> void:
	if model == null:
		return
	var was_refreshing := _refreshing
	_refreshing = true
	var selected := _selected_content_id
	var found := false
	var names: Dictionary = {}
	for entry: Dictionary in model.entries():
		names[str(entry.displayName)] = int(names.get(str(entry.displayName), 0)) + 1
	%Contents.clear()
	var root: TreeItem = %Contents.create_item()
	var count := 0
	for entry: Dictionary in model.entries():
		var type := str(entry.type)
		var index := Requirements.TYPES.find(type)
		if index < 0:
			index = 5
		if %Filter.selected > 0 and index != %Filter.selected - 1:
			continue
		var query: String = %Search.text.strip_edges().to_lower()
		if not query.is_empty() and not (str(entry.displayName) + " " + str(entry.payload) + " " + Requirements.title(type)).to_lower().contains(query):
			continue
		var item: TreeItem = %Contents.create_item(root)
		item.set_text(0, str(entry.displayName) + ("\n" + str(entry.payload) if names[str(entry.displayName)] > 1 else ""))
		item.set_text(1, Requirements.title(type))
		item.set_tooltip_text(0, str(entry.payload))
		item.set_metadata(0, str(entry.id))
		if not ResourceLoader.exists(model.root_path() + str(entry.payload)):
			item.set_text(1, "Missing file")
			item.set_tooltip_text(1, "Select this item, then Locate resource in the Inspector.")
		if entry.id == selected:
			found = true
			item.select(0)
		count += 1
	%ContentHelp.text = "Create scenes in content/ and keep source assets in assets/. Select your scene root or Resource in the Inspector to make it available in Rookframe." if model.entries().is_empty() else (
		"No Content matches this filter." if count == 0 else "Select Content to edit its properties in the Inspector. Double-click to open its source.")
	%OpenContent.disabled = %Contents.get_selected() == null
	%HiddenSelection.visible = not selected.is_empty() and not found and not model.entry_by_id(selected).is_empty()
	%ClearFilter.visible = not %Search.text.is_empty() or %Filter.selected > 0
	_refreshing = was_refreshing


func _inspect() -> void:
	if _refreshing or %Contents.get_selected() == null:
		return
	_selection = Selection.new()
	_selected_content_id = str(%Contents.get_selected().get_metadata(0))
	_selection.content_id = _selected_content_id
	EditorInterface.inspect_object(_selection)
	%OpenContent.disabled = false


func _open_content() -> void:
	if %Contents.get_selected() == null:
		return
	var entry: Dictionary = model.entry_by_id(str(%Contents.get_selected().get_metadata(0)))
	_open_path(model.root_path() + str(entry.get("payload", "")))


func _source_list() -> void:
	var previous := _source_data()
	%Sources.clear()
	var root: TreeItem = %Sources.create_item()
	if model.manifest.has("implementation"):
		var item: TreeItem = %Sources.create_item(root)
		item.set_text(0, "Behavior")
		item.set_tooltip_text(0, str(model.manifest.implementation.entryPoint))
		item.set_metadata(0, model.manifest.implementation)
		if previous == model.manifest.implementation:
			item.select(0)
	for presentation: Dictionary in model.manifest.get("presentations", []):
		var item: TreeItem = %Sources.create_item(root)
		item.set_text(0, str(presentation.entryPoint).get_file().get_basename().capitalize() + " · " + ", ".join(presentation.experiences))
		item.set_tooltip_text(0, str(presentation.entryPoint) + "\nIdentity: " + str(presentation.id))
		item.set_metadata(0, presentation)
		if previous.get("id", "") == presentation.id:
			item.select(0)
	_source_selected()


func _source_data() -> Dictionary:
	return %Sources.get_selected().get_metadata(0) if %Sources.get_selected() != null else {}


func _source_selected() -> void:
	%OpenSource.disabled = _source_data().is_empty()
	%EditPresentation.disabled = not _source_data().has("id") or model.busy


func _open_source() -> void:
	if not _source_data().is_empty():
		_open_path(model.root_path() + str(_source_data().entryPoint))


func _open_path(path: String) -> void:
	if not ResourceLoader.exists(path):
		%Status.text = "Missing source: " + path
		return
	if ResourceLoader.load(path) is PackedScene:
		EditorInterface.open_scene_from_path(path)
	else:
		EditorInterface.edit_resource(ResourceLoader.load(path))


func _library_status() -> void:
	var inventory: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://.rookframe/optional-libraries.json")) if FileAccess.file_exists("res://.rookframe/optional-libraries.json") else {}
	for library: String in ["sdk", "ui-kit"]:
		var installed: Dictionary = inventory.get(library, {}) if inventory is Dictionary else {}
		var label: Label = %SdkVersion if library == "sdk" else %UiVersion
		label.text = ("SDK: " if library == "sdk" else "UI Kit: ") + (str(installed.get("version", "Installed")) if not installed.is_empty() else (
			"Installed outside plugin" if DirAccess.dir_exists_absolute("res://rookframe/" + ("ui" if library == "ui-kit" else "sdk")) else "Not installed"))


func install_library(library: String) -> void:
	_run_tool("install " + library, PackedStringArray(["--library", library]), "optional_libraries.py")


func run_command(command: String) -> void:
	if model.busy:
		return
	EditorInterface.get_script_editor().save_all_scripts()
	if not EditorInterface.get_unsaved_scenes().is_empty() and EditorInterface.get_edited_scene_root() != null and EditorInterface.save_scene() != OK:
		%Status.text = "Save the open scene before checking or building."
		return
	var unsaved := EditorInterface.get_unsaved_scenes()
	if not unsaved.is_empty():
		%Status.text = "Save the other edited scenes before checking or building:\n" + "\n".join(unsaved)
		return
	if model.save():
		_run_tool(command, PackedStringArray([command, "--godot", OS.get_executable_path()]))


func _run_tool(job: String, arguments: PackedStringArray, script := "rookframe_authoring.py") -> void:
	if model.busy:
		%Status.text = "Wait for the current Package operation to finish."
		return
	model.busy = true
	_job = job
	_job_source_revision = _source_revision
	%ProblemSources.clear()
	%ProblemSources.hide()
	%Status.text = job.capitalize() + "… You can keep editing scenes."
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var args := PackedStringArray(["-X", "utf8", ProjectSettings.globalize_path("res://addons/rookframe_development/" + script)])
	args.append_array(arguments)
	args.append_array(PackedStringArray(["--project", ProjectSettings.globalize_path("res://")]))
	_worker = Thread.new()
	var error := _worker.start(func():
		var output: Array = []
		var status := OS.execute(python, args, output, true)
		return {"status": status, "output": "\n".join(output)})
	if error != OK:
		model.busy = false
		_worker = null
		%Status.text = "Cannot start operation: " + error_string(error)
	refresh()
	get_tree().call_group("rookframe_content_editors", "refresh")


func _process(_delta: float) -> void:
	if _worker != null:
		if _worker.is_alive():
			return
		_pending_result = _worker.wait_to_finish()
		_worker = null
	if _pending_result.is_empty():
		return
	var result := _pending_result
	_pending_result = {}
	model.busy = false
	_logs = str(result.output)
	print(_logs)
	%Logs.visible = true
	%Status.text = _job.capitalize() + (" finished." if result.status == 0 else " failed.\n" + _diagnostic(_logs))
	if result.status != 0:
		var expression := RegEx.new()
		expression.compile("res://[^\\s\\\"'<>]+\\.(gd|tscn|tres|res|scn)")
		var paths: Array[String] = []
		for match: RegExMatch in expression.search_all(_logs):
			var path := match.get_string()
			if ResourceLoader.exists(path) and path not in paths:
				paths.append(path)
				var index: int = %ProblemSources.add_item(path.get_file())
				%ProblemSources.set_item_metadata(index, path)
				%ProblemSources.set_item_tooltip(index, "Double-click to open " + path)
		%ProblemSources.visible = not paths.is_empty()
	if result.status == 0:
		var changed_during_check := _job_source_revision != _source_revision
		model.reload()
		var parsed: Variant = JSON.parse_string(_logs) if _job == "build" else {}
		if _job == "build" and parsed is Dictionary and parsed.has("archive"):
			%BuildResult.show()
			%BuildPath.text = parsed.archive
		if _job in ["init", "media"]:
			package_changed.emit(PackedStringArray(["res://rookframe.json"]))
		if _job == "check":
			%Status.text = "Sources changed during the check. Save and check again before building." if changed_during_check else "Saved source checked by Rookframe. Check again after editing; builds also verify exported content."
		EditorInterface.get_resource_filesystem().scan()
	%Dialogs.operation_finished(result.status == 0)
	refresh()
	get_tree().call_group("rookframe_content_editors", "refresh")


func _diagnostic(output: String) -> String:
	for raw: String in output.split("\n", false):
		var line := raw.strip_edges()
		var start := line.find("{")
		if start >= 0:
			var value: Variant = JSON.parse_string(line.substr(start))
			if value is Dictionary and value.has("diagnostic"):
				return str(value.diagnostic)
		if line.contains(":") and not line.begins_with("Godot Engine") and not line.begins_with("at:"):
			return line
	return output.strip_edges().left(600) + "\nOpen operation details for the full output."


func _exit_tree() -> void:
	if _worker != null:
		_pending_result = _worker.wait_to_finish()
		_worker = null
