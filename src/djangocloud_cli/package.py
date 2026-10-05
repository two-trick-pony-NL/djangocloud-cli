"""Pack the project folder into the .tar.gz that `deploy` uploads.

Which files go in: when the folder is a git repository, exactly what git would consider part of the project
(tracked files plus untracked ones that aren't ignored), so `.gitignore` is respected for free. Otherwise a
built-in list of things that never belong in a deploy. Either way secrets and junk are always left out.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path

from . import link

# Never uploaded, whatever .gitignore says: secrets, virtualenvs, VCS data, caches and local databases.
ALWAYS_SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".idea", ".vscode",
                    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", "staticfiles"}  # fmt: skip
ALWAYS_SKIP_FILES = {".DS_Store"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".sqlite3", ".sqlite", ".pem", ".key")


class PackageError(Exception):
    pass


def _is_secret_env(name: str) -> bool:
    return name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")))


def _skip(rel: Path) -> bool:
    if rel.parts[0] == link.DIR:  # our own folder is handled separately (only config.json goes up)
        return True
    if any(part in ALWAYS_SKIP_DIRS for part in rel.parts[:-1]):
        return True
    name = rel.name
    return name in ALWAYS_SKIP_FILES or name.endswith(SKIP_SUFFIXES) or _is_secret_env(name)


def _git_files(root: Path) -> list[Path] | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True, timeout=30,
        )  # fmt: skip
        top = (
            subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, check=True, timeout=30
            )
            .stdout.decode()
            .strip()
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if Path(top).resolve() != root.resolve():
        return None  # root is a subfolder of a bigger repo: fall back to the plain walk
    return [Path(p) for p in done.stdout.decode().split("\0") if p]


def _walk(root: Path) -> list[Path]:
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ALWAYS_SKIP_DIRS]
        found += [Path(folder, f).relative_to(root) for f in files]
    return found


def collect(root: Path) -> list[Path]:
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk(root)
    files = sorted({p for p in candidates if (root / p).is_file() and not (root / p).is_symlink() and not _skip(p)})
    config = Path(link.DIR) / link.FILE
    if (root / config).is_file():
        files.append(config)  # the server reads the build settings from here
    return files


def build(root: Path, *, max_bytes: int = 100 * 1024 * 1024) -> tuple[bytes, int]:
    """Returns (tar.gz bytes, file count). Deterministic: the same files always give the same bytes."""
    files = collect(root)
    if not any(p.name == "manage.py" for p in files):
        raise PackageError(
            "This folder doesn't look like a Django project (no manage.py). Run the command from your project folder."
        )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel in files:
            info = tar.gettarinfo(str(root / rel), arcname=rel.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            with (root / rel).open("rb") as handle:
                tar.addfile(info, handle)
    import gzip

    data = gzip.compress(buffer.getvalue(), mtime=0)
    if len(data) > max_bytes:
        raise PackageError(
            f"The project is {len(data) // (1024 * 1024)} MB compressed; the limit is {max_bytes // (1024 * 1024)} MB. "
            "Check for large files (data, media, build output) that should be in .gitignore."
        )
    return data, len(files)
