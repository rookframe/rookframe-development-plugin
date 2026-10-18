@tool
extends EditorImportPlugin


func _get_importer_name() -> String:
	return "rookframe.development"


func _get_visible_name() -> String:
	return "Rookframe development declarations"


func _get_recognized_extensions() -> PackedStringArray:
	return PackedStringArray(["rookframe-dev"])


func _get_save_extension() -> String:
	return "res"


func _get_resource_type() -> String:
	return "Resource"


func _get_preset_count() -> int:
	return 0


func _get_import_options(_path: String, _preset_index: int) -> Array[Dictionary]:
	return []


func _import(source_file: String, save_path: String, _options: Dictionary,
		_platform_variants: Array[String], _gen_files: Array[String]) -> Error:
	var source := FileAccess.get_file_as_string(source_file)
	var parsed := JSON.new()
	if parsed.parse(source) != OK or not parsed.data is Dictionary:
		return ERR_PARSE_ERROR
	if parsed.data.get("schema") != "rookframe-development-declarations-v1":
		return ERR_INVALID_DATA
	var declarations := Resource.new()
	# Resource's stock name setter emits Changed when native reimport replaces it.
	# The runtime defers reading until Resource.copy_from has copied the metadata.
	declarations.resource_name = source.sha256_text()
	declarations.set_meta("declarations", source)
	return ResourceSaver.save(declarations, save_path + ".res")
