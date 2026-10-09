"""`djangocloud new <name>`: a project made by Django's own `django-admin startproject`, with only the database
settings changed.

Nothing is bundled and nothing needs updating when Django ships a new LTS:

1. the newest stable LTS series (the X.2 releases: 4.2, 5.2, 6.2, ...) is looked up on PyPI,
2. that Django is installed on demand, away from your environment (`uvx`, or a throwaway virtual environment), so it
   never changes what is installed in your project,
3. the real `django-admin startproject` runs,
4. the DATABASES block of the generated settings.py is replaced: SQLite on your computer, and the Postgres database
   DjangoCloud creates for you as soon as the app runs there (DjangoCloud adds the connection details to the
   deployment's environment when you select and connect a database),
5. a short README.md is written, and an empty `.env` (a comment on sending it with `djangocloud env push .env`), and
6. `djangocloud_cli` is added to INSTALLED_APPS (so `python manage.py djangocloud <command>` works), and
   `djangocloud-cli` to requirements.txt (the app has to be installed wherever the project runs).

Nothing else is touched; DjangoCloud adds what a deployed app needs (static files, allowed hosts, the Postgres
driver) itself when it builds the image.
"""

from __future__ import annotations

import json
import keyword
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import link

PYPI_DJANGO = "https://pypi.org/pypi/django/json"
LTS_RELEASE = re.compile(r"^(\d+)\.2\.(\d+)$")  # 4.2.x, 5.2.x ...: every X.2 series is a long-term-support release


# Django refuses a project name that is also an importable module; these are the ones it would always trip over (the
# stdlib names are checked separately, and `test` is a package Django's own test runner imports).
RESERVED_NAMES = {"django", "test", "tests"}


class ScaffoldError(Exception):
    """The message is safe to show the person who asked."""


def in_project(start: Path | None = None) -> Path | None:
    """The project you are inside (a linked folder, or one with manage.py), or None."""
    root = link.find_root(start)
    return root if (root / link.DIR / link.FILE).is_file() or (root / "manage.py").is_file() else None


def package_name(name: str) -> str:
    """The Python package for a project called `name` ('my-shop' -> 'my_shop'), or raise ScaffoldError."""
    package = name.replace("-", "_")
    if not name or not name.isascii() or not all(c.isalnum() or c in "-_" for c in name):
        raise ScaffoldError("Use letters, digits, '-' and '_' only, for example: djangocloud new my-shop")
    if not package.isidentifier():
        raise ScaffoldError(f"{name!r} can't be a Python package name (it must not start with a digit).")
    if keyword.iskeyword(package) or package in sys.stdlib_module_names or package in RESERVED_NAMES:
        raise ScaffoldError(f"{name!r} clashes with a Python or Django module name. Pick another one.")
    return package


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - fixed https URL
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ScaffoldError(f"Couldn't look up the latest Django LTS on PyPI ({exc}). Check your connection.") from None


def latest_lts(releases: dict | None = None) -> str:
    """The newest stable LTS series, like '5.2'. Pre-releases and fully yanked releases are ignored."""
    if releases is None:
        releases = fetch_json(PYPI_DJANGO)["releases"]
    found = []
    for version, files in releases.items():
        match = LTS_RELEASE.match(version)
        if match and files and not all(f.get("yanked") for f in files):
            found.append((int(match.group(1)), int(match.group(2)), version))
    if not found:
        raise ScaffoldError("Couldn't find a Django LTS release on PyPI.")
    major = max(found)[0]
    return f"{major}.2"


def requirement(series: str) -> str:
    return f"Django~={series}.0"


def generate(series: str, package: str, folder: Path) -> None:
    """Run the real `django-admin startproject`, with Django installed just for this (never in your environment)."""
    spec = requirement(series)
    try:
        if shutil.which("uvx"):
            command = ["uvx", "--quiet", "--from", spec, "django-admin", "startproject", package, str(folder)]
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=300, cwd=folder.parent)  # noqa: S603
            return
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True, timeout=300)  # noqa: S603
            bin_dir = venv / ("Scripts" if sys.platform == "win32" else "bin")
            subprocess.run(  # noqa: S603
                [str(bin_dir / "python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", spec],
                check=True, capture_output=True, text=True, timeout=600,
            )  # fmt: skip
            subprocess.run(  # noqa: S603
                [str(bin_dir / "django-admin"), "startproject", package, str(folder)],
                check=True, capture_output=True, text=True, timeout=300, cwd=folder.parent,
            )  # fmt: skip
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()[-1:] or [str(exc)]
        raise ScaffoldError(f"Couldn't create the Django {series} project: {detail[0]}") from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScaffoldError(f"Couldn't create the Django {series} project: {exc}") from None


# The only part of the generated project that is changed.
DATABASES_BLOCK = """# On your computer: a local SQLite file, so manage.py works without any setup.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# On DjangoCloud: these variables only exist there, so only then do we switch to the Postgres database.
if "DJANGOCLOUD_HOSTED_DB_NAME" in os.environ:
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DJANGOCLOUD_HOSTED_DB_NAME"],
        "USER": os.environ["DJANGOCLOUD_HOSTED_DB_USER"],
        "PASSWORD": os.environ["DJANGOCLOUD_HOSTED_DB_PASSWORD"],
        "HOST": os.environ["DJANGOCLOUD_HOSTED_DB_HOST"],
        "PORT": os.environ["DJANGOCLOUD_HOSTED_DB_PORT"],
        "CONN_MAX_AGE": 600,
        "OPTIONS": {"sslmode": "require"},
    }
"""

_STOCK_DATABASES = re.compile(r"^DATABASES = \{\n.*?\n\}\n", re.DOTALL | re.MULTILINE)
_PATHLIB_IMPORT = re.compile(r"^from pathlib import Path\n", re.MULTILINE)


def patch_settings(text: str) -> str:
    """Replace the stock DATABASES block (and add `import os`). Raises if Django's settings no longer look like that,
    rather than writing a project that is not wired to the database."""
    if not _STOCK_DATABASES.search(text) or "BASE_DIR" not in text:
        raise ScaffoldError(
            "The settings.py Django generated has a different DATABASES block than expected, so it was left as "
            "it is. Add the database settings from the DjangoCloud docs (Databases) by hand."
        )
    if not re.search(r"^import os$", text, re.MULTILINE):
        text = (
            _PATHLIB_IMPORT.sub("import os\nfrom pathlib import Path\n", text, count=1)
            if _PATHLIB_IMPORT.search(text)
            else "import os\n" + text
        )
    patched = _STOCK_DATABASES.sub(lambda _: DATABASES_BLOCK, text, count=1)
    compile(patched, "settings.py", "exec")
    return patched


_INSTALLED_APPS = re.compile(r"^INSTALLED_APPS = \[\n.*?\n\]\n", re.DOTALL | re.MULTILINE)
APP_NAME = "djangocloud_cli"


def add_installed_app(text: str) -> str:
    """Add our app to INSTALLED_APPS, at the end. Left alone if it is there already or the list is not where it
    always is (the project still works; only `manage.py djangocloud` would be missing)."""
    found = _INSTALLED_APPS.search(text)
    if not found or f'"{APP_NAME}"' in found.group(0):
        return text
    block = found.group(0)
    return text.replace(block, block[: -len("]\n")] + f'    "{APP_NAME}",\n]\n', 1)


def cli_requirement() -> str:
    """The requirements.txt line for this CLI. Deliberately unpinned: the project only needs the app to import, and
    a version floor at the version that made it can fail for minutes after a release, while PyPI's mirrors catch up."""
    return "djangocloud-cli"


ENV_FILE = """# Environment variables for this project. Kept out of git (see .gitignore), never uploaded with your code.
# Add KEY=value lines below, then send them to DjangoCloud with:
#
#     djangocloud env push .env
#
# They reach your app from the next deploy. Values are stored encrypted and never shown again.
"""


DOCS_URL = "https://django-cloud.gitbook.io/django-cloud-docs/"

PROJECT_README = """# {name}

A Django {series} (LTS) project, ready to deploy with [DjangoCloud](https://djangocloud.dev).

## Run it on your computer

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Locally the project uses a SQLite file (`db.sqlite3`), so there is nothing else to set up.

## Deploy it

```bash
djangocloud deploy
```

The first time, the CLI signs you in and links this folder to a project. After that it is the same command every
time. If you want your tests to run before every deploy, switch that on with `djangocloud tests on`.

## Environment variables

Put `KEY=value` lines in `.env` (it is kept out of git and never uploaded with your code), then send them:

```bash
djangocloud env push .env
```

They reach your app from the next deploy.

## Database

In the DjangoCloud dashboard, select and connect a database for this project. DjangoCloud then gives your app its
connection details, and `{package}/settings.py` switches from SQLite to that Postgres database by itself. Nothing to
copy by hand.

## Handy commands

```bash
djangocloud status        # is it live, and the latest releases
djangocloud logs -f       # follow the logs
djangocloud scale         # change the server size or the number of instances
djangocloud metrics       # CPU and memory
djangocloud help          # everything else
```

Documentation: {docs}
"""


def _undo(folder: Path, created: bool) -> None:
    """Remove what a failed run wrote: the whole folder if we made it, else just what is in it (it was empty)."""
    if created:
        shutil.rmtree(folder, ignore_errors=True)
        return
    for child in folder.iterdir():
        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)


def create(parent: Path, name: str, *, series: str | None = None) -> tuple[Path, str]:
    """Create parent/name. Returns (folder, Django series). Refuses a folder that already has something in it."""
    package = package_name(name)
    folder = parent / name
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ScaffoldError(f"{name!r} already exists and isn't empty. Pick another name, or remove it first.")
    series = series or latest_lts()
    created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)  # django-admin wants the folder to exist already
    try:
        generate(series, package, folder)
        settings = folder / package / "settings.py"
        if not settings.is_file():
            raise ScaffoldError("Django did not create the expected settings.py.")
        settings.write_text(add_installed_app(patch_settings(settings.read_text())))
        (folder / "requirements.txt").write_text(f"{requirement(series)}\n{cli_requirement()}\n")
        (folder / ".env").write_text(ENV_FILE)
        (folder / "README.md").write_text(
            PROJECT_README.format(name=name, series=series, package=package, docs=DOCS_URL)
        )
        (folder / ".gitignore").write_text("__pycache__/\n*.pyc\n.venv/\ndb.sqlite3\n.env\nstaticfiles/\n")
    except BaseException:
        _undo(folder, created)  # a failed run leaves nothing behind
        raise
    return folder, series
