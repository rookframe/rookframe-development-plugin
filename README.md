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

Download [the standalone plugin ZIP](https://github.com/rookframe/rookframe-development-plugin/releases/download/v0.3.1-rc.1/rookframe-development-0.3.1-rc.1.zip).
Install it through Godot's **AssetLib → Install from ZIP**,
then enable **Rookframe Development** in **Project Settings → Plugins**.
The ZIP contains only `addons/rookframe_development/`. Running a World does not
require gd-plug, Git, a local SDK, the UI Kit or a Package in the project.

1. Open the dock's **Development** tab and choose the installed Rookframe executable.
2. Keep or enter a Development World name and supply a public HTTPS System Manifest
   URL. Add other published Package Manifest URLs only when needed.
3. Choose **Save and prepare**, then **Save and reopen editor** if requested.
4. Wait for preparation and imports to finish, then choose **Run development World**
   or use normal Play. Reuse the same World name to retain its data.

Godot loads the installed application's SDK and UI resources when running the
World. Local authoring copies are optional helpers.

## Optional authoring libraries

All Package kinds can be created, registered, checked and built without a local
SDK or UI Kit. The plugin owns authoring commands; the installed Rookframe
application performs production checks. Initial scenes use ordinary Godot nodes.

In **Package → Optional libraries**, download the SDK or UI Kit independently.
Their installed versions are shown beside each action. Alternatively, under
**Project > Tools**, choose **Rookframe: Install latest SDK** or
**Rookframe: Install latest UI Kit** when you want those helpers. Each action
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

Choose **Create Package** in the **Package** tab:

- **Content pack** prepares folders for reusable Miniatures, Props, Wall Styles,
  Surface Finishes and Themes.
- **Extension** adds an author-owned behavior script for optional World features.
- **System Extension** prepares the entry point for your game's rules and Actor
  workflows. This is a skeleton; author the actual game behavior yourself.

Extensions and Systems can include a starter Control scene and Presentation.
None of these choices installs the SDK or UI Kit.

Use **Open folder** to find `rookframe/packages/<package-id>/`. Import source
assets into `assets/` using Godot, and save your scenes and Resources in `content/`.
The folders are suggestions; all Package resources must remain in its namespace.
Author meshes, materials and collision in Godot's normal tools.

Select the scene root or Resource, then choose **Make available in Rookframe**
in the Inspector. Choose a Content type, read its requirements and enter its
display name. **Add to Package** creates its identity once. The Package list
selects these same properties; names and previews always belong to the selected
item. Use a native Theme Resource for Theme Content. Wall and finish settings
appear on their scene root; the plugin never generates their geometry or colliders.

Save Package properties with Godot's normal Save or the dock's **Save** button.
Undo/Redo preserves Content identities. Moving files through Godot updates their
declarations; copied scenes begin undeclared. **Remove from Package** leaves files
in place. Missing files can be located without changing identity. Conflicting
external Manifest edits require copying the draft or reloading; unsaved Package
drafts are also retained under `.godot/` for recovery after closing.

**Behavior and UI** opens your actual source. Add a Presentation independently of
behavior, then choose its device experiences. Reassigning an experience previews
the affected declarations and saves them together. Existing authored scripts are
never regenerated. Source changes and richer UI composition use ordinary Godot
scenes, Resources and scripts; the optional SDK supplies Rookframe domain APIs.
The API reference links from the dock identify revision requirements. System
authors also get a direct Actor and System data guide. Presentation list labels
follow their source filenames; changing coverage preserves their identity.

**Details** edits Package-wide name and compatibility while keeping identity
read-only. **Release details** edits the version and optional cover/gallery.
Captions apply to the selected media item. See [publication instructions](PUBLICATION.md)
for the separate publication workflow.

**Project → Tools → Rookframe: Check Package** validates the author project.
**Build Package** produces a local archive and offers **Show archive**. These
background commands use the installed application as the verifier. Save other
modified scene tabs before checking or building; neither action publishes files.

## Run a Development World

1. Open the author project in the stock Godot .NET editor matching the installed
   Rookframe application's engine, OS and architecture.
2. Open the **Development** tab. Select the installed Rookframe executable,
   the existing World name and published Package Manifest URLs. To live-edit
   a Package, enable **Edit this project's Package**. It is selected by default
   for existing Package projects and saved selections are preserved. An editable
   optional Package also needs a published System Manifest URL. An editable
   System Extension supplies the System itself.
3. Choose **Save and prepare** and reopen the editor once if requested.
4. Run the World. Save scripts/scenes or reimport models in the same session.
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
ROOKFRAME_GODOT=/path/to/godot python -m unittest discover -s tests -v
godot --headless --editor --path . --quit-after 30
```

On PowerShell, set `$env:ROOKFRAME_GODOT` before running Python. Use the actual
Godot .NET executable path so the native resource test runs. Open this project in the editor to
inspect the dock. For World behavior, use the same external author project,
installed application and saved World throughout the change. Keep SDK dependency
upgrades explicit and verify before tagging a release; never move released tags.

