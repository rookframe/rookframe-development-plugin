@tool
extends RefCounted

const PATH := "res://rookframe/development/package.rookframe-dev"
const SCHEMA := "rookframe-development-declarations-v1"
var _manifest := ""
var _paths: Dictionary = {}
var _queued := false
var _stopped := false
var _generation := 0


func stop() -> void:
	_stopped = true


func source_saved(resource: Resource) -> void:
	sources_changed(PackedStringArray([resource.resource_path]))


func sources_changed(paths: PackedStringArray) -> void:
	var manifest = JSON.parse_string(FileAccess.get_file_as_string("res://rookframe.json"))
	if not manifest is Dictionary or not manifest.get("id") is String:
		return
	var prefix := "res://rookframe/packages/" + str(manifest.id) + "/"
	for path in paths:
		if path.begins_with(prefix) and not path.begins_with(prefix + "sdk/"):
			_paths[path] = true
	queue_update()


func queue_update() -> void:
	if not _queued and not _stopped:
		_queued = true
		# Coalesce resource notifications at the next editor frame.
		EditorInterface.get_base_control().get_tree().process_frame.connect(_publish, CONNECT_ONE_SHOT)


func _publish() -> void:
	_queued = false
	if _stopped or not FileAccess.file_exists("res://rookframe.json"):
		return
	var filesystem := EditorInterface.get_resource_filesystem()
	if filesystem.is_importing() or filesystem.is_scanning():
		# The next native filesystem/import notification schedules another attempt.
		return
	var source := FileAccess.get_file_as_string("res://rookframe.json")
	if source == _manifest and _paths.is_empty() and FileAccess.file_exists(PATH):
		return
	var parsed := JSON.new()
	if parsed.parse(source) != OK or not parsed.data is Dictionary:
		push_error("Rookframe: correct rookframe.json before updating the running World.")
		return
	if FileAccess.file_exists(PATH):
		var previous = JSON.parse_string(FileAccess.get_file_as_string(PATH))
		if not previous is Dictionary or previous.get("schema") != SCHEMA:
			push_error("Rookframe: development declarations path contains an author-owned file: " + PATH)
			return
	var paths: Array = _paths.keys()
	paths.sort()
	_generation += 1
	var snapshot := {"schema": SCHEMA, "manifest": source,
		"changedPaths": paths, "generation": _generation}
	DirAccess.make_dir_recursive_absolute(PATH.get_base_dir())
	var file := FileAccess.open(PATH, FileAccess.WRITE)
	if file == null:
		push_error("Rookframe: cannot save development declarations: " + error_string(FileAccess.get_open_error()))
		return
	file.store_string(JSON.stringify(snapshot, "\t") + "\n")
	file.close()
	_manifest = source
	_paths.clear()
	# The native scan schedules imports safely, including after an external scene
	# reload. Synchronous reimport_files can reenter a deferred editor reload.
	filesystem.scan()
