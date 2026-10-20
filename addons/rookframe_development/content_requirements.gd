@tool
extends RefCounted

const TYPES := ["miniature", "prop", "wall_style", "surface_finish", "theme", "system"]
const NAMES := ["Miniature", "Prop", "Wall Style", "Surface Finish", "Theme", "System content"]
const HELP := [
	"The appearance of a Rook on the tabletop. Game data belongs to its Actor. Use a Node3D scene or imported model.",
	"A physical decorative object or fixed scenery. Author PropCollision/CollisionShape3D and assign a primitive shape yourself.",
	"The appearance applied to Builder walls. Author a Surface mesh: +X along the wall, +Y up; one surface with a BaseMaterial3D. Set its sample width below.",
	"The appearance of architectural floors and ceilings. Author a Surface mesh in the XZ plane with one BaseMaterial3D surface. This finish supports both floors and ceilings. Set its sample width below.",
	"A reusable native Godot Theme. Edit its .tres Resource with Godot's Theme editor.",
	"Data interpreted by a particular System. Use that System's documented Resource schema and content contract."
]


static func title(type: String) -> String:
	var index := TYPES.find(type)
	return NAMES[index] if index >= 0 else type.capitalize()


static func inspect(path: String, type: String, edited: Node = null) -> String:
	if not ResourceLoader.exists(path):
		return "Payload missing. Locate the resource to keep this Content identity."
	var resource := ResourceLoader.load(path)
	if type == "theme":
		return "" if resource is Theme else "Choose a native Godot Theme Resource (.tres)."
	if type not in TYPES.slice(0, 4):
		return ""
	if not resource is PackedScene:
		return "Use a saved Node3D scene or an imported model."
	var node: Node = edited if edited != null and edited.scene_file_path == path else resource.instantiate()
	var result := _scene_problem(node, type)
	if node != edited:
		node.free()
	return result


static func _scene_problem(node: Node, type: String) -> String:
	if not node is Node3D:
		return "The scene root must be a Node3D."
	if type == "prop":
		var collider := node.get_node_or_null("PropCollision/CollisionShape3D") as CollisionShape3D
		if collider == null or collider.shape == null:
			return "Add PropCollision/CollisionShape3D and assign a primitive shape. Collision is authored in Godot."
		if not (collider.shape is BoxShape3D or collider.shape is SphereShape3D or
			collider.shape is CapsuleShape3D or collider.shape is CylinderShape3D):
			return "Use a stock primitive collision shape. Mesh, triangle and convex-hull collision are not supported."
	if type in ["wall_style", "surface_finish"]:
		if node.get_meta("content_kind", "") != type:
			return "Use Apply appearance settings below to save this Content type on the scene root."
		if node.get_meta("authored_units_per_world_unit", 0.0) != 1.0:
			return "Use Apply appearance settings below to set one authored unit per world unit."
		var span: Variant = node.get_meta("authored_sample_span", 0.0)
		if not (span is float or span is int) or not is_finite(float(span)) or span < 0.05 or span > 100.0:
			return "Enter a sample width between 0.05 and 100 world units, then Apply appearance settings."
		var surfaces := node.find_children("Surface", "MeshInstance3D", true, false)
		if surfaces.is_empty():
			return "Author a MeshInstance3D named Surface."
		var surface: MeshInstance3D = surfaces[0]
		if surface.mesh == null or surface.mesh.get_surface_count() != 1 or not surface.get_active_material(0) is BaseMaterial3D:
			return "Surface needs a mesh with one surface and a BaseMaterial3D material."
		if type == "surface_finish" and (not node.get_meta("supports_floor", false) or not node.get_meta("supports_ceiling", false)):
			return "Use Apply appearance settings to enable this finish for both floors and ceilings."
	return ""
