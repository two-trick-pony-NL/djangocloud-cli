"""Setting up the admin user: ask the project's own Django which fields its user model needs, so a custom user model
(email as the username, extra required fields) works as well as the default one.

Django's `createsuperuser --noinput` reads one variable per field: DJANGO_SUPERUSER_<FIELD_NAME_UPPERCASE> for the
user model's USERNAME_FIELD and each of its REQUIRED_FIELDS, plus DJANGO_SUPERUSER_PASSWORD. DjangoCloud stores them
as encrypted environment variables, and the `create_superuser` build setting makes the next release's start command
run `createsuperuser --noinput` after the migrations. The password variable is removed again once the deploy is live.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "DJANGO_SUPERUSER_"
PASSWORD_VAR = PREFIX + "PASSWORD"

# Run inside the project's own environment, in its folder. Prints one JSON line.
PROBE = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
settings = sys.argv[1]
if settings:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings)
import django
django.setup()
from django.contrib.auth import get_user_model
model = get_user_model()
def field(name):
    f = model._meta.get_field(name)
    kind = "m2m" if getattr(f, "many_to_many", False) else "fk" if getattr(f, "is_relation", False) else "plain"
    return {"name": name, "kind": kind, "label": str(getattr(f, "verbose_name", name))}
print(json.dumps({
    "model": model._meta.label,
    "username": field(model.USERNAME_FIELD),
    "required": [field(n) for n in model.REQUIRED_FIELDS],
}))
"""


@dataclass
class UserModel:
    label: str = "auth.User"
    username: dict = field(default_factory=lambda: {"name": "username", "kind": "plain", "label": "username"})
    required: list = field(default_factory=lambda: [{"name": "email", "kind": "plain", "label": "email address"}])
    guessed: bool = False  # True when the project could not be asked and Django's defaults are assumed

    @property
    def fields(self) -> list[dict]:
        return [self.username, *self.required]

    @property
    def unsupported(self) -> list[dict]:
        """Fields `createsuperuser --noinput` can't be given a value for in an environment variable."""
        return [f for f in self.fields if f["kind"] != "plain"]


def variable_name(field_name: str) -> str:
    return PREFIX + field_name.upper()


def python_command(root: Path) -> list[str]:
    """How to run Python inside the project's own environment."""
    for candidate in (root / ".venv" / "bin" / "python", root / ".venv" / "Scripts" / "python.exe"):
        if candidate.is_file():
            return [str(candidate)]
    if (root / "uv.lock").is_file() and shutil.which("uv"):
        return ["uv", "run", "python"]
    if (root / "poetry.lock").is_file() and shutil.which("poetry"):
        return ["poetry", "run", "python"]
    return [shutil.which("python") or shutil.which("python3") or sys.executable]


def discover(root: Path, settings_module: str = "") -> UserModel:
    """Ask the project which fields its user model needs. Falls back to Django's defaults (and says so)."""
    try:
        done = subprocess.run(  # noqa: S603 - the project's own interpreter, a fixed script
            [*python_command(root), "-c", PROBE, settings_module],
            cwd=root, capture_output=True, text=True, timeout=120, check=True,
        )  # fmt: skip
        data = json.loads(done.stdout.strip().splitlines()[-1])
        return UserModel(label=data["model"], username=data["username"], required=data["required"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return UserModel(guessed=True)
