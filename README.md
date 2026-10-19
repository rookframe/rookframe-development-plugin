# Rookframe Development Plugin

Godot editor tools for authoring Packages and running a reusable Development
World. This repository owns the editor plugin independently of the
[Rookframe SDK](https://github.com/rookframe/rookframe-sdk) and application.

## Install in a Package project

Use stock Godot 4.7.2 .NET, Python 3.10+, and the .NET 10 runtime.
Add the plugin to the project's existing gd-plug dependencies:

```gdscript
plug("rookframe/rookframe-development-plugin", {"tag": "v0.2.0-rc.1", "include": ["addons/rookframe_development"]})
```

Keep the Package's SDK and UI Kit pins. The plugin uses the installed SDK's
commands; version 0.2.0-rc.1 uses SDK v0.36.0-rc.2. Install dependencies using
the project's normal `godot --headless --script plug.gd install` command.
Ignore `addons/rookframe_development/` in the Package's source control.

In **Project Settings → Plugins**, disable **Rookframe Package SDK** if an older
SDK enables it, then enable **Rookframe Development**. Keep the SDK dependency
installed. Published SDK versions remain immutable, including their old plugin
files; those files are inactive after this switch.

Existing Worlds and `.rookframe/development.json` are reused. The new plugin's
**Save and prepare** action refreshes launch settings; it may request one editor
restart. It also migrates the old plugin's placeholder main-scene path while
preserving an author-selected main scene.

## Author Packages

The **Package** tab can initialize an Extension with UI, Content Pack or System
Extension in an ordinary Godot project. Creation writes the Package manifest,
source structure and references to the installed SDK.

Save scenes and models below `rookframe/packages/<package-id>/`. Choose a scene
and **Register scene** to add it as content or a UI contribution. **Prepare model
scene** adds editable supporting scene structure; inspect the result before
registration. The Media controls use the installed SDK's publication commands.

**Project → Tools → Rookframe: Check Package** validates the author project.
**Build Package** produces its archive. These commands use the SDK selected by
the author project; the plugin does not contain a second SDK or verifier.

## Run a Development World

1. Open the author project in the stock Godot .NET editor matching the installed
   Rookframe application's engine, OS and architecture.
2. Open the **Development** tab. Select the installed Rookframe executable,
   the existing World name and published Package Manifest URLs. An editable
   optional Package also needs a published System Manifest URL.
3. Choose **Save and prepare** and reopen the editor once if requested.
4. Run the World. Save scripts/scenes or reimport models in the same session.
   Use Godot's Debugger and Output to inspect errors.

Reuse the World name to preserve its data. SDK Edition, Package identity,
Implementation entry point and Package selection require a session restart.
Only the Editable Package bypasses admission. Other Packages use normal public
HTTPS acquisition. Development Mode disables networking.

Keep `.rookframe/development.json`, `.rookframe/installed-application.json`,
`.rookframe-development/`, `rookframe/development/`,
`.godot/` and staged `dependencies/webrtc_native/lib/` files outside source control.
Preparing again repairs a deleted Godot cache.

## Work on this plugin

Clone this repository. Place the pinned [gd-plug script](https://raw.githubusercontent.com/imjp94/gd-plug/209276d1f00d14b49b74403d9839f29598e9a8eb/addons/gd-plug/plug.gd)
and its [license](https://raw.githubusercontent.com/imjp94/gd-plug/209276d1f00d14b49b74403d9839f29598e9a8eb/LICENSE)
in `addons/gd-plug/`, then run:

```sh
godot --headless --path . --script plug.gd install
python -m unittest discover -s tests -v
godot --headless --editor --path . --quit-after 30
```

Use the actual Godot .NET executable path. Open this project in the editor to
inspect the dock. For World behavior, use the same external author project,
installed application and saved World throughout the change. Keep SDK dependency
upgrades explicit and verify before tagging a release; never move released tags.

