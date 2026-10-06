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


def asgi_module(root: Path) -> str:
    """e.g. config.asgi:application, from the shallowest asgi.py that defines `application`."""
    for rel in sorted(_walk(root, "asgi.py"), key=lambda p: len(p.parts)):
        if rel.parent != Path() and "application" in (root / rel).read_text(errors="ignore"):
            return ".".join(rel.with_suffix("").parts) + ":application"
    return ""


_PLAIN_WSGI = re.compile(r"^\s*application\s*=\s*get_wsgi_application\(\)\s*$", re.MULTILINE)
_ANY_APPLICATION = re.compile(r"^\s*application\s*=", re.MULTILINE)


def wsgi_wraps_the_app(root: Path, module: str) -> bool:
    """True when wsgi.py does more than `application = get_wsgi_application()` (WhiteNoise, Sentry, gevent...).
    Running such a project under ASGI would silently skip that wrapper, so it keeps gunicorn."""
    path = root / (module.split(":")[0].replace(".", "/") + ".py")
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    assignments = _ANY_APPLICATION.findall(text)
    return len(assignments) != len(_PLAIN_WSGI.findall(text))


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
    wsgi, asgi = wsgi_module(root), asgi_module(root) if "asgi_module" in fields else ""
    if wsgi:
        build["wsgi_module"] = wsgi
        notes.append(f"wsgi_module = {wsgi}")
    if asgi:
        build["asgi_module"] = asgi
        if wsgi and wsgi_wraps_the_app(root, wsgi) and "server" in fields:
            build["server"] = "gunicorn"
            notes.append(f"asgi_module = {asgi}")
            notes.append("server = gunicorn (your wsgi.py wraps the app; set server to uvicorn to use ASGI instead)")
        else:
            notes.append(f"asgi_module = {asgi} (started with uvicorn)")
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
