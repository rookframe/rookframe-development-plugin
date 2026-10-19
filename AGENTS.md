# Repository boundary

This repository owns the Godot editor plugin and Development World preparation.
Use the pinned public Rookframe SDK source and its Package commands. Package
authors never generate or rebuild the SDK.
The Rookframe application owns the C# API, Package checks and ordinary application releases.
Prepare Development Worlds from the installed application; do not require a
separate development-runtime distribution.
Keep plugin source and tests here; consuming repositories install a fixed release.

Use stock Godot editor, import and live-edit facilities. A departure requires the
user's explicit approval. Preserve the authored dock scenes and existing World
configuration when changing implementation.

Reuse one author project and saved World when verifying development behavior.
Only its single Editable Package may use source; acquire other Packages through
published HTTPS Manifests. Keep verification projects, Worlds and captures outside
this repository. Retain unique fast tests; do not add fixture Packages.

Run the checks in README.md before publishing an immutable plugin release.
