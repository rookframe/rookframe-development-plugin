@tool
extends RefCounted

const DECLARATIONS := "res://rookframe/development/package.rookframe-dev"
const CONFIG := "res://.rookframe/development.json"
var _paths: Dictionary = {}
var _queued := false
var _stopped := false
var _worker: Thread
var _refresh_again := false
var _ready := false
var _last_error := ""


func stop() -> void:
	_stopped = true
	if _worker != null:
		_worker.wait_to_finish()
		_worker = null


func can_run() -> bool:
	if _selected_package() == "":
		return true
	if _ready and _worker == null and not _queued:
		return true
	push_error("Rookframe: wait for Editable Package preparation or correct the reported error before playing.")
	return false


func source_saved(resource: Resource) -> void:
	sources_changed(PackedStringArray([resource.resource_path]))


func sources_changed(paths: PackedStringArray) -> void:
	var selected := _selected_package()
	if selected.is_empty():
		return
	var prefix := "res://rookframe/packages/" + selected + "/"
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
		EditorInterface.get_base_control().get_tree().process_frame.connect(_prepare, CONNECT_ONE_SHOT)


func _selected_package() -> String:
	if not FileAccess.file_exists(CONFIG):
		return ""
	var config = JSON.parse_string(FileAccess.get_file_as_string(CONFIG))
	var selected: Variant = config.get("developPackage") if config is Dictionary else null
	return selected if selected is String else ""


func _prepare() -> void:
	_queued = false
	var selected := _selected_package()
	if _stopped or selected.is_empty():
		return
	var filesystem := EditorInterface.get_resource_filesystem()
	if filesystem.is_importing() or filesystem.is_scanning():
		# The next native filesystem/import notification schedules another attempt.
		return
	var paths := _paths.keys()
	var temporary := "res://.rookframe-development/changes-%s-%s.json" % [OS.get_process_id(), Time.get_ticks_usec()]
	DirAccess.make_dir_recursive_absolute(temporary.get_base_dir())
	var file := FileAccess.open(temporary, FileAccess.WRITE)
	if file == null:
		_ready = false
		push_error("Rookframe: cannot record Editable Package changes: " + error_string(FileAccess.get_open_error()))
		return
	file.store_string(JSON.stringify(paths))
	file.close()
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var changes := ProjectSettings.globalize_path(temporary)
	var args := PackedStringArray([
		"-X", "utf8", ProjectSettings.globalize_path("res://addons/rookframe_development/rookframe_editable_preparation.py"),
		"--project", ProjectSettings.globalize_path("res://"), "--godot", OS.get_executable_path(), "--changes", changes])
	var renderer := RenderingServer.get_current_rendering_method()
	if renderer in ["mobile", "forward_plus", "gl_compatibility"]:
		args.append_array(PackedStringArray(["--renderer", renderer]))
	var worker := Thread.new()
	var started := worker.start(func():
		var output: Array = []
		var status := OS.execute(python, args, output, true)
		DirAccess.remove_absolute(changes)
		return {"status": status, "output": "\n".join(output), "paths": paths, "selected": selected})
	if started != OK:
		DirAccess.remove_absolute(changes)
		_ready = false
		push_error("Rookframe: cannot prepare the Editable Package: " + error_string(started))
		return
	_paths.clear()
	_worker = worker


func poll() -> void:
	if _worker == null or _worker.is_alive():
		return
	var result: Dictionary = _worker.wait_to_finish()
	_worker = null
	if result.selected == _selected_package():
		var prepared: Variant = JSON.parse_string(result.output) if result.status == 0 else null
		var lods: Dictionary = prepared.get("lodPreparation", {}) if prepared is Dictionary else {}
		_ready = prepared is Dictionary and prepared.get("packageData") is Dictionary and lods.get("status") != "failed"
		if prepared is Dictionary and (prepared.get("updated", false) or not ResourceLoader.exists(DECLARATIONS)):
			# A scan, rather than synchronous reimport, is safe during native scene reload.
			EditorInterface.get_resource_filesystem().scan()
		if _ready:
			_last_error = ""
			if prepared.get("updated", false):
				for fallback: Dictionary in prepared.get("scenery", {}).get("fallbacks", []):
					print("Rookframe %s: %s variant %s. %s" % [fallback.code, fallback.content, fallback.variant, fallback.reason])
			if prepared.get("lodPreparationUpdated", false) and lods.get("status") == "source_fallback":
				push_warning("Rookframe: Editable LOD preparation was incomplete. The World will use original artwork for affected variants.")
		else:
			var error: String = prepared.get("dataError", result.output) if prepared is Dictionary else result.output
			if lods.get("status") == "failed":
				error = lods.get("diagnostic", "Rookframe could not prepare Editable Package LODs.")
			if not prepared is Dictionary or not prepared.get("updated", false):
				for path: String in result.paths:
					_paths[path] = true
			if error.begins_with("SCENERY.STALE:"):
				_refresh_again = true
			elif _last_error != error:
				_last_error = error
				push_error("Rookframe: Editable Package preparation failed. " + _last_error)
	if _refresh_again:
		_refresh_again = false
		queue_update()
