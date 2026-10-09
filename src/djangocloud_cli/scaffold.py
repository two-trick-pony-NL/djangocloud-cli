"""`djangocloud new <name>`: a stock Django project, with only the database settings changed.

The files are what `django-admin startproject` writes for the current LTS release, bundled here so the CLI needs
neither Django nor a network connection to create a project. The one difference is the DATABASES block: SQLite on
your computer, and the Postgres database DjangoCloud creates for you as soon as the app runs there (it adds the
connection details to the deployment's environment). Nothing else in the project is touched; DjangoCloud adds what
a deployed app needs (static files, allowed hosts, the Postgres driver) itself when it builds the image.

To move to a newer LTS: change DJANGO_LTS, and compare the templates below with `django-admin startproject` output.
"""

from __future__ import annotations

import keyword
import secrets
import sys
from pathlib import Path

DJANGO_LTS = "5.2"  # the latest long-term-support release; 4.2 and 5.2 are LTS, the next is 6.2

SECRET_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)"  # noqa: S105 - the characters a key is drawn from, not a secret


class ScaffoldError(Exception):
    """The message is safe to show the person who asked."""


def package_name(name: str) -> str:
    """The Python package for a project called `name` ('my-shop' -> 'my_shop'), or raise ScaffoldError."""
    package = name.replace("-", "_")
    if not name or not all(c.isalnum() or c in "-_" for c in name) or not name.isascii():
        raise ScaffoldError("Use letters, digits, '-' and '_' only, for example: djangocloud new my-shop")
    if not package.isidentifier():
        raise ScaffoldError(f"{name!r} can't be a Python package name (it must not start with a digit).")
    if keyword.iskeyword(package) or package in sys.stdlib_module_names or package == "django":
        raise ScaffoldError(f"{name!r} clashes with a Python or Django module name. Pick another one.")
    return package


MANAGE_PY = '''#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{name}.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
'''

ASGI_PY = '''"""
ASGI config for {name} project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/{lts}/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{name}.settings")

application = get_asgi_application()
'''

WSGI_PY = '''"""
WSGI config for {name} project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/{lts}/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{name}.settings")

application = get_wsgi_application()
'''

URLS_PY = '''"""
URL configuration for {name} project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/{lts}/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
'''

# The DATABASES block is the only part that differs from `django-admin startproject`.
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

SETTINGS_PY = '''"""
Django settings for {name} project.

Generated by 'djangocloud new' using Django {lts}.

For more information on this file, see
https://docs.djangoproject.com/en/{lts}/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/{lts}/ref/settings/
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/{lts}/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "{secret}"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "{name}.urls"

TEMPLATES = [
    {{
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {{
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        }},
    }},
]

WSGI_APPLICATION = "{name}.wsgi.application"


# Database
# https://docs.djangoproject.com/en/{lts}/ref/settings/#databases

{databases}

# Password validation
# https://docs.djangoproject.com/en/{lts}/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {{
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    }},
    {{
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    }},
    {{
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    }},
    {{
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    }},
]


# Internationalization
# https://docs.djangoproject.com/en/{lts}/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/{lts}/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/{lts}/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
'''

GITIGNORE = """__pycache__/
*.pyc
.venv/
db.sqlite3
.env
staticfiles/
"""


def files_for(name: str) -> dict[str, str]:
    """Every file of the new project, by path relative to the project folder."""
    package = package_name(name)
    secret = "django-insecure-" + "".join(secrets.choice(SECRET_ALPHABET) for _ in range(50))
    fill = {"name": package, "lts": DJANGO_LTS}
    return {
        "manage.py": MANAGE_PY.format(**fill),
        f"{package}/__init__.py": "",
        f"{package}/asgi.py": ASGI_PY.format(**fill),
        f"{package}/wsgi.py": WSGI_PY.format(**fill),
        f"{package}/urls.py": URLS_PY.format(**fill),
        f"{package}/settings.py": SETTINGS_PY.format(secret=secret, databases=DATABASES_BLOCK, **fill),
        "requirements.txt": f"Django~={DJANGO_LTS}.0\n",
        ".gitignore": GITIGNORE,
    }


def create(parent: Path, name: str) -> Path:
    """Write the project into parent/name. Refuses a folder that already has something in it."""
    files = files_for(name)
    folder = parent / name
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ScaffoldError(f"{name!r} already exists and isn't empty. Pick another name, or remove it first.")
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (folder / "manage.py").chmod(0o755)
    return folder
