"""Publish one coherent Editable Package declaration after compilation."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from time import monotonic

from authoring_paths import project_member
from rookframe_compiled_scenery import (
    ASSETS, EDITABLE, INDEX, Compilation, application_pack, compile_scenery, fingerprint, inputs, jobs,
    replace_bytes,
)
from rookframe_package_data import (
    DEV_DATA, INDEX_PATH, development_lock, package_root, prepare_development_data, wire_bytes,
)


SCHEMA = "rookframe-development-declarations-v1"
DECLARATIONS = "rookframe/development/package.rookframe-dev"
CONFIG = ".rookframe/development.json"
MEMBER = re.compile(r"_rookframe_scenery/assets/[0-9a-f]{64}\.rfscenery")
RENDERERS = ("mobile", "forward_plus", "gl_compatibility")
APPLICATION_SCENE = "res://rookframe/application/ApplicationRoot.tscn"
DEVELOPMENT_ARGUMENT = f"--development=res://{CONFIG}"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return default


def current_index(destination: Path, key: str) -> Compilation | None:
    try:
        cache = read_json(project_member(destination, "cache.json"), {})
        if cache.get("key") != key:
            return None
        result = Compilation(**cache["result"])
        for path, digest in result.members.items():
            if path != INDEX and MEMBER.fullmatch(path) is None:
                return None
            if sha256(project_member(destination, path).read_bytes()).hexdigest() != digest:
                return None
        if bool(result.declaration) != bool(result.members):
            return None
        if result.declaration and result.declaration.get("indexSha256") != result.members.get(INDEX):
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def owned_snapshot(project: Path) -> tuple[Path, dict]:
    path = project_member(project, DECLARATIONS)
    value = read_json(path, {})
    if not isinstance(value, dict) or (path.exists() and value.get("schema") != SCHEMA):
        raise ValueError(f"DEVELOPMENT.COLLISION: {path} is an author-owned file")
    return path, value


def application_generation(pack: Path | None) -> list:
    if pack is None:
        return []
    stat = pack.stat()
    return [str(pack), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


@contextmanager
def publication_lock(path: Path):
    with ExitStack() as locks:
        deadline = monotonic() + 150
        while True:
            try:
                locks.enter_context(development_lock(path))
                break
            except OSError as error:
                # Windows' blocking byte lock can time out before the current
                # Godot worker finishes. Retry acquisition, never publication.
                if os.name != "nt" or error.errno not in {errno.EACCES, errno.EDEADLK} or monotonic() >= deadline:
                    raise
        yield


def prepare_editable_package(project: Path, godot: Path, engine: str, package_id: str | None,
                             *, paths: tuple[str, ...] = (), application: Path | None = None,
                             renderer: str | None = None, force_lods: bool = False) -> dict:
    if package_id is None and not project_member(project, DECLARATIONS).exists():
        return {}
    generated = project_member(project, EDITABLE)
    generated.mkdir(parents=True, exist_ok=True)
    with publication_lock(project_member(generated, "publish.lock")):
        config_path = project_member(project, CONFIG)
        config_bytes = config_path.read_bytes()
        config = json.loads(config_bytes)
        if config.get("developPackage") != package_id:
            raise ValueError("SCENERY.STALE: source: Editable Package selection changed during preparation")
        declaration, previous = owned_snapshot(project)
        if package_id is None:
            if "compiledScenery" in previous or "packageData" in previous:
                for section in ("compiledScenery", "packageData"):
                    previous.pop(section, None)
                previous.update(generation=previous["generation"] + 1, changedPaths=[])
                replace_bytes(declaration, wire_bytes(previous) + b"\n")
            return {}
        if application is None and config.get("rookframe"):
            application = Path(config["rookframe"])
            if application.suffix.lower() == ".app":
                application /= "Contents/MacOS/Rookframe"
        return _prepare(project, godot, engine, package_id, paths, application,
                        config_bytes, declaration, previous, renderer, force_lods)


def _prepare(project: Path, godot: Path, engine: str, package_id: str, paths: tuple[str, ...],
             application: Path | None, config: bytes, declaration: Path, previous: dict,
             renderer: str | None, force_lods: bool) -> dict:
    source = project_member(project, "rookframe.json")
    manifest_bytes = source.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("id") != package_id:
        raise ValueError("DEVELOPMENT.IDENTITY: Save settings again after changing the Package identity")
    root = package_root(project, package_id)
    files = inputs(root, "source")
    vegetation = [job for job in jobs(manifest, files, "source") if job["target"] in {"grass", "trees"}]
    settings_path = project_member(project, "project.godot")
    settings = settings_path.read_bytes()
    pack = application_pack(application)
    pack_key = application_generation(pack)
    key = fingerprint(manifest_bytes, engine, files, wire_bytes(pack_key) + settings)
    destination = project_member(project, f"{EDITABLE}/{package_id}")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pending-", dir=destination) as pending:
        stage = Path(pending)
        result = current_index(destination, key)
        if result is None:
            result = compile_scenery(godot, engine, project, root, stage, manifest, "", "source",
                                     files, settings, application_pack=pack)

        def require_current():
            if (project_member(project, CONFIG).read_bytes() != config
                    or source.read_bytes() != manifest_bytes or inputs(root, "source") != files
                    or settings_path.read_bytes() != settings or application_generation(pack) != pack_key):
                raise ValueError("SCENERY.STALE: source: scenery inputs changed during preparation")

        pointer = {}
        if result.declaration:
            pointer = {**result.declaration, "packageId": package_id,
                       "root": f"res://{EDITABLE}/{package_id}/"}
        prefix = f"res://{root.relative_to(project).as_posix()}/"
        manifest_text = manifest_bytes.decode("utf-8")
        require_current()
        try:
            data = prepare_development_data(project, godot, engine, package_id)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            require_current()
            return _refresh_native_paths(project, package_id, declaration, previous,
                                         manifest_text, prefix, paths, pointer, error)
        require_current()
        changed = sorted({path for path in (*paths, *data["changedPaths"]) if path.startswith(prefix)})
        snapshot = {"schema": SCHEMA, "manifest": manifest_text, "changedPaths": changed,
                    "generation": previous.get("generation", 0) + 1, "packageData": data["packageData"]}
        if pointer:
            snapshot["compiledScenery"] = pointer
        updated = (previous.get("manifest") != manifest_text or previous.get("packageData") != data["packageData"]
                   or previous.get("compiledScenery", {}) != pointer or bool(changed))

        # All validated immutable members become visible before the atomic index
        # swap. The importer observes the declaration only after the matching index.
        for member in result.members:
            if member != INDEX and (stage / member).is_file():
                replace_bytes(project_member(destination, member), (stage / member).read_bytes())
        require_current()
        index = project_member(destination, INDEX)
        prior_index = index.read_bytes() if index.is_file() else None
        index_replaced = False
        if (stage / INDEX).is_file():
            index_replaced = replace_bytes(index, (stage / INDEX).read_bytes())
        try:
            require_current()
            if updated:
                replace_bytes(declaration, wire_bytes(snapshot) + b"\n")
        except (OSError, ValueError):
            if index_replaced:
                if prior_index is None:
                    index.unlink(missing_ok=True)
                else:
                    replace_bytes(index, prior_index)
            raise
        replace_bytes(project_member(destination, "cache.json"), wire_bytes({"key": key, "result": vars(result)}))
        # A reader holding an old declaration may have to retry against the
        # current index. Keep obsolete files until the new declaration exists.
        if not result.declaration:
            project_member(destination, INDEX).unlink(missing_ok=True)
        assets = project_member(destination, ASSETS)
        for path in assets.glob("*.rfscenery"):
            relative = path.relative_to(destination).as_posix()
            if MEMBER.fullmatch(relative) and relative not in result.members:
                path.unlink(missing_ok=True)
        lods = {}
        lods_updated = False
        if vegetation:
            # The native pointer is already published. The app can now prepare
            # its own derivatives against that exact source generation.
            mapped = {asset["content"] for asset in result.vegetation_sources}
            unmapped = any(job["content"] not in mapped for job in vegetation)
            lod_key = sha256(wire_bytes([engine, pack_key, renderer, vegetation,
                                        result.vegetation_sources, key if unmapped else None])).hexdigest()
            receipt = project_member(destination, "lod-preparation.json")
            if not force_lods:
                lods = current_lod_receipt(receipt, lod_key)
            if not lods:
                lods = prepare_client_lods(godot, project, pack, renderer, stage / "application-lods.json")
                require_current()
                if lods.get("status") in {"ready", "source_fallback"} and (
                        lods.get("packageId") != package_id or renderer and lods.get("renderer") != renderer):
                    lods = {"status": "failed", "diagnostic": "Rookframe reported LODs for a different Package or renderer."}
                replace_bytes(receipt, wire_bytes({"inputs": lod_key, "report": lods}) + b"\n")
                lods_updated = True
    return {**data, "compiledScenery": pointer, "updated": updated,
            **({"lodPreparation": lods, "lodPreparationUpdated": lods_updated} if lods else {}),
            "scenery": {"counts": result.counts, "fallbacks": result.fallbacks,
                        "vegetationSources": result.vegetation_sources}}


def current_lod_receipt(path: Path, input_key: str) -> dict:
    try:
        cached = read_json(path, {})
    except (OSError, ValueError):
        return {}
    if isinstance(cached, dict) and cached.get("inputs") == input_key:
        report = cached.get("report")
        if isinstance(report, dict) and report.get("status") in {"ready", "source_fallback"}:
            return report
    return {}


def prepare_client_lods(godot: Path, project: Path, pack: Path | None,
                        renderer: str | None, report_path: Path) -> dict:
    if pack is None:
        return {"status": "failed", "diagnostic": "Select an installed Rookframe application to prepare Editable LODs."}
    rendering = ["--rendering-method", renderer] if renderer else []
    command = [str(godot), "--path", str(project), "--main-pack", str(pack), "--scene", APPLICATION_SCENE,
               *rendering, "--resolution", "64x64", "--position", "-10000,-10000",
               "--", "--prepare-editable-lods", DEVELOPMENT_ARGUMENT, f"--report={report_path}"]
    # This is an ordinary graphics process without the editor debugger or
    # the Village Trial development-launch override.
    environment = {name: value for name, value in os.environ.items() if name != "ROOKFRAME_VILLAGE_TRIAL"}
    try:
        result = subprocess.run(command, env=environment, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=1800)
        report = read_json(report_path)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"status": "failed", "diagnostic": f"Rookframe Editable LOD preparation failed. {error}"}
    if isinstance(report, dict) and report.get("status") in {"ready", "source_fallback", "failed"}:
        if not result.returncode or report["status"] == "failed":
            return report
    diagnostic = next((line for line in (result.stderr + result.stdout).splitlines()
                       if line.startswith("ERROR:")), "")
    return {"status": "failed", "diagnostic": "Rookframe did not complete --prepare-editable-lods. "
            f"Use an application with Editable LOD preparation support. {diagnostic}".strip()}


def _refresh_native_paths(project: Path, package_id: str, declaration: Path, previous: dict,
                          manifest: str, prefix: str, paths: tuple[str, ...], pointer: dict,
                          error: Exception) -> dict:
    data = previous.get("packageData")
    index = project_member(project, f"{DEV_DATA}/{package_id}/{INDEX_PATH}")
    if (not isinstance(data, dict) or data.get("packageId") != package_id or not index.is_file()
            or sha256(index.read_bytes()).hexdigest() != data.get("indexSha256")):
        raise error
    changed = sorted({path for path in paths if path.startswith(prefix) and not path.lower().endswith(".json")})
    same_scenery = previous.get("compiledScenery", {}) == pointer
    updated = bool(changed or previous["manifest"] != manifest or not same_scenery)
    if updated:
        snapshot = {**previous, "manifest": manifest, "changedPaths": changed,
                    "generation": previous["generation"] + 1}
        if not same_scenery:
            snapshot.pop("compiledScenery", None)
        replace_bytes(declaration, wire_bytes(snapshot) + b"\n")
    return {"dataError": str(error), "updated": updated}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--godot", type=Path, required=True)
    parser.add_argument("--changes", type=Path)
    parser.add_argument("--renderer", choices=RENDERERS)
    args = parser.parse_args()
    try:
        project = args.project.resolve()
        config = read_json(project_member(project, CONFIG), {})
        changes = tuple(read_json(args.changes, [])) if args.changes is not None else ()
        engine = subprocess.check_output([str(args.godot), "--version"], text=True, timeout=30).strip()
        result = prepare_editable_package(project, args.godot, engine, config.get("developPackage"),
                                          paths=changes, renderer=args.renderer)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
