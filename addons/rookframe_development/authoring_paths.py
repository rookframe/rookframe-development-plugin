"""Keep authoring writes inside ordinary project directories on Python 3.10+."""
from pathlib import Path
import stat


def is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def project_member(project: Path, relative: str) -> Path:
    project = project.absolute()
    path = project / relative
    if not path.resolve().is_relative_to(project.resolve()):
        raise ValueError("Authoring output must remain inside the project.")
    for candidate in (path, *path.parents):
        if candidate == project:
            break
        if is_link(candidate):
            raise ValueError("Authoring output must use real project directories, without links or junctions.")
    return path
