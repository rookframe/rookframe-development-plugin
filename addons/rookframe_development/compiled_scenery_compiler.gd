extends SceneTree
## Native encoder for Package-local, reusable scenery. World records are produced by Rookframe.

const ASSETS := "_rookframe_scenery/assets/"
const INDEX := "_rookframe_scenery/index.rfscenery"
const COMPILER_VERSION := 1
const RENDERER_PROFILE := "shared"
var _request: Dictionary
var _paths := {}
var _files := {}
var _entries: Array = []
var _members := {}
var _fallbacks: Array = []
var _vegetation_sources: Array = []
var _counts := {"geometrySurfaces": 0, "shadowSurfaces": 0, "nativeArtwork": 0}
var _error := ""
var _include := RegEx.new()
var _surface_issue := ""


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() != 1:
		push_error("SCENERY.FORMAT: expected one compiler request")
		quit(1)
		return
	_request = JSON.parse_string(FileAccess.get_file_as_string(args[0]))
	_include.compile('(?m)^\\s*#include\\s+"([^"\\n]+)"')
	if not String(_request.applicationPack).is_empty() and not ProjectSettings.load_resource_pack(_request.applicationPack, false):
		push_error("SCENERY.REFERENCE: cannot open the installed Rookframe resource pack")
		quit(1)
		return
	for path: String in _request.paths:
		_paths[path] = true
	for job: Dictionary in _request.jobs:
		_prepare(job)
		if not _error.is_empty():
			break
	if _error.is_empty() and not _entries.is_empty():
		_entries.sort_custom(_entry_before)
		if _entries.size() > 65536:
			_fail("BOUNDS", INDEX, "entries exceeds 65,536")
		else:
			var index := {
				"format": _request.indexFormat, "compilerVersion": COMPILER_VERSION,
				"packageId": _request.packageId, "buildId": _request.buildId,
				"profile": _request.profile, "entries": _entries,
			}
			_members[INDEX] = _save(INDEX, _frame(index))
			if not str(_request.export).is_empty():
				_include_export()
	if not _error.is_empty():
		push_error(_error)
		quit(1)
		return
	var report := FileAccess.open(_request.report, FileAccess.WRITE)
	if report == null:
		push_error("SCENERY.REFERENCE: cannot write compiler report")
		quit(1)
		return
	report.store_string(JSON.stringify({"members": _members, "counts": _counts, "fallbacks": _fallbacks,
		"vegetationSources": _vegetation_sources}))
	report.close()
	quit()


func _prepare(job: Dictionary) -> void:
	var path: String = _request.root + job.path
	if job.target not in ["grass", "trees"]:
		# Keep an authored composition and its collision, animation and materials.
		# Its separate native resource remains subject to the normal Package checker.
		if ResourceLoader.exists(path, "PackedScene") or ResourceLoader.exists(path, "Mesh"):
			var native: Resource = _load(job, path)
			if native is PackedScene or native is Mesh:
				var record := _asset(job, 0, "artwork", path)
				record.nativeArtwork = job.path
				if native is Mesh:
					record.bounds = native.get_aabb()
				elif _can_instantiate(native):
					_native_composition(record, native)
				record.unanimatedBounds = record.bounds
				_emit(record)
		return
	var definition := _load(job, path)
	if definition == null:
		return
	var variants: Variant = definition.get_meta("variants", [])
	if not variants is Array:
		_fail("FORMAT", job.content, "variants must be an Array of PackedScene resources")
		return
	for variant in variants.size():
		if not variants[variant] is PackedScene:
			_fail("FORMAT", job.content, "variant %s must be a PackedScene" % variant)
			return
		_prepare_vegetation(job, variant, variants[variant], definition)


func _load(job: Dictionary, path: String) -> Resource:
	# Authored scripting is outside the geometry compiler. Avoid loading file-backed
	# scripts found in this graph; instance inspection additionally rejects scene scripts.
	var dependencies := _dependencies(path)
	for relative: String in dependencies:
		if relative.get_extension().to_lower() in ["gd", "gdc", "cs", "gdscript"]:
			_fallback(job, -1, "source-only scripted dependency " + relative)
			return null
	if not _error.is_empty():
		return null
	var resource := ResourceLoader.load(path)
	if resource == null:
		_fail("REFERENCE", job.content, "cannot load " + path)
	return resource


func _prepare_vegetation(job: Dictionary, variant: int, scene: PackedScene, definition: Resource) -> void:
	var scene_path := _relative(scene.resource_path)
	if scene_path.is_empty():
		_fallback(job, variant, "variant has no exact local PackedScene path")
		return
	if not _can_instantiate(scene):
		var record := _asset(job, variant, "artwork", scene.resource_path)
		record.nativeArtwork = scene_path
		_vegetation_sources.append(_source_info(record, false, false))
		_emit(record)
		_fallback(job, variant, "native composition contains a script or placeholder")
		return
	var model := scene.instantiate()
	if not model is Node3D:
		model.free()
		_fail("FORMAT", job.content, "variant %s must have a Node3D root" % variant)
		return
	var meshes := _mesh_nodes(model)
	var lods := {}
	for node: MeshInstance3D in meshes:
		var name := String(node.name)
		if name.begins_with("LOD") and name.substr(3).is_valid_int():
			lods[int(name.substr(3))] = node
	var grouped := lods.has(0)
	if grouped:
		var ordered := lods.keys()
		ordered.sort()
		var nodes: Array = ordered.map(func(lod: int): return lods[lod])
		var last: MeshInstance3D = lods.get(2, lods.get(1, lods[0]))
		var authored_lods: bool = (lods.has(1) and lods.has(2)) or String(last.get_meta("vegetation_lod_role", "")) in ["cards", "impostor"]
		_prepare_part(job, variant, 0, definition, scene, nodes, true, true, authored_lods)
	else:
		for part in meshes.size():
			_prepare_part(job, variant, part, definition, scene, [meshes[part]], false,
				_requires_native(model, meshes[part]), false)
	if meshes.is_empty():
		_fail("FORMAT", job.content, "variant %s does not contain a mesh" % variant)
	model.free()


func _mesh_nodes(root: Node3D) -> Array:
	var meshes: Array = [root] if root is MeshInstance3D else []
	meshes.append_array(root.find_children("*", "MeshInstance3D", true, false))
	return meshes.filter(func(node: MeshInstance3D): return node.mesh != null)


func _native_composition(record: Dictionary, scene: PackedScene) -> void:
	var root := scene.instantiate()
	if root is Node3D:
		var has_bounds := false
		for node: MeshInstance3D in _mesh_nodes(root):
			var bounds := _composition_transform(node) * node.mesh.get_aabb()
			record.bounds = record.bounds.merge(bounds) if has_bounds else bounds
			has_bounds = true
		var colliders: Array = [root] if root is CollisionShape3D else []
		colliders.append_array(root.find_children("*", "CollisionShape3D", true, false))
		colliders = colliders.filter(func(node: CollisionShape3D): return not node.disabled and node.shape != null)
		if colliders.size() == 1:
			var node: CollisionShape3D = colliders[0]
			var transform := _composition_transform(node)
			var shape := node.shape
			if shape is ConvexPolygonShape3D and shape.points.size() >= 4:
				record.collision = {"kind": "convex", "points": transform * shape.points}
			elif shape is ConcavePolygonShape3D:
				var triangles: PackedVector3Array = shape.get_faces()
				if not triangles.is_empty() and triangles.size() % 3 == 0:
					record.collision = {"kind": "triangles", "points": transform * triangles}
	root.free()


func _can_instantiate(scene: PackedScene, visited: Dictionary = {}) -> bool:
	var identity := scene.get_instance_id()
	if visited.has(identity):
		return true
	visited[identity] = true
	var state := scene.get_state()
	for node in state.get_node_count():
		if state.is_node_instance_placeholder(node):
			return false
		var nested := state.get_node_instance(node)
		if nested != null and not _can_instantiate(nested, visited):
			return false
		for property in state.get_node_property_count(node):
			if state.get_node_property_name(node, property) == &"script" and state.get_node_property_value(node, property) != null:
				return false
	return true


func _requires_native(root: Node3D, mesh: MeshInstance3D) -> bool:
	return mesh.material_overlay != null or mesh.skin != null or mesh.mesh.get_blend_shape_count() != 0 \
		or (mesh.mesh is ArrayMesh and mesh.mesh.shadow_mesh != null) \
		or not root.find_children("*", "AnimationPlayer", true, false).is_empty() \
		or not root.find_children("*", "CollisionShape3D", true, false).is_empty()


func _prepare_part(job: Dictionary, variant: int, part: int, definition: Resource,
		scene: PackedScene, nodes: Array, grouped: bool, preserve_native: bool, authored_lods: bool) -> void:
	var first: MeshInstance3D = nodes[0]
	var mesh: Mesh = first.mesh
	var record := _asset(job, variant, "mesh/" + str(part), scene.resource_path)
	record.transform = Transform3D.IDENTITY if grouped else _composition_transform(first)
	record.nativeArtwork = _relative(scene.resource_path)
	var grass: bool = job.target == "grass"
	var textures := {}
	var margin := 0.0
	var height := maxf(0.01, mesh.get_aabb().end.y)
	for node: MeshInstance3D in nodes:
		var wind := _inspect_materials(node, textures)
		if wind and node == first:
			margin = maxf(1.4, height + 0.5) if grass else height * 0.5
	var crown := float(definition.get_meta("retain_crown_lods", 0)) > 0.0
	var texture_paths := textures.keys()
	texture_paths.sort_custom(_ordinal)
	record.bounds = _bounds(mesh, margin)
	record.unanimatedBounds = mesh.get_aabb()
	record.windMargin = margin if not grass else 0.0
	record.motion = _motion(job.target, nodes.size() - 1,
		mini(1, nodes.size() - 1) if grouped and not grass else 0,
		crown, grass and not crown, float(definition.get_meta("terrain_color_influence", 0)),
		PackedStringArray(texture_paths))
	var authored_shadow := nodes.any(func(node: MeshInstance3D): return node.mesh is ArrayMesh and node.mesh.shadow_mesh != null)
	var source := _source_info(record, authored_lods, authored_shadow)
	_vegetation_sources.append(source)
	var chunks: Array = [record]
	if not preserve_native:
		var geometry := _surfaces(mesh, first)
		if geometry.is_empty():
			source["nativeGeometryReason"] = _surface_issue
		else:
			var packed := record.duplicate()
			packed.nativeArtwork = ""
			packed.surfaces = geometry
			chunks.append(packed)
	_emit_chunks(chunks)


func _source_info(record: Dictionary, authored_lods: bool, authored_shadow: bool) -> Dictionary:
	return {"content": record.content, "variant": record.variant, "part": record.part,
		"artwork": record.nativeArtwork, "sourceSha256": record.sourceSha256,
		"authoredLods": authored_lods, "authoredShadow": authored_shadow}


func _inspect_materials(node: MeshInstance3D, textures: Dictionary) -> bool:
	var motion := false
	for surface in node.mesh.get_surface_count():
		var material := _material(node, node.mesh, surface)
		if material == null:
			continue
		if material is ShaderMaterial and material.shader != null:
			for uniform: Dictionary in material.shader.get_shader_uniform_list():
				var parameter := String(uniform.name)
				motion = motion or parameter in ["wind_strength", "tree_wind_strength"]
				_add_texture(textures, material.get_shader_parameter(parameter))
		for property in material.get_property_list():
			if property.usage & PROPERTY_USAGE_STORAGE and property.type == TYPE_OBJECT:
				_add_texture(textures, material.get(property.name))
	return motion


func _add_texture(textures: Dictionary, value: Variant) -> void:
	if value is Texture2D:
		var path := _relative(value.resource_path)
		if not path.is_empty():
			textures[path] = true


func _composition_transform(node: Node3D) -> Transform3D:
	var transform := node.transform
	var ancestor := node.get_parent()
	while ancestor is Node3D:
		transform = ancestor.transform * transform
		ancestor = ancestor.get_parent()
	return transform


func _bounds(mesh: Mesh, margin: float) -> AABB:
	if margin > 0.0:
		return mesh.get_aabb().grow(margin)
	if mesh is ArrayMesh and mesh.custom_aabb != AABB():
		return mesh.custom_aabb
	return mesh.get_aabb()


func _material(node: MeshInstance3D, mesh: Mesh, surface: int) -> Material:
	if node.material_override != null:
		return node.material_override
	var override: Material = node.get_surface_override_material(surface)
	return override if override != null else mesh.surface_get_material(surface)


func _surfaces(mesh: Mesh, node: MeshInstance3D) -> Array:
	_surface_issue = ""
	if mesh.get_blend_shape_count() != 0 or mesh.get_surface_count() == 0 or node.skin != null:
		_surface_issue = "mesh has no surfaces, or uses blend shapes or a skin"
		return []
	var importer := ImporterMesh.from_mesh(mesh)
	if importer == null:
		_surface_issue = "Godot could not expose the original surfaces"
		return []
	var result: Array = []
	for index in importer.get_surface_count():
		var arrays := importer.get_surface_arrays(index)
		if importer.get_surface_primitive_type(index) != Mesh.PRIMITIVE_TRIANGLES:
			_surface_issue = "surface %s uses nontriangle primitives" % index
			return []
		for channel in [Mesh.ARRAY_BONES, Mesh.ARRAY_WEIGHTS, Mesh.ARRAY_CUSTOM0,
				Mesh.ARRAY_CUSTOM1, Mesh.ARRAY_CUSTOM2, Mesh.ARRAY_CUSTOM3]:
			if arrays[channel] != null and not arrays[channel].is_empty():
				_surface_issue = "surface %s uses native-only mesh channel %s" % [index, channel]
				return []
		var material := _material(node, mesh, index)
		var path := "" if material == null else _relative(material.resource_path)
		if material != null and path.is_empty():
			_surface_issue = "surface %s material has no exact local path (%s)" % [index, material.resource_path]
			return []
		var surface := _surface(importer, index, arrays, path)
		if surface.is_empty():
			return []
		result.append(surface)
	return result


func _surface(mesh: ImporterMesh, index: int, arrays: Array, material: String) -> Dictionary:
	var distances := PackedFloat32Array()
	var offsets := PackedInt32Array([0])
	var indices := PackedInt32Array()
	var lods := {}
	for lod in mesh.get_surface_lod_count(index):
		var distance := mesh.get_surface_lod_size(index, lod)
		if not is_finite(distance) or distance <= 0.0 or lods.has(distance):
			return _unsupported(index, "lodDistances", "expected positive, distinct, finite values")
		lods[distance] = mesh.get_surface_lod_indices(index, lod)
	var order := lods.keys()
	order.sort()
	var vertices := PackedVector3Array(arrays[Mesh.ARRAY_VERTEX])
	var triangles := PackedInt32Array([] if arrays[Mesh.ARRAY_INDEX] == null else arrays[Mesh.ARRAY_INDEX])
	if vertices.size() < 3 or not _valid_indices(triangles, vertices.size(), true):
		return _unsupported(index, "vertices or indices", "invalid triangle layout")
	for distance: float in order:
		var lod: PackedInt32Array = lods[distance]
		if not _valid_indices(lod, vertices.size(), false):
			return _unsupported(index, "lodIndices", "invalid triangle layout")
		distances.append(distance)
		indices.append_array(lod)
		offsets.append(indices.size())
	var normals := PackedVector3Array([] if arrays[Mesh.ARRAY_NORMAL] == null else arrays[Mesh.ARRAY_NORMAL])
	var tangents := PackedFloat32Array([] if arrays[Mesh.ARRAY_TANGENT] == null else arrays[Mesh.ARRAY_TANGENT])
	var colours := PackedColorArray([] if arrays[Mesh.ARRAY_COLOR] == null else arrays[Mesh.ARRAY_COLOR])
	var uv := PackedVector2Array([] if arrays[Mesh.ARRAY_TEX_UV] == null else arrays[Mesh.ARRAY_TEX_UV])
	var uv2 := PackedVector2Array([] if arrays[Mesh.ARRAY_TEX_UV2] == null else arrays[Mesh.ARRAY_TEX_UV2])
	var channels := {"normals": normals, "colours": colours, "uv": uv, "uv2": uv2}
	for name: String in channels:
		var channel = channels[name]
		if not channel.is_empty() and channel.size() != vertices.size():
			return _unsupported(index, name, "expected one value per vertex")
	if not tangents.is_empty() and tangents.size() != vertices.size() * 4:
		return _unsupported(index, "tangents", "expected four values per vertex")
	for name: String in ["vertices", "normals", "uv", "uv2"]:
		var channel = vertices if name == "vertices" else channels[name]
		for coordinate in channel:
			if not coordinate.is_finite():
				return _unsupported(index, name, "nonfinite value")
	for tangent in tangents:
		if not is_finite(tangent):
			return _unsupported(index, "tangents", "nonfinite value")
	for colour in colours:
		if not (is_finite(colour.r) and is_finite(colour.g) and is_finite(colour.b) and is_finite(colour.a)):
			return _unsupported(index, "colours", "nonfinite value")
	return {"name": String(mesh.get_surface_name(index)), "material": material,
		"vertices": vertices, "normals": normals, "tangents": tangents,
		"colours": colours, "uv": uv, "uv2": uv2, "indices": triangles,
		"lodDistances": distances, "lodOffsets": offsets, "lodIndices": indices}


func _unsupported(surface: int, field: String, reason: String) -> Dictionary:
	_surface_issue = "surface %s %s: %s" % [surface, field, reason]
	return {}


func _valid_indices(indices: PackedInt32Array, vertices: int, base: bool) -> bool:
	if indices.is_empty():
		return base and vertices % 3 == 0
	if indices.size() % 3 != 0:
		return false
	for index in indices:
		if index < 0 or index >= vertices:
			return false
	return true


func _motion(target: String, lod: int, shadow_lod: int, crown: bool,
		cover: bool, influence: float, textures: PackedStringArray) -> Dictionary:
	return {"target": target, "lod": lod, "shadowLod": shadow_lod, "retainCrownLods": crown,
		"groundCover": cover, "heightOffset": 0.0, "terrainColourInfluence": influence,
		"textureReferences": textures}


func _asset(job: Dictionary, variant: int, part: String, artwork: String) -> Dictionary:
	var dependencies := _dependencies(_request.root + job.path)
	dependencies.merge(_dependencies(artwork))
	dependencies.merge(_request.algorithm)
	var ordered := dependencies.keys()
	ordered.sort_custom(_ordinal)
	var sources: Array = ordered.map(func(key: String): return [key, dependencies[key]])
	var digest := _sha(var_to_bytes([COMPILER_VERSION, job.content, variant, part, RENDERER_PROFILE, sources]))
	return {"format": _request.format, "compilerVersion": COMPILER_VERSION, "content": job.content,
		"variant": variant, "part": part, "chunk": 0, "rendererProfile": RENDERER_PROFILE,
		"sourceSha256": digest, "nativeArtwork": "", "transform": Transform3D.IDENTITY,
		"bounds": AABB(), "unanimatedBounds": AABB(), "windMargin": 0.0, "surfaces": [],
		"shadowSurfaces": [], "collision": {"kind": "none", "points": PackedVector3Array()},
		"motion": _motion("none", 0, 0, false, false, 0.0, PackedStringArray())}


func _relative(path: String) -> String:
	var root: String = _request.root
	if not path.begins_with(root):
		return ""
	var relative := path.substr(root.length())
	return relative if _paths.has(relative) or _paths.has(relative + ".remap") else ""


func _dependencies(start: String) -> Dictionary:
	var result := {}
	var pending: Array[String] = [start]
	while not pending.is_empty():
		var path: String = pending.pop_back()
		var relative := _relative(path)
		if relative.is_empty() and path.begins_with("res://rookframe/application/") and not String(_request.applicationPack).is_empty():
			relative = path
		if relative.is_empty():
			_fail("REFERENCE", start, "missing or nonlocal artwork dependency " + path)
			break
		if result.has(relative):
			continue
		if not _files.has(relative):
			_files[relative] = _dependency_hash(path, relative == path)
		if String(_files[relative]).is_empty():
			_fail("REFERENCE", start, "cannot hash dependency " + path)
			break
		result[relative] = _files[relative]
		for suffix in [".import", ".remap"]:
			if _paths.has(relative + suffix):
				result[relative + suffix] = FileAccess.get_sha256(path + suffix)
		if _paths.has(relative + ".remap"):
			var remap := ConfigFile.new()
			if remap.load(path + ".remap") == OK:
				var target: String = remap.get_value("remap", "path", "")
				if not target.is_empty() and target != path:
					pending.append(target)
		for dependency in ResourceLoader.get_dependencies(path):
			pending.append(dependency.get_slice("::", dependency.get_slice_count("::") - 1))
		if path.get_extension() in ["gdshader", "gdshaderinc"]:
			for directive in _include.search_all(FileAccess.get_file_as_string(path)):
				var include := directive.get_string(1)
				pending.append(include if include.begins_with("res://") else path.get_base_dir().path_join(include).simplify_path())
	return result


func _dependency_hash(path: String, application: bool) -> String:
	if FileAccess.file_exists(path):
		return FileAccess.get_sha256(path)
	for suffix in [".remap", ".import"]:
		var sidecar: String = path + suffix
		if not FileAccess.file_exists(sidecar):
			continue
		if not application:
			return FileAccess.get_sha256(sidecar)
		# Exported application textures can be represented only by the import
		# mapping and renderer-specific native targets inside the installed PCK.
		var mapping := ConfigFile.new()
		if mapping.load(sidecar) != OK or not mapping.has_section("remap"):
			return ""
		var keys := mapping.get_section_keys("remap")
		keys.sort()
		var digests := [FileAccess.get_sha256(sidecar)]
		for key in keys:
			if key == "path" or key.begins_with("path."):
				var digest := FileAccess.get_sha256(mapping.get_value("remap", key))
				if digest.is_empty():
					return ""
				digests.append(digest)
		return _sha(var_to_bytes(digests))
	return ""


func _emit(record: Dictionary) -> void:
	_emit_chunks([record])


func _emit_chunks(records: Array) -> void:
	var chunks: Array = []
	for record: Dictionary in records:
		if not record.transform.is_finite() or not _valid_bounds(record.bounds) or not _valid_bounds(record.unanimatedBounds) \
				or not is_finite(record.windMargin) or not is_finite(record.motion.terrainColourInfluence):
			_fail("FORMAT", record.content, "%s has invalid transform, bounds or motion" % record.part)
			return
		for point: Vector3 in record.collision.points:
			if not point.is_finite():
				_fail("FORMAT", record.content, "%s has nonfinite collision points" % record.part)
				return
		if _frame(record).size() <= int(_request.maximum):
			chunks.append(record)
			continue
		# Keep each original mesh surface with all of its channels and LODs.
		for field in ["surfaces", "shadowSurfaces"]:
			for surface in record[field]:
				var section := record.duplicate()
				section.surfaces = []
				section.shadowSurfaces = []
				section[field].append(surface)
				chunks.append(section)
		if record.surfaces.is_empty() and record.shadowSurfaces.is_empty():
			_fail("BOUNDS", record.content, "%s cannot be split into bounded surfaces" % record.part)
			return
	for chunk in chunks.size():
		var record: Dictionary = chunks[chunk]
		record.chunk = chunk
		var bytes := _frame(record)
		var digest := _sha(bytes)
		var path := ASSETS + digest + ".rfscenery"
		if _members.has(path):
			_fail("REFERENCE", record.content, "duplicate generated member " + path)
			return
		_members[path] = _save(path, bytes)
		_entries.append({"content": record.content, "variant": record.variant, "part": record.part,
			"rendererProfile": RENDERER_PROFILE, "chunk": chunk, "chunkCount": chunks.size(),
			"path": path, "length": bytes.size(), "sha256": digest, "sourceSha256": record.sourceSha256})
		_counts.geometrySurfaces += record.surfaces.size()
		_counts.shadowSurfaces += record.shadowSurfaces.size()
		_counts.nativeArtwork += int(not record.nativeArtwork.is_empty())


func _valid_bounds(bounds: AABB) -> bool:
	return bounds.position.is_finite() and bounds.size.is_finite() and bounds.size.x >= 0 \
		and bounds.size.y >= 0 and bounds.size.z >= 0


func _ordinal(left: String, right: String) -> bool:
	var first := left.to_utf16_buffer()
	var second := right.to_utf16_buffer()
	for offset in range(0, mini(first.size(), second.size()), 2):
		var a := first.decode_u16(offset)
		var b := second.decode_u16(offset)
		if a != b:
			return a < b
	return first.size() < second.size()


func _entry_before(left: Dictionary, right: Dictionary) -> bool:
	if left.content != right.content:
		return _ordinal(left.content, right.content)
	if left.variant != right.variant:
		return left.variant < right.variant
	if left.part != right.part:
		return _ordinal(left.part, right.part)
	return left.chunk < right.chunk


func _frame(record: Dictionary) -> PackedByteArray:
	var body := var_to_bytes(record)
	var result := PackedByteArray()
	result.resize(4)
	result.encode_u32(0, body.size())
	result.append_array(body)
	return result


func _sha(bytes: PackedByteArray) -> String:
	var hashing := HashingContext.new()
	hashing.start(HashingContext.HASH_SHA256)
	hashing.update(bytes)
	return hashing.finish().hex_encode()


func _save(relative: String, bytes: PackedByteArray) -> String:
	if bytes.size() > int(_request.maximum):
		_fail("BOUNDS", relative, "record exceeds 32 MiB including framing")
		return ""
	var path := String(_request.output).path_join(relative)
	var error := DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	if error != OK:
		_fail("REFERENCE", relative, error_string(error))
		return ""
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null or not file.store_buffer(bytes):
		_fail("REFERENCE", relative, "could not write complete framed record")
		return ""
	file.close()
	return _sha(bytes)


func _include_export() -> void:
	var config := ConfigFile.new()
	var path := "res://export_presets.cfg"
	var error := config.load(path)
	if error != OK:
		_fail("REFERENCE", path, error_string(error))
		return
	for section in config.get_sections():
		if config.get_value(section, "name", "") != _request.profile:
			continue
		var filter: String = config.get_value(section, "include_filter", "")
		var patterns := PackedStringArray([_request.export + INDEX, _request.export + ASSETS + "*.rfscenery"])
		if not filter.is_empty():
			patterns.insert(0, filter)
		config.set_value(section, "include_filter", ",".join(patterns))
		error = config.save(path)
		if error != OK:
			_fail("REFERENCE", path, error_string(error))
		return
	_fail("PROFILE", path, "missing export preset")


func _fallback(job: Dictionary, variant: int, reason: String) -> void:
	_fallbacks.append({"code": "SCENERY.PROFILE", "profile": _request.profile,
		"content": job.content, "variant": variant, "reason": reason})


func _fail(code: String, member: String, field: String) -> void:
	if _error.is_empty():
		_error = "SCENERY.%s: %s: %s: %s" % [code, _request.profile, member, field]
