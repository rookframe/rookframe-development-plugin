@tool
extends RefCounted

const PATH := "res://rookframe/development/package.rookframe-dev"
const SCHEMA := "rookframe-development-declarations-v1"
const CONFIG := "res://.rookframe/development.json"
var _manifest := ""
var _paths: Dictionary = {}
var _queued := false
var _stopped := false
var _generation := 0
var _worker: Thread
var _refresh_again := false
var _package_data: Dictionary = {}
var _data_ready := false
var _data_error := ""


func stop() -> void:
	_stopped = true
	if _worker != null:
		_worker.wait_to_finish()
		_worker = null


func can_run() -> bool:
	if _selected_package() == null:
		return true
	if _data_ready and _worker == null and not _queued:
		return true
	push_error("Rookframe: wait for Editable Package data, or correct the reported data error before playing.")
	return false


func source_saved(resource: Resource) -> void:
	sources_changed(PackedStringArray([resource.resource_path]))


func sources_changed(paths: PackedStringArray) -> void:
	if not _has_editable_package():
		return
	var manifest = JSON.parse_string(FileAccess.get_file_as_string("res://rookframe.json"))
	if not manifest is Dictionary or not manifest.get("id") is String:
		return
	var prefix := "res://rookframe/packages/" + str(manifest.id) + "/"
	for path in paths:
		if path.begins_with(prefix) and not path.begins_with(prefix + "sdk/"):
			_paths[path] = true
	queue_update()


func queue_update() -> void:
	if _worker != null:
		_refresh_again = true
		return
	if not _queued and not _stopped:
		_queued = true
		# Coalesce resource notifications at the next editor frame.
		EditorInterface.get_base_control().get_tree().process_frame.connect(_publish, CONNECT_ONE_SHOT)


func _has_editable_package() -> bool:
	if not FileAccess.file_exists("res://rookframe.json"):
		return false
	if not FileAccess.file_exists(CONFIG):
		return true
	return _selected_package() != null


func _selected_package() -> Variant:
	if not FileAccess.file_exists(CONFIG):
		return null
	var config = JSON.parse_string(FileAccess.get_file_as_string(CONFIG))
	var selected: Variant = config.get("developPackage") if config is Dictionary else null
	return selected if selected is String else null


func _publish() -> void:
	_queued = false
	if _stopped or not _has_editable_package():
		return
	var filesystem := EditorInterface.get_resource_filesystem()
	if filesystem.is_importing() or filesystem.is_scanning():
		# The next native filesystem/import notification schedules another attempt.
		return
	var source := FileAccess.get_file_as_string("res://rookframe.json")
	var parsed := JSON.new()
	if parsed.parse(source) != OK or not parsed.data is Dictionary:
		push_error("Rookframe: correct rookframe.json before updating the running World.")
		return
	var paths := _paths.keys()
	_paths.clear()
	var selected: Variant = _selected_package()
	if selected == null:
		_write_snapshot(source, paths, {})
		return
	_compile_data(source, paths, selected)


func _compile_data(source: String, paths: Array, selected: String) -> void:
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var args := PackedStringArray([
		"-X", "utf8", ProjectSettings.globalize_path("res://addons/rookframe_development/rookframe_package_data.py"),
		"--project", ProjectSettings.globalize_path("res://"), "--godot", OS.get_executable_path()])
	var worker := Thread.new()
	var started := worker.start(func():
		var output: Array = []
		var status := OS.execute(python, args, output, true)
		return {"status": status, "output": "\n".join(output), "manifest": source,
			"paths": paths, "selected": selected})
	if started != OK:
		_data_ready = false
		push_error("Rookframe: cannot prepare Editable Package data: " + error_string(started))
		return
	_worker = worker


func poll() -> void:
	if _worker == null or _worker.is_alive():
		return
	var result: Dictionary = _worker.wait_to_finish()
	_worker = null
	if result.selected == _selected_package():
		var prepared: Variant = JSON.parse_string(result.output) if result.status == 0 else null
		_data_ready = prepared is Dictionary and prepared.get("packageData") is Dictionary
		if _data_ready:
			_data_error = ""
			var paths: Dictionary = {}
			for path in result.paths + prepared.changedPaths:
				paths[path] = true
			_write_snapshot(result.manifest, paths.keys(), prepared.packageData)
		else:
			if _data_error != result.output:
				_data_error = result.output
				push_error("Rookframe: Editable Package data preparation failed. " + _data_error)
			if _package_data.get("packageId") == result.selected:
				# A data error leaves the previous index usable. Continue native
				# script and scene updates while the author corrects the data.
				var paths: Array = result.paths.filter(func(path: String): return path.get_extension().to_lower() != "json")
				_write_snapshot(result.manifest, paths, _package_data)
	if _refresh_again:
		_refresh_again = false
		queue_update()


func _write_snapshot(source: String, paths: Array, package_data: Dictionary) -> void:
	if source == _manifest and paths.is_empty() and package_data == _package_data and FileAccess.file_exists(PATH):
		return
	if FileAccess.file_exists(PATH):
		var previous = JSON.parse_string(FileAccess.get_file_as_string(PATH))
		if not previous is Dictionary or previous.get("schema") != SCHEMA:
			push_error("Rookframe: development declarations path contains an author-owned file: " + PATH)
			return
	paths.sort()
	_generation += 1
	var snapshot := {"schema": SCHEMA, "manifest": source,
		"changedPaths": paths, "generation": _generation}
	if not package_data.is_empty():
		snapshot["packageData"] = package_data
	DirAccess.make_dir_recursive_absolute(PATH.get_base_dir())
	var file := FileAccess.open(PATH, FileAccess.WRITE)
	if file == null:
		push_error("Rookframe: cannot save development declarations: " + error_string(FileAccess.get_open_error()))
		return
	file.store_string(JSON.stringify(snapshot, "\t") + "\n")
	file.close()
	_manifest = source
	_package_data = package_data
	# The native scan schedules imports safely, including after an external scene
	# reload. Synchronous reimport_files can reenter a deferred editor reload.
	EditorInterface.get_resource_filesystem().scan()
