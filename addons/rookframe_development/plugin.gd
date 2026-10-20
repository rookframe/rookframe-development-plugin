@tool
extends EditorPlugin

var _development: Control
var _dock: EditorDock
var _importer: EditorImportPlugin
var _declarations: RefCounted
var _active := false
var _package: RefCounted
var _content_dock: EditorDock
var _authoring: Control
var _launch_manifest: Dictionary = {}

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
	resource_saved.connect(_source_saved)
	scene_saved.connect(_scene_saved)
	var filesystem := EditorInterface.get_resource_filesystem()
	filesystem.filesystem_changed.connect(_declarations.queue_update)
	filesystem.resources_reimported.connect(_declarations.sources_changed)
	filesystem.resources_reload.connect(_declarations.sources_changed)
	_declarations.queue_update()
	_package = preload("res://addons/rookframe_development/package_model.gd").new()
	_package.undo = get_undo_redo()
	_package.reload()
	_content_dock = preload("res://addons/rookframe_development/content_dock.tscn").instantiate()
	_content_dock.model = _package
	_content_dock.custom_minimum_size.x = 300.0 * EditorInterface.get_editor_scale()
	add_dock(_content_dock)
	filesystem.filesystem_changed.connect(_package.reload)
	var files := EditorInterface.get_file_system_dock()
	files.files_moved.connect(_package.move_path)
	files.folder_moved.connect(_package.move_path)
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
	_dock.custom_minimum_size.x = 300.0 * EditorInterface.get_editor_scale()
	_development = _dock.get_node("Margin/Body/Development")
	_development.configuration_saved.connect(_declarations.queue_update)
	_dock.get_node("Margin/Body/Package").package_changed.connect(_declarations.sources_changed)
	_dock.get_node("Margin/Body/Package").package_changed.connect(func(_paths: PackedStringArray): _development.refresh_package())
	_dock.get_node("Margin/Body/Package").package_changed.connect(_package_saved)
	add_dock(_dock)
	_authoring = _dock.get_node("Margin/Body/Package")
	_authoring.configure(_package)
	_dock.make_visible.call_deferred()
	add_tool_menu_item("Rookframe: Package settings", _authoring.show_settings)
	add_tool_menu_item("Rookframe: Authoring guide", _authoring.show_guide)
	add_tool_menu_item("Rookframe: Development World", _development.show_configuration)


func _set_window_layout(_configuration: ConfigFile) -> void:
	var settings := EditorInterface.get_editor_settings()
	if is_instance_valid(_dock) and not settings.get_project_metadata("rookframe", "dock_shown", false):
		_dock.make_visible.call_deferred()
		settings.set_project_metadata("rookframe", "dock_shown", true)


func _exit_tree() -> void:
	if not _active:
		return
	_active = false
	if is_instance_valid(_content_dock):
		remove_dock(_content_dock)
		_content_dock.queue_free()
		_content_dock = null
	if _package != null:
		var files := EditorInterface.get_file_system_dock()
		files.files_moved.disconnect(_package.move_path)
		files.folder_moved.disconnect(_package.move_path)
		EditorInterface.get_resource_filesystem().filesystem_changed.disconnect(_package.reload)
	if _declarations != null:
		_declarations.stop()
		resource_saved.disconnect(_declarations.source_saved)
		resource_saved.disconnect(_source_saved)
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
		remove_tool_menu_item("Rookframe: Package settings")
		remove_tool_menu_item("Rookframe: Authoring guide")
		remove_dock(_dock)
		_dock.queue_free()


func _build() -> bool:
	if _package != null:
		_launch_manifest = _package.manifest.duplicate(true)
	return not is_instance_valid(_development) or _development.can_run()


func _scene_saved(path: String) -> void:
	_declarations.sources_changed(PackedStringArray([path]))
	get_tree().call_group_flags(SceneTree.GROUP_CALL_DEFERRED, "rookframe_content_properties", "source_saved")
	_content_dock._queue_refresh()


func _source_saved(_resource: Resource) -> void:
	get_tree().call_group_flags(SceneTree.GROUP_CALL_DEFERRED, "rookframe_content_properties", "source_saved")
	_content_dock._queue_refresh()


func _package_saved(_paths: PackedStringArray) -> void:
	if not EditorInterface.is_playing_scene() or _launch_manifest.is_empty():
		return
	var current: Dictionary = _package.manifest.duplicate(true)
	var launched := _launch_manifest.duplicate(true)
	for key: String in ["content", "presentations"]:
		current.erase(key)
		launched.erase(key)
	if current != launched:
		_development.get_node("%Status").text = "Package details changed. Restart this World to apply them. Content and Presentation edits can reload live."


func _check() -> void:
	_run("check")


func _build_package() -> void:
	_run("build")


func _install_library(library: String) -> void:
	_authoring.install_library(library)
	_dock.make_visible()


func _run(command: String) -> void:
	_apply_changes()
	_authoring.run_command(command)
	_dock.make_visible()


func _apply_changes() -> void:
	if is_inside_tree():
		get_tree().call_group("rookframe_content_properties", "commit_fields")


func _save_external_data() -> void:
	if _package != null and not _package.busy:
		_apply_changes()
		_package.save()


func _get_unsaved_status(_for_scene: String) -> String:
	if _package != null and _package.dirty():
		return "Rookframe Package has unsaved properties."
	return ""
