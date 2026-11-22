extends SceneTree
## Runs in a disposable stock Godot project. Input is produced by Python.


func _initialize() -> void:
	var request: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://request.json"))
	for job: Dictionary in request.jobs:
		var input := JSON.new()
		if input.parse(FileAccess.get_file_as_string(job.input)) != OK:
			push_error("Cannot read prepared data for " + job.source)
			quit(1)
			return
		if not _write(job.output, {"format": request.format, "payload": _restore(input.data)}):
			quit(1)
			return
	if request.has("index") and not _write("res://index.rfdata", request.index):
		quit(1)
		return
	if request.has("export") and not _include_data(request.export):
		quit(1)
		return
	quit()


func _restore(value: Variant) -> Variant:
	if value is Dictionary:
		# Author keys all have a leading colon in this temporary transport.
		# Numeric tokens carry their exact bits through Godot's JSON parser.
		if value.has("int"):
			return value.int.hex_decode().decode_s64(0)
		if value.has("float"):
			return value.float.hex_decode().decode_double(0)
		var restored := {}
		for key: String in value:
			restored[key.substr(1)] = _restore(value[key])
		return restored
	if value is Array:
		for index in value.size():
			value[index] = _restore(value[index])
	return value


func _write(path: String, value: Dictionary) -> bool:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		push_error("Cannot write " + path + ": " + error_string(FileAccess.get_open_error()))
		return false
	var stored := file.store_var(value, false)
	file.flush()
	var error := file.get_error()
	file.close()
	if not stored or error != OK:
		push_error("Could not store the complete data block at " + path)
		return false
	return true


func _include_data(export: Dictionary) -> bool:
	var presets := ConfigFile.new()
	var error := presets.load(export.path)
	if error != OK:
		push_error("Cannot read disposable export presets: " + error_string(error))
		return false
	for section in presets.get_sections():
		if presets.get_value(section, "name", "") != export.profile:
			continue
		var filter: String = presets.get_value(section, "include_filter", "")
		var patterns := PackedStringArray(export.patterns)
		if not filter.is_empty():
			patterns.insert(0, filter)
		presets.set_value(section, "include_filter", ",".join(patterns))
		error = presets.save(export.path)
		if error != OK:
			push_error("Cannot write disposable export presets: " + error_string(error))
		return error == OK
	push_error("Cannot find the Package export preset: " + export.profile)
	return false
