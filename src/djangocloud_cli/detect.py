"""Fill in the build settings from what is already in the project, so a first deploy needs no configuration.

Everything found here is only a starting value written to `.djangocloud/config.json`; from then on that file is
the source of truth and can be edited by hand.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

SKIP = {".venv", "venv", "env", "node_modules", ".git", "site-packages", "staticfiles"}


def _walk(root: Path, name: str, depth: int = 3):
    for path in sorted(root.rglob(name)):
        rel = path.relative_to(root)
        if len(rel.parts) <= depth and not (set(rel.parts) & SKIP):
            yield rel


def wsgi_module(root: Path) -> str:
    """e.g. config.wsgi:application, from the shallowest wsgi.py that defines `application`."""
    for rel in sorted(_walk(root, "wsgi.py"), key=lambda p: len(p.parts)):
        if rel.parent != Path() and "application" in (root / rel).read_text(errors="ignore"):
            return ".".join(rel.with_suffix("").parts) + ":application"
    return ""


def settings_module(root: Path) -> str:
    try:
        text = (root / "manage.py").read_text(errors="ignore")
    except OSError:
        return ""
    found = re.search(r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([\w.]+)['\"]", text)
    return found.group(1) if found else ""


def python_version(root: Path, allowed: list[str]) -> str:
    candidates = []
    with contextlib.suppress(OSError):
        candidates.append((root / ".python-version").read_text().strip())
    try:
        found = re.search(r"requires-python\s*=\s*[\"'][><=~! ]*(\d+\.\d+)", (root / "pyproject.toml").read_text())
        if found:
            candidates.append(found.group(1))
    except OSError:
        pass
    for candidate in candidates:
        major_minor = ".".join(candidate.split(".")[:2])
        if major_minor in allowed:
            return major_minor
    return ""


def detect(root: Path, schema: dict) -> tuple[dict, list[str]]:
    """Returns (build settings to write, human-readable notes about what was found)."""
    fields = {f["name"]: f for f in schema["fields"]}
    build: dict = {}
    notes: list[str] = []
    if wsgi := wsgi_module(root):
        build["wsgi_module"] = wsgi
        notes.append(f"wsgi_module = {wsgi}")
    if settings := settings_module(root):
        build["django_settings_module"] = settings
        notes.append(f"django_settings_module = {settings}")
    if (root / "uv.lock").is_file() and (root / "pyproject.toml").is_file():
        build["package_manager"] = "uv"
        notes.append("package_manager = uv (found uv.lock)")
    elif (root / "requirements.txt").is_file():
        notes.append("package_manager = pip (found requirements.txt)")
    if version := python_version(root, fields["python_version"]["choices"]):
        build["python_version"] = version
        notes.append(f"python_version = {version}")
    return build, notes


def missing_dependencies_file(root: Path) -> bool:
    return not ((root / "requirements.txt").is_file() or (root / "uv.lock").is_file())
