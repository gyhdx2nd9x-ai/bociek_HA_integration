"""Archive and component hash helpers for local builds."""

import hashlib
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".venv",
    ".ha-venv",
    ".ha-lab",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
    "dist",
    "build",
    "local",
}


def sources(root):
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not any(
            part in EXCLUDED or part.endswith(".egg-info") for part in p.relative_to(root).parts
        )
        and p.name not in {".env", "ha-snapshot.json", ".DS_Store", "ui-session.json"}
        and not p.name.endswith(".bundle")
        and not (p.parent == ROOT / "examples" and p.name.startswith("user_inventory"))
        and p.suffix != ".pyc"
    )


def component_tree_sha256(component):
    digest = hashlib.sha256()
    for file in sources(component):
        digest.update(file.relative_to(component).as_posix().encode() + b"\0" + file.read_bytes())
    return digest.hexdigest()


def archive(path, paths, prefix="", name_for=None, *, immutable=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for file in paths:
            name = prefix + (name_for(file) if name_for else file.relative_to(ROOT).as_posix())
            info = zipfile.ZipInfo(name, (2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            output.writestr(info, file.read_bytes())
    content = buffer.getvalue()
    if immutable and path.exists() and path.read_bytes() != content:
        raise ValueError(f"Release already exists with different bytes: {path.name}")
    path.write_bytes(content)
    with zipfile.ZipFile(path) as check:
        assert check.testzip() is None
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
