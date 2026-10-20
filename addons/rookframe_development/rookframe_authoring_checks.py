"""Read-only Publisher input checks and the shared production verifier process."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
import re
import subprocess

# The Godot exporter runs on the authoring host. This is not a Package OS target.
RESOURCE_EXPORT = "package"
EXPORT_PLATFORMS = {"macOS", "Windows Desktop", "Linux"}


def configured_profiles(project: Path) -> list[str]:
    config = configparser.ConfigParser(interpolation=None, strict=True)
    config.read(project / "export_presets.cfg")
    profiles = []
    for section in config.sections():
        if not re.fullmatch(r"preset\.\d+", section):
            continue
        values = config[section]
        name = values.get("name", "").strip('"')
        if name != RESOURCE_EXPORT:
            continue
        if name in profiles:
            raise RuntimeError(f"EXPORT.DUPLICATE: {name} is configured more than once.")
        if values.get("platform", "").strip('"') not in EXPORT_PLATFORMS:
            raise RuntimeError(f"EXPORT.PLATFORM: {name} must use the local Godot desktop exporter for the shared PCK, not an app export.")
        if values.get("script_export_mode") != "0":
            raise RuntimeError(f"EXPORT.SCRIPT: {name} must export textual GDScript (script_export_mode=0).")
        if values.get("dedicated_server", "false") != "false":
            raise RuntimeError("EXPORT.CONTENTS: The shared Package export must retain graphical resources (dedicated_server=false).")
        features = set(values.get("custom_features", "").strip('"').split(","))
        if not {"s3tc", "bptc", "etc2", "astc"}.issubset(features):
            raise RuntimeError("EXPORT.TEXTURES: The shared Package export must include s3tc,bptc,etc2,astc texture formats.")
        if values.get("encrypt_pck", "false") != "false" or values.get("encrypt_directory", "false") != "false":
            raise RuntimeError(f"EXPORT.ENCRYPTION: {name} must remain inspectable.")
        profiles.append(name)
    if not profiles:
        raise RuntimeError("EXPORT.MISSING: Run init to add the single package resource export preset; Packages have no OS targets.")
    return profiles


_application: Path | None = None


def configure_application(project: Path, explicit: Path | None = None) -> None:
    global _application
    config_path = project / ".rookframe/development.json"
    config = json.loads(config_path.read_text()) if config_path.is_file() else {}
    selected = explicit or os.environ.get("ROOKFRAME_APP") or config.get("rookframe")
    if selected:
        application = Path(selected).expanduser().resolve()
        if application.suffix.lower() == ".app":
            application /= "Contents/MacOS/Rookframe"
        if not application.is_file():
            raise RuntimeError(f"CHECK.ROOKFRAME: Selected Rookframe application does not exist: {application}")
        _application = application
        return
    candidates = []
    if sys.platform == "win32":
        candidates.extend(Path(os.environ.get(key, "")) / "Rookframe/Rookframe.exe"
                          for key in ("ProgramFiles", "LOCALAPPDATA"))
    elif sys.platform == "darwin":
        candidates.extend([Path("/Applications/Rookframe.app/Contents/MacOS/Rookframe"),
                           Path.home() / "Applications/Rookframe.app/Contents/MacOS/Rookframe"])
    found = shutil.which("rookframe")
    if found:
        candidates.append(Path(found))
    _application = next((p.resolve() for p in candidates if p.is_file()), None)
    if _application is None:
        raise RuntimeError("CHECK.ROOKFRAME: Select your installed Rookframe with --rookframe or in the Development World dock.")


def verify(*arguments: str) -> dict:
    if _application is None:
        raise RuntimeError("CHECK.ROOKFRAME: Select your installed Rookframe before checking a Package.")
    mode, path, *rest = map(str, arguments)
    with tempfile.TemporaryDirectory(prefix="rookframe-check-") as directory:
        report = Path(directory) / "report.json"
        # Supported checks finish synchronously during _Ready. Older applications
        # ignore the command; stock --quit keeps them from running their menu forever.
        command = [str(_application), "--headless", "--quit", "--",
                   "--package-check=" + mode, "--input=" + str(Path(path).resolve()),
                   "--report=" + str(report)]
        if rest:
            command.append("--profile=" + rest[0])
        process = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if not report.is_file():
            raise RuntimeError(f"CHECK.ROOKFRAME: {_application} did not return a Package check report. "
                               "Select a current Rookframe release in Development World configuration or with --rookframe.\n"
                               + process.stdout + process.stderr)
        result = json.loads(report.read_text(encoding="utf-8"))
        if process.returncode or result.get("status") == "rejected":
            raise RuntimeError("CHECK.PRODUCTION: " + json.dumps(result))
        return result

