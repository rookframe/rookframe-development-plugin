@tool
extends RefCounted
## One editor draft of the publisher-owned Manifest. Never copied onto scene nodes.

signal changed
signal saved(paths: PackedStringArray)
signal problem(message: String)

const MANIFEST := "res://rookframe.json"
const RECOVERY := "res://.godot/rookframe-package-draft.json"
var manifest: Dictionary = {}
var conflict := ""
var busy := false
var undo: EditorUndoRedoManager
var _disk := ""
var _baseline: Dictionary = {}
var _epoch := 0
var _moves: Array[Array] = []
var _first_load := true


func reload(discard := false) -> void:
	if busy:
		return
	var source := FileAccess.get_file_as_string(MANIFEST) if FileAccess.file_exists(MANIFEST) else ""
	if source == _disk and not discard:
		return
	if dirty() and not discard:
		conflict = "rookframe.json changed outside the editor. Copy your draft or reload the file before saving."
		changed.emit()
		return
	var parsed: Variant = JSON.parse_string(source) if not source.is_empty() else {}
	if not parsed is Dictionary or (not source.is_empty() and not parsed.get("id") is String):
		conflict = "Cannot read rookframe.json. Correct the file, then reload it."
		changed.emit()
		return
	_disk = source
	manifest = parsed
	_baseline = manifest.duplicate(true)
	_epoch += 1
	_moves.clear()
	conflict = ""
	if discard:
		DirAccess.remove_absolute(RECOVERY)
	elif _first_load and FileAccess.file_exists(RECOVERY):
		var recovery: Variant = JSON.parse_string(FileAccess.get_file_as_string(RECOVERY))
		if recovery is Dictionary and recovery.get("manifest") is Dictionary and recovery.manifest.get("id") == manifest.get("id"):
			manifest = recovery.manifest
			if recovery.get("expected") != _disk.sha256_text():
				conflict = "Recovered an unsaved Package draft. rookframe.json also changed. Copy the draft or reload the file before saving."
	_first_load = false
	changed.emit()


func dirty() -> bool:
	return manifest != _baseline


func root_path() -> String:
	return "res://rookframe/packages/" + str(manifest.get("id", "")) + "/"


func owns(path: String) -> bool:
	return not manifest.is_empty() and path.begins_with(root_path()) and path == path.simplify_path()


func entries() -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	for group: Dictionary in manifest.get("content", []):
		for entry: Dictionary in group.get("entries", []):
			result.append(entry)
	return result


func entry_at(path: String) -> Dictionary:
	for entry: Dictionary in entries():
		if root_path() + str(entry.get("payload", "")) == path:
			return entry
	return {}


func entry_by_id(id: String) -> Dictionary:
	for entry: Dictionary in entries():
		if entry.get("id") == id:
			return entry
	return {}


func change(label: String, value: Dictionary) -> void:
	if busy:
		problem.emit("Wait for the Package operation to finish before changing its properties.")
		return
	if value == manifest:
		return
	if not conflict.is_empty():
		problem.emit(conflict)
		return
	undo.create_action(label, UndoRedo.MERGE_DISABLE, self)
	undo.add_do_method(self, "_apply", value.duplicate(true), _epoch, _moves.size())
	undo.add_undo_method(self, "_apply", manifest.duplicate(true), _epoch, _moves.size())
	undo.commit_action()


func _apply(value: Dictionary, epoch: int, move_version: int) -> void:
	if epoch != _epoch:
		problem.emit("This undo belongs to an earlier Manifest. The externally changed file has been preserved.")
		return
	manifest = value.duplicate(true)
	for move: Array in _moves.slice(move_version):
		_relocate(manifest, move[0], move[1])
	_checkpoint()
	changed.emit()


func _checkpoint() -> void:
	DirAccess.make_dir_recursive_absolute("res://.godot")
	var writer := FileAccess.open(RECOVERY, FileAccess.WRITE)
	if writer != null:
		writer.store_string(JSON.stringify({"manifest": manifest, "expected": _disk.sha256_text()}))
		writer.close()
	else:
		problem.emit("Could not retain a recovery draft. Copy the draft from the Package dock before closing.")


func update_entry(id: String, fields: Dictionary) -> void:
	var next := manifest.duplicate(true)
	for group: Dictionary in next.get("content", []):
		for entry: Dictionary in group.get("entries", []):
			if entry.get("id") == id:
				for key: String in fields:
					if key not in ["id", "type"]:
						if fields[key] == null:
							entry.erase(key)
						else:
							entry[key] = fields[key]
	change("Edit Rookframe Content", next)


func add_entry(path: String, display_name: String, type: String, system: Dictionary = {}) -> bool:
	if busy or not conflict.is_empty():
		return false
	if not owns(path) or not FileAccess.file_exists(path):
		problem.emit("Save the resource inside this Package's folder first.")
		return false
	if not entry_at(path).is_empty():
		problem.emit("This resource is already Content. Edit its existing properties.")
		return false
	if display_name.strip_edges().is_empty():
		problem.emit("Enter a display name.")
		return false
	var next := manifest.duplicate(true)
	var groups: Array = next.get("content", [])
	var target: Dictionary = {}
	for group: Dictionary in groups:
		if (system.is_empty() and group.get("kind") == "visual") or (
			not system.is_empty() and group.get("kind") == "system"
			and group.get("targetSystemPackageId") == system.get("targetSystemPackageId")
			and group.get("targetContractId") == system.get("targetContractId")
			and group.get("compatibleVersions") == system.get("compatibleVersions")):
			target = group
			break
	if target.is_empty():
		target = system.duplicate()
		target["kind"] = "visual" if system.is_empty() else "system"
		target["entries"] = []
		groups.append(target)
	var id := "content-" + Crypto.new().generate_random_bytes(16).hex_encode()
	target.entries.append({"id": id, "displayName": display_name.strip_edges(),
		"type": type, "payload": path.trim_prefix(root_path())})
	next["content"] = groups
	change("Add Rookframe Content", next)
	return true


func remove_entry(id: String) -> void:
	var next := manifest.duplicate(true)
	var groups: Array = []
	for group: Dictionary in next.get("content", []):
		group["entries"] = group.entries.filter(func(entry: Dictionary): return entry.id != id)
		if not group.entries.is_empty():
			groups.append(group)
	if groups.is_empty():
		next.erase("content")
	else:
		next["content"] = groups
	change("Remove Rookframe Content declaration", next)


func move_path(old_path: String, new_path: String) -> void:
	if not owns(old_path) and not old_path.begins_with("res://rookframe/packages/"):
		return
	var next := manifest.duplicate(true)
	_relocate(next, old_path, new_path)
	if next == manifest:
		return
	# FileSystem moves are already performed by Godot. Apply their path mapping
	# to earlier undo snapshots as well; undoing a name must not undo a file move.
	_moves.append([old_path, new_path])
	manifest = next
	_checkpoint()
	changed.emit()


func _relocated(path: String, old_path: String, new_path: String) -> String:
	var absolute := root_path() + path
	if absolute == old_path or absolute.begins_with(old_path.trim_suffix("/") + "/"):
		absolute = new_path.trim_suffix("/") + absolute.trim_prefix(old_path.trim_suffix("/"))
		if owns(absolute):
			return absolute.trim_prefix(root_path())
	return path


func _relocate(value: Dictionary, old_path: String, new_path: String) -> void:
	for group: Dictionary in value.get("content", []):
		for entry: Dictionary in group.get("entries", []):
			for key in ["payload", "preview"]:
				if entry.has(key):
					entry[key] = _relocated(str(entry[key]), old_path, new_path)
	for presentation: Dictionary in value.get("presentations", []):
		presentation.entryPoint = _relocated(presentation.entryPoint, old_path, new_path)
	if value.has("implementation"):
		value.implementation.entryPoint = _relocated(value.implementation.entryPoint, old_path, new_path)


func save(operation := "save", extra: Dictionary = {}, candidate: Dictionary = {}, label := "") -> bool:
	if busy:
		problem.emit("Wait for the Package operation to finish before saving its properties.")
		return false
	if not conflict.is_empty():
		problem.emit(conflict)
		return false
	if not dirty() and operation == "save" and candidate.is_empty():
		return true
	var request := extra.duplicate(true)
	request.merge({"operation": operation, "manifest": manifest if candidate.is_empty() else candidate, "expected": _disk.sha256_text()}, true)
	var request_path := "res://.godot/rookframe-editor-request.json"
	DirAccess.make_dir_recursive_absolute("res://.godot")
	var writer := FileAccess.open(request_path, FileAccess.WRITE)
	if writer == null:
		problem.emit("Cannot save the Package draft: " + error_string(FileAccess.get_open_error()))
		return false
	writer.store_string(JSON.stringify(request))
	writer.close()
	var python := OS.get_environment("ROOKFRAME_PYTHON")
	if python.is_empty():
		python = "python" if OS.get_name() == "Windows" else "python3"
	var output: Array = []
	var code := OS.execute(python, PackedStringArray(["-X", "utf8",
		ProjectSettings.globalize_path("res://addons/rookframe_development/editor_project.py"),
		"--project", ProjectSettings.globalize_path("res://"),
		"--request", ProjectSettings.globalize_path(request_path)]), output, true)
	DirAccess.remove_absolute(request_path)
	if code != 0:
		problem.emit("\n".join(output))
		return false
	_disk = FileAccess.get_file_as_string(MANIFEST)
	var saved_value: Dictionary = JSON.parse_string(_disk)
	# Source-creating transactions earn an Undo action only after all files commit.
	# Failure leaves both the draft and its history untouched.
	if candidate.is_empty():
		manifest = saved_value
	else:
		change(label, saved_value)
	_baseline = saved_value.duplicate(true)
	DirAccess.remove_absolute(RECOVERY)
	changed.emit()
	var result: Variant = JSON.parse_string("\n".join(output))
	saved.emit(PackedStringArray(result.get("changedPaths", [MANIFEST])) if result is Dictionary else PackedStringArray([MANIFEST]))
	return true


func presentation_changes(id: String, path: String, experiences: Array) -> Dictionary:
	var next := manifest.duplicate(true)
	var result: Array = []
	for item: Dictionary in next.get("presentations", []):
		if item.id == id:
			continue
		item["experiences"] = item.experiences.filter(func(value: String): return value not in experiences)
		if not item.experiences.is_empty():
			result.append(item)
	result.append({"id": id, "entryPoint": path, "experiences": experiences})
	next["presentations"] = result
	return next
