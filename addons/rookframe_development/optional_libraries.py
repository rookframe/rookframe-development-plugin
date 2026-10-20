"""Install an independently selected public helper library; never a World Package."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import urllib.parse
import urllib.request
import zipfile
from authoring_paths import project_member

LIBRARIES = {
    "sdk": ("rookframe/rookframe-sdk", "rookframe/sdk/"),
    "ui-kit": ("rookframe/rookframe-ui-kit", "rookframe/ui/"),
}
MAX_BYTES = 256 * 1024 * 1024


def fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("Library downloads require HTTPS.")
    request = urllib.request.Request(url, headers={"User-Agent": "Rookframe-Development-Plugin"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Library download exceeds 256 MiB.")
    return data


def latest_release(repository: str) -> dict:
    releases = json.loads(fetch(f"https://api.github.com/repos/{repository}/releases?per_page=100"))
    published = [item for item in releases if not item.get("draft") and item.get("published_at")]
    if not published:
        raise ValueError("No published library release is available.")
    # The newest published candidate is intentional: GitHub's /latest endpoint
    # excludes prereleases and can otherwise select an obsolete SDK generation.
    return max(published, key=lambda item: item["published_at"])


def library_files(library: str, release: dict, commit: str, archive: bytes) -> dict[str, bytes]:
    repository, prefix = LIBRARIES[library]
    files = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for member in zipped.infolist():
            parts = PurePosixPath(member.filename).parts
            relative = "/".join(parts[1:])
            if not relative.startswith(prefix):
                if relative not in ("LICENSE", "LICENSE.md", "LICENSE.txt", "THIRD_PARTY_NOTICES.md"):
                    continue
                relative = f".rookframe/libraries/{library}/{relative}"
            if member.is_dir():
                continue
            if (".." in parts or "\\" in member.filename or ":" in member.filename
                    or stat.S_ISLNK(member.external_attr >> 16) or member.file_size > MAX_BYTES):
                raise ValueError("Unsafe path or oversized file in library archive.")
            if relative in files:
                raise ValueError("Duplicate library archive path.")
            data = zipped.read(member)
            if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                pointer = data.decode("ascii")
                oid = re.search(r"^oid sha256:([0-9a-f]{64})$", pointer, re.M)
                size = re.search(r"^size ([0-9]+)$", pointer, re.M)
                if oid is None or size is None or int(size[1]) > MAX_BYTES:
                    raise ValueError("Invalid Git LFS library pointer.")
                source_path = "/".join(parts[1:])
                url = f"https://media.githubusercontent.com/media/{repository}/{commit}/{urllib.parse.quote(source_path)}"
                data = fetch(url)
                if len(data) != int(size[1]) or hashlib.sha256(data).hexdigest() != oid[1]:
                    raise ValueError("Downloaded library asset differs from its Git LFS hash.")
            files[relative] = data
            if sum(map(len, files.values())) > MAX_BYTES:
                raise ValueError("Expanded library exceeds 256 MiB.")
    if not any(name.startswith(prefix) for name in files):
        raise ValueError(f"Release {release['tag_name']} does not contain the {library} source library.")
    return files


def member(project: Path, relative: str) -> Path:
    if (PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts
            or "\\" in relative or ":" in relative):
        raise ValueError("Library files must stay inside real project directories.")
    return project_member(project, relative)


def owned_member(project: Path, library: str, relative: str) -> Path:
    prefix = LIBRARIES[library][1]
    if not relative.startswith((prefix, f".rookframe/libraries/{library}/")):
        raise ValueError("Library inventory contains a path outside this library.")
    return member(project, relative)


def install_files(project: Path, library: str, release: dict, commit: str,
                  files: dict[str, bytes]) -> dict:
    inventory_path = member(project, ".rookframe/optional-libraries.json")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.exists() else {}
    previous = inventory.get(library, {}).get("files", {})
    proposed = {owned_member(project, library, name): data for name, data in files.items()}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    # Compare every existing file before writing. An explicit library update may
    # replace the previous installed release, but never silently erase author edits.
    for name in previous.keys() | files.keys():
        path = owned_member(project, library, name)
        if path.exists():
            current = hashlib.sha256(path.read_bytes()).hexdigest()
            if current not in (previous.get(name), hashes.get(name)):
                raise ValueError(f"Preserve or reconcile your edited file before installing: {name}")
        if name not in files:
            proposed[path] = None
    inventory[library] = {"version": release["tag_name"], "commit": commit,
                          "prerelease": release.get("prerelease", False), "files": hashes}
    proposed[inventory_path] = (json.dumps(inventory, indent=2) + "\n").encode()
    previous_bytes = {path: path.read_bytes() if path.exists() else None for path in proposed}
    try:
        for path, data in proposed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if data is None:
                path.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    pending = Path(stream.name)
                    try:
                        stream.write(data)
                        stream.close()
                        pending.replace(path)
                    finally:
                        pending.unlink(missing_ok=True)
    except OSError:
        for path, data in previous_bytes.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        raise
    return {"library": library, "version": release["tag_name"],
            "prerelease": release.get("prerelease", False), "commit": commit}


def install(project: Path, library: str) -> dict:
    repository, _ = LIBRARIES[library]
    release = latest_release(repository)
    tag = urllib.parse.quote(release["tag_name"], safe="")
    commit = json.loads(fetch(f"https://api.github.com/repos/{repository}/commits/{tag}"))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("GitHub did not resolve an exact library commit.")
    archive = fetch(f"https://api.github.com/repos/{repository}/zipball/{commit}")
    return install_files(project, library, release, commit,
                         library_files(library, release, commit, archive))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--library", choices=tuple(LIBRARIES), required=True)
    args = parser.parse_args()
    try:
        result = install(args.project.resolve(), args.library)
        candidate = " (prerelease)" if result["prerelease"] else ""
        print(f"Installed {result['library']} {result['version']}{candidate}. This optional library is ready to use.")
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
