# Rookframe Development Plugin

Godot editor tools for authoring Packages and running a reusable Development
World. This repository owns the editor plugin independently of the
[Rookframe SDK](https://github.com/rookframe/rookframe-sdk) and application.

## Install and run a World

Use stock Godot 4.7.2 .NET, Python 3.10+, the .NET 10 runtime and a matching
installed Rookframe application. The application must support Development Worlds
without an Editable Package. The first compatible closed-alpha candidate is
`0.1.0-alpha.1003101+c8e443206a4f` (build sequence `1003101` in
`build-manifest.json`), available through the existing closed-alpha desktop
distribution. Keep its complete application directory.

Download [the standalone plugin ZIP](https://github.com/rookframe/rookframe-development-plugin/releases/download/v0.4.0-dev.3/rookframe-development-0.4.0-dev.3.zip).
Install it through Godot's **AssetLib → Install from ZIP**,
then enable **Rookframe Development** in **Project Settings → Plugins**.
The ZIP contains only `addons/rookframe_development/`. Running a World does not
require gd-plug, Git, a local SDK, the UI Kit or a Package in the project.

1. In the **Rookframe** dock, choose **Set up World…** and select the installed Rookframe executable.
2. Keep or enter a Development World name and supply a public HTTPS System Manifest
   URL. Add other published Package Manifest URLs only when needed.
3. Choose **Save and prepare World**, then **Save and reopen editor** if requested.
4. Wait for **Ready**, then use normal Godot **Play (F5)** and **Stop (F8)**.
   Reuse the same World name to retain its data.

Godot loads the installed application's SDK and UI resources when running the
World. Local authoring copies are optional helpers.

## Optional authoring libraries

All Package kinds can be created, registered, checked and built without a local
SDK or UI Kit. The plugin owns authoring commands; the installed Rookframe
application performs production checks. Initial scenes use ordinary Godot nodes.

Under **Package settings → Optional libraries…**, choose the SDK or UI Kit
download when you want those helpers. The same actions remain under
**Project → Tools**. Each action
installs only the selected library over HTTPS, without gd-plug or manual Git
setup. Latest means the newest published release, including prereleases; the
result names the installed version. Updates preserve edited library files and
report conflicts instead of overwriting them. Neither action changes your
Package Manifest, other library selection, or saved Development World.

Projects already using gd-plug can keep it for their SDK and UI Kit dependencies.
Keep their pins and normal dependency installation command. When installing the
standalone plugin ZIP, remove the old plugin dependency declaration so a later
gd-plug install does not replace the addon with the older release.
Ignore `addons/rookframe_development/` in the Package's source control.

In **Project Settings → Plugins**, disable **Rookframe Package SDK** if an older
SDK enables it, then enable **Rookframe Development**. Published SDK versions
remain immutable, including their old plugin
files; those files are inactive after this switch.

Existing Worlds and `.rookframe/development.json` are reused. The new plugin's
**Save and prepare** action refreshes launch settings; it may request one editor
restart. It also migrates the old plugin's placeholder main-scene path while
preserving an author-selected main scene.

## Author Packages

Choose **Create Package…** in the Rookframe dock:

- **Content pack** prepares folders for scenes and Resources without code.
- **Extension** adds behavior to an existing World.
- **System** defines a game's Actors, rules and shared World data.

Extension and System creation provide an ordinary GDScript behavior entry point.
An editable UI window is an explicit optional choice. Neither local library is required.
Creation opens a short authoring guide and selects the content folder or opens
the behavior script. The guide remains available from the Package settings menu.

Author under `rookframe/packages/<package-id>/`: keep models and images in
`assets/`, scenes and Resources in `content/`, behavior in `logic/`, and UI in
`ui/`. Use Godot's ordinary FileSystem, Scene tree and editors. Imported models
become authored inherited scenes; author collision shapes yourself.

Select a scene root (or a Theme/System Resource), then open the **Rookframe Content**
dock beside the Inspector. It follows the object being inspected and names the
source you are editing. Choose its explained type and **Add to Package**. The
name and optional Library thumbnail belong to that source. Use **Choose image…**
to assign an image from the Package; **Clear** removes only that assignment.
Godot properties stay in the regular Inspector. Identity is automatic; it stays stable
through property edits and native FileSystem moves. Save normally with Godot.
**Save As** makes a separate source and leaves the original Content declaration
intact. Add the copy explicitly if it should also be Content.

Declared types are fixed for that identity. Removing a declaration leaves its
source file and warns about existing World references. Undo restores the same
identity; adding again creates a new one. System Resources require the target
System's UUID, contract, content type and compatible version expression.

In the dock's **Package settings** menu, open **Package settings… → Code & UI**
to assign Extension/System entry scripts through native GDScript resource pickers.
Click a script to open it; finish settings with **Save** or **Cancel** to return
to editing. **New UI…** creates an ordinary editable scene and entry script, with
explicit device coverage. Reassigning devices previews the affected
assignments. A Content pack can opt into these tools through **Advanced → Add
code or UI to this Package…**.
Missing Content sources can be repaired in the settings' **Missing sources** tab.

**Check** validates saved sources; **Build…** produces a checked archive using the
installed application. Save unfinished scenes first. Checking does not stop a
running World. Validation results link sources to their normal Godot editor.
Package publication artwork remains available through the media CLI in
[publication instructions](PUBLICATION.md).

## Run a Development World

1. Open the author project in the stock Godot .NET editor matching the installed
   Rookframe application's engine, OS and architecture.
2. Choose **Set up World…** or **Configure World…**. Select the installed Rookframe executable,
   the existing World name and published Package Manifest URLs. To live-edit
   a Package, enable **Edit this project's Package**. It is selected by default
   for existing Package projects and saved selections are preserved. An editable
   optional Package also needs a published System Manifest URL. An editable
   System Extension supplies the System itself.
3. Choose **Save and prepare World** and reopen the editor once if requested.
4. Use normal **Play (F5)**. Save scripts/scenes or reimport models in the same session.
   Use Godot's Debugger and Output to inspect errors.

Reuse the World name to preserve its data. SDK Edition, Package identity,
Implementation entry point and Package selection require a session restart.
An existing source World needs its matching Editable Package to reopen. Restore
that selection or choose another World name when running without it; the saved
source World is preserved.
Only the Editable Package bypasses admission. Other Packages use normal public
HTTPS acquisition. Development Mode disables networking.

Keep `.rookframe/development.json`, `.rookframe/installed-application.json`,
`.rookframe-development/`, `rookframe/development/`,
`.godot/` and staged `dependencies/webrtc_native/lib/` files outside source control.
Preparing again repairs a deleted Godot cache.

## Work on this plugin

Build the standalone addon archive outside the repository:

```sh
python tools/build-plugin.py --output /path/to/rookframe-development.zip
```

The addon and its Package tools have no SDK or UI Kit dependency. Run:

```sh
python -m unittest discover -s tests -v
godot --headless --editor --path . --quit-after 30
```

Use the actual Godot .NET executable path. Open this project in the editor to
inspect the dock. For World behavior, use the same external author project,
installed application and saved World throughout the change. Keep SDK dependency
upgrades explicit and verify before tagging a release; never move released tags.

