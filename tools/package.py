"""Export only explicitly approved Lite files, never the parent repository."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ".dockerignore", ".gitignore", ".github/workflows/tests.yml", "README.md",
    "Dockerfile", "compose.yaml", "requirements.txt", "requirements-dev.txt",
    "lite_app/__init__.py", "lite_app/hl7.py", "lite_app/main.py", "lite_app/mllp.py", "lite_app/store.py", "lite_app/design.py",
    "lite_app/static/index.html", "lite_app/static/original.css", "lite_app/static/style.css", "lite_app/static/app.js",
    "lite_app/static/design-editor.js", "lite_app/static/design-editor.css",
    "lite_app/static/catalog-editor.js", "lite_app/static/catalog-editor.css",
    "lite_app/static/original-workshop.css", "lite_app/static/original-equipment-profile.css", "lite_app/static/profile-workspace.css",
    "samples/order-glucose.hl7", "tests/test_lite.py", "tools/package.py",
)


def package():
    content = {}
    for name in FILES:
        source = ROOT / name
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise ValueError("Source outside Lite: " + name)
        # All allowlisted files are text; stable newlines keep hashes portable.
        content[name] = source.read_bytes().replace(b"\r\n", b"\n")
    manifest = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(content.items()))
    content["MANIFEST.sha256"] = manifest.encode("ascii")
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    output = destination / "hl7-simulator-lite.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo("hl7-simulator-lite/" + name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Archive integrity check failed")
        assert len(archive.namelist()) == len(content)
        for name, data in content.items():
            assert archive.read("hl7-simulator-lite/" + name) == data
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(f"{output}: {len(content)} verified files; SHA256 {checksum}")
    return output


if __name__ == "__main__":
    package()
