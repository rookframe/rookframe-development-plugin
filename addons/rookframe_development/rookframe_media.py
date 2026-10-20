"""Attach nonexecuting release media and carry its exact bytes through publication."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import urllib.parse

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".ogv", ".mp4", ".webm"}


def assets(manifest: dict) -> list[dict]:
    media = manifest.get("media")
    if media is None:
        return []
    if not isinstance(media, dict) or not isinstance(media.get("gallery", []), list):
        raise ValueError("Media requires an object with a gallery array.")
    if media.get("cover") is not None and not isinstance(media["cover"], dict):
        raise ValueError("The cover must be an image object.")
    if any(not isinstance(item, dict) for item in media.get("gallery", [])):
        raise ValueError("Each gallery item must be an object.")
    items = ([media["cover"]] if media.get("cover") else []) + media.get("gallery", [])
    if any(not isinstance(item.get("path"), str) for item in items):
        raise ValueError("Each media item needs a filepath.")
    if len(media.get("gallery", [])) > 12 or len({item["path"] for item in items}) != len(items):
        raise ValueError("Choose one cover and up to 12 distinct gallery items.")
    if (media.get("cover") or {}).get("type", "image") != "image":
        raise ValueError("The cover must be an image.")
    for item in items:
        if not re.fullmatch(r"media/[a-z0-9][a-z0-9._-]{0,193}", item["path"]):
            raise ValueError("Media must use a portable media/filename path.")
        stem = Path(item["path"]).name.split(".")[0]
        if stem in {"con", "prn", "aux", "nul"} or re.fullmatch(r"(?:com|lpt)[0-9]", stem):
            raise ValueError("Media filenames cannot use reserved device names.")
        extension = Path(item["path"]).suffix
        if extension not in (IMAGE_EXTENSIONS if item["type"] == "image" else VIDEO_EXTENSIONS if item["type"] == "video" else set()):
            raise ValueError("Choose PNG, JPEG, WebP, Ogg Theora, MP4 or WebM media.")
        if not isinstance(item.get("caption", ""), str):
            raise ValueError("Media captions must be text.")
        if len(item.get("caption", "")) > 200 or any(ord(c) < 32 for c in item.get("caption", "")):
            raise ValueError("Media captions must be at most 200 characters without control characters.")
        if item.get("url"):
            url = urllib.parse.urlsplit(item["url"])
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
                raise ValueError("Published media requires public HTTPS URLs.")
    if manifest.get("manifestVersion") not in (2, 3):
        raise ValueError("Release media requires Manifest format 2 or 3.")
    return items


def validate_bytes(path: str, data: bytes) -> None:
    extension = Path(path).suffix
    maximum = 16 * 1024 * 1024 if extension in IMAGE_EXTENSIONS else 128 * 1024 * 1024
    signature = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        ".ogv": data.startswith(b"OggS"),
        ".mp4": data[4:8] == b"ftyp",
        ".webm": data.startswith(b"\x1a\x45\xdf\xa3"),
    }.get(extension, False)
    if not 12 <= len(data) <= maximum or not signature:
        raise ValueError(f"{path}: media is too large or does not match its declared format.")


def project_media(project: Path, manifest: dict) -> dict[str, bytes]:
    result = {}
    for item in assets(manifest):
        path = project / item["path"]
        if not path.resolve().is_relative_to(project.resolve()) or path.is_symlink():
            raise ValueError("Media files must remain inside the authoring project.")
        maximum = 16 if item["type"] == "image" else 128
        if path.stat().st_size > maximum * 1024 * 1024:
            raise ValueError(f"{path.name}: media exceeds {maximum} MiB.")
        data = path.read_bytes()
        validate_bytes(item["path"], data)
        result[item["path"]] = data
    return result


def edit(project: Path, source: Path | None, role: str, caption: str = "",
         url: str | None = None, remove: str | None = None, move: int = 0) -> dict:
    path = project / "rookframe.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assets(manifest)
    media = manifest.get("media") or {"gallery": []}
    manifest["media"] = media
    gallery = media.setdefault("gallery", [])
    changed = ["rookframe.json"]
    if remove:
        if move:
            index = next((i for i, item in enumerate(gallery) if item["path"] == remove), None)
            if index is None:
                raise ValueError("Select a gallery item to reorder; the cover always appears first.")
            target = max(0, min(len(gallery) - 1, index + move))
            gallery.insert(target, gallery.pop(index))
        elif (media.get("cover") or {}).get("path") == remove:
            del media["cover"]
        else:
            gallery[:] = [item for item in gallery if item["path"] != remove]
    elif source:
        extension = source.suffix.lower()
        if source.stat().st_size > (16 if extension in IMAGE_EXTENSIONS else 128) * 1024 * 1024:
            raise ValueError("Image files are limited to 16 MiB; videos to 128 MiB.")
        data = source.read_bytes()
        validate_bytes(source.name.lower(), data)
        if role == "cover" and extension not in IMAGE_EXTENSIONS:
            raise ValueError("Choose an image for the cover.")
        filename = hashlib.sha256(data).hexdigest() + extension
        relative = "media/" + filename
        public = url
        item = {"type": "image" if extension in IMAGE_EXTENSIONS else "video",
                "path": relative, "caption": caption}
        if public:
            item["url"] = public
        if role == "cover":
            media["cover"] = item
            gallery[:] = [entry for entry in gallery if entry["path"] != relative]
        else:
            gallery[:] = [entry for entry in gallery if entry["path"] != relative]
            if (media.get("cover") or {}).get("path") == relative:
                raise ValueError("That image is already the cover.")
            gallery.append(item)
        manifest["manifestVersion"] = max(2, manifest.get("manifestVersion", 1))
        assets(manifest)
        destination = project / relative
        if not destination.resolve().is_relative_to(project.resolve()) or destination.is_symlink():
            raise ValueError("Media files must remain inside the authoring project.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        (destination.parent / ".gdignore").touch()
        if destination.exists() and destination.read_bytes() != data:
            raise ValueError("An existing media file has changed. Restore it before attaching this file.")
        if not destination.exists():
            destination.write_bytes(data)
        changed.extend([relative, "media/.gdignore"])
    else:
        raise ValueError("Choose a file to attach or an existing media path to remove/reorder.")
    assets(manifest)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return {"status": "media_updated", "media": media, "changedPaths": changed}
