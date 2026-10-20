"""Package the standalone editor addon for Godot's Install from ZIP workflow."""
from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


def build(output: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    addon = root / "addons/rookframe_development"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(addon.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                archive.write(source, source.relative_to(root).as_posix())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
    print(args.output.resolve())
