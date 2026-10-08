"""The link between a folder and a DjangoCloud project: `.djangocloud/config.json`.

Written by the CLI and meant to be committed: it holds the project, the API address and the build settings, and no
secrets, so a CI checkout (`djangocloud --no-input deploy`) knows which project it deploys to. The API token lives in
the user's config directory, not here.
"""

import json
from pathlib import Path

LEGACY_GITIGNORE = "*\n"  # what CLI versions up to 0.1.12 wrote into the folder

DIR = ".djangocloud"
FILE = "config.json"


def find_root(start: Path | None = None) -> Path:
    """The nearest folder already linked, else the one holding manage.py, else where we are."""
    start = (start or Path.cwd()).resolve()
    for folder in (start, *start.parents):
        if (folder / DIR / FILE).is_file():
            return folder
    for folder in (start, *start.parents):
        if (folder / "manage.py").is_file():
            return folder
    return start


def load(root: Path) -> dict | None:
    try:
        data = json.loads((root / DIR / FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("project"):
        return None
    # Same shape the API returns for a project, so callers treat both alike.
    return {"id": data.get("project_id"), "slug": data["project"], "api": data.get("api", "")}


def _read_raw(root: Path) -> dict:
    try:
        data = json.loads((root / DIR / FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_build(root: Path) -> dict | None:
    """The "build" settings block, or None when there isn't one yet."""
    build = _read_raw(root).get("build")
    return build if isinstance(build, dict) else None


def save_build(root: Path, build: dict) -> Path:
    """Write the build settings, keeping everything else in the file as it is."""
    data = _read_raw(root)
    data["build"] = build
    path = root / DIR / FILE
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def save(root: Path, project: dict, api: str) -> Path:
    folder = root / DIR
    folder.mkdir(exist_ok=True)
    legacy = folder / ".gitignore"
    if (
        legacy.is_file() and legacy.read_text() == LEGACY_GITIGNORE
    ):  # older versions hid the folder; ours, so ours to remove
        legacy.unlink()
    path = folder / FILE
    data = {"project_id": project["id"], "project": project["slug"], "api": api}
    old = _read_raw(root)
    if old.get("project") == project["slug"] and isinstance(old.get("build"), dict):
        data["build"] = old["build"]  # relinking the same project must not wipe the build settings
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def remove(root: Path) -> bool:
    path = root / DIR / FILE
    if not path.exists():
        return False
    path.unlink()
    return True
