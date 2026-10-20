@tool
extends EditorPlugin

var _development: Control
var _dock: EditorDock
var _importer: EditorImportPlugin
var _declarations: RefCounted
var _active := false

func _enter_tree() -> void:
	if OS.get_environment("ROOKFRAME_AUTHOR_COPY") == "1":
		return
	if "res://addons/rookframe_sdk/plugin.cfg" in ProjectSettings.get_setting("editor_plugins/enabled", PackedStringArray()):
		push_error("Disable the legacy Rookframe Package SDK editor plugin, then enable Rookframe Development. The SDK library itself is optional.")
		return
	_active = true
	_importer = preload("res://addons/rookframe_development/development_importer.gd").new()
	add_import_plugin(_importer)
	_declarations = preload("res://addons/rookframe_development/development_declarations.gd").new()
	resource_saved.connect(_declarations.source_saved)
	scene_saved.connect(_scene_saved)
	var filesystem := EditorInterface.get_resource_filesystem()
	filesystem.filesystem_changed.connect(_declarations.queue_update)
	filesystem.resources_reimported.connect(_declarations.sources_changed)
	filesystem.resources_reload.connect(_declarations.sources_changed)
	_declarations.queue_update()
	add_tool_menu_item("Rookframe: Check Package", _check)
	add_tool_menu_item("Rookframe: Build Package", _build_package)
	add_tool_menu_item("Rookframe: Install latest SDK", func(): _install_library("sdk"))
	add_tool_menu_item("Rookframe: Install latest UI Kit", func(): _install_library("ui-kit"))
	if FileAccess.file_exists("res://.rookframe/development.json"):
		# Apply project metadata during editor startup, before GameView reads it.
		var settings := EditorInterface.get_editor_settings()
		settings.set_project_metadata("game_view", "embed_on_play", true)
		settings.set_project_metadata("game_view", "make_floating_on_play", false)
	_dock = preload("res://addons/rookframe_development/development.tscn").instantiate()
	_development = _dock.get_node("Tabs/Development/Content")
	_development.configuration_saved.connect(_declarations.queue_update)
	_dock.get_node("Tabs/Package/Package").package_changed.connect(_declarations.sources_changed)
	_dock.get_node("Tabs/Package/Package").package_changed.connect(func(_paths: PackedStringArray): _development.refresh_package())
	add_dock(_dock)
	add_tool_menu_item("Rookframe: Development World", _dock.make_visible)


func _exit_tree() -> void:
	if not _active:
		return
	_active = false
	if _declarations != null:
		_declarations.stop()
		resource_saved.disconnect(_declarations.source_saved)
		scene_saved.disconnect(_scene_saved)
		var filesystem := EditorInterface.get_resource_filesystem()
		filesystem.filesystem_changed.disconnect(_declarations.queue_update)
		filesystem.resources_reimported.disconnect(_declarations.sources_changed)
		filesystem.resources_reload.disconnect(_declarations.sources_changed)
		_declarations = null
	if _importer != null:
		remove_import_plugin(_importer)
		_importer = null
	remove_tool_menu_item("Rookframe: Check Package")
	remove_tool_menu_item("Rookframe: Build Package")
	remove_tool_menu_item("Rookframe: Install latest SDK")
	remove_tool_menu_item("Rookframe: Install latest UI Kit")
	if is_instance_valid(_dock):
		remove_tool_menu_item("Rookframe: Development World")
		remove_dock(_dock)
		_dock.queue_free()


func _build() -> bool:
	return not is_instance_valid(_development) or _development.can_run()


func _scene_saved(path: String) -> void:
	_declarations.sources_changed(PackedStringArray([path]))


func _check() -> void:
	_run("check")


func _build_package() -> void:
	_run("build")


func _install_library(library: String) -> void:
	_dock.get_node("Tabs/Package/Package").install_library(library)
	_dock.make_visible()
	_dock.get_node("Tabs").current_tab = 1


func _run(command: String) -> void:
	# The CLI executes author tools only inside disposable project copies.
	# ROOKFRAME_PYTHON may name Python on installations without python3 on PATH.
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var output: Array = []
	var status := OS.execute(python, PackedStringArray([
		"-X", "utf8",
		ProjectSettings.globalize_path("res://addons/rookframe_development/rookframe_authoring.py"),
		command, "--project", ProjectSettings.globalize_path("res://"),
		"--godot", OS.get_executable_path(),
	]), output, true)
	for line in output:
		print(line)
	if status != 0:
		push_error("Rookframe " + command + " failed. See the Output panel for the corrective diagnostic.")
