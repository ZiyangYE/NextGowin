"""Package the binary runtime from a combined NextGowin build."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package(combined, output):
    if combined.resolve() == output.resolve():
        raise ValueError("Use a distinct output path")
    files = {}
    with zipfile.ZipFile(combined) as source:
        for entry in source.namelist():
            if entry.startswith("bin/") and not entry.endswith("/"):
                files[entry] = source.read(entry)
    for required in ("bin/nextgowin.exe", "bin/nextgowin_core.dll", "bin/env_manifest.json"):
        if required not in files:
            raise ValueError(f"Missing runtime component: {required}")
    for path in [ROOT / "VERSION", ROOT / "README.md", ROOT / "README.en.md", ROOT / "LICENSE", *(ROOT / "docs").rglob("*")]:
        if path.is_file():
            files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    files["SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files)
    ).encode("ascii")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("combined", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    package(args.combined, args.output)
