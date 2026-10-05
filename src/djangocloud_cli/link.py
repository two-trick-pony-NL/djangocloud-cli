"""The link between a folder and a DjangoCloud project: `.djangocloud/config.json`.

Written by the CLI, holds no secrets, and is git-ignored by a `.gitignore` inside the folder, so it
can't be committed by accident. The API token lives in the user's config directory, not here.
"""

import json
from pathlib import Path

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


def save(root: Path, project: dict, api: str) -> Path:
    folder = root / DIR
    folder.mkdir(exist_ok=True)
    (folder / ".gitignore").write_text("*\n")
    path = folder / FILE
    path.write_text(json.dumps({"project_id": project["id"], "project": project["slug"], "api": api}, indent=2) + "\n")
    return path


def remove(root: Path) -> bool:
    path = root / DIR / FILE
    if not path.exists():
        return False
    path.unlink()
    return True
